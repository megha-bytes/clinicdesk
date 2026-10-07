"""Agent tools (plan §7). The only way the agent can read or change clinic data.

Rules every tool follows
- Typed input (pydantic). Bad arguments → {"ok": false, "error": "INVALID_ARGUMENTS"}; never an exception.
- Every booking decision goes through the deterministic engine; the model can't invent slots,
  lengths, fees or token numbers. Engine refusals come back as stable error codes.
- Privacy: a tool only ever returns the current patient's data. Lookups that don't match return
  the same NOT_FOUND message whether or not the phone number exists.
- Every state change is written to the audit log.

`run_tool(name, args, ctx)` is the single entry point used by the LangGraph `tools` node.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import engine as eng
from app import models as m
from app import repo
from app.payments.base import get_provider

log = logging.getLogger(__name__)
UTC = timezone.utc

WINDOWS: dict[str, tuple[time, time]] = {
    "morning": (time(6, 0), time(12, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(22, 0)),
}
TIMED_LEAD_MIN = 15          # don't offer timed slots starting in the next 15 minutes
MAX_SEARCH_DAYS = 7


# ----------------------------------------------------------------------------- context & results
@dataclass
class ToolContext:
    session: Session
    clinic_id: str
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    channel: Literal["web", "voice", "a2a", "staff"] = "web"
    conversation_id: str | None = None
    a2a_caller_id: str | None = None
    _clinic: m.Clinic | None = None

    @property
    def clinic(self) -> m.Clinic:
        if self._clinic is None:
            self._clinic = self.session.get(m.Clinic, self.clinic_id)
            if self._clinic is None:
                raise LookupError("clinic not found")
        return self._clinic

    @property
    def tz(self) -> ZoneInfo:
        return repo.clinic_tz(self.clinic)

    @property
    def local_now(self) -> datetime:
        return self.now.astimezone(self.tz)


class ToolFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def ok(**data: Any) -> dict:
    return {"ok": True, **data}


def fail(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}


# ----------------------------------------------------------------------------- helpers
def label(dt: datetime, tz: ZoneInfo) -> str:
    """'Mon 12 Oct, 10:00 AM' in clinic time: what the agent reads back to patients."""
    return dt.astimezone(tz).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


def iso(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).isoformat(timespec="minutes")


def normalise_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ToolFailure("INVALID_PHONE", "Please share a 10-digit Indian mobile number.")
    return digits


def parse_day(value: str, ctx: ToolContext) -> date:
    v = (value or "").strip().lower()
    today = ctx.local_now.date()
    if v in ("", "today"):
        return today
    if v == "tomorrow":
        return today + timedelta(days=1)
    if v in ("day after tomorrow", "day_after_tomorrow"):
        return today + timedelta(days=2)
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise ToolFailure("INVALID_ARGUMENTS", "Dates must be 'today', 'tomorrow' or YYYY-MM-DD.") from e


def parse_start(value: str, ctx: ToolContext) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise ToolFailure("INVALID_ARGUMENTS", "Start time must be an ISO datetime from find_slots.") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=ctx.tz)


def get_type(ctx: ToolContext, name: str) -> m.AppointmentType:
    row = ctx.session.scalar(select(m.AppointmentType).where(
        m.AppointmentType.clinic_id == ctx.clinic_id, m.AppointmentType.name == name))
    if row is None:
        names = ctx.session.scalars(select(m.AppointmentType.name).where(
            m.AppointmentType.clinic_id == ctx.clinic_id)).all()
        raise ToolFailure("UNKNOWN_TYPE", f"Appointment type must be one of: {', '.join(names)}.")
    return row


def get_doctor(ctx: ToolContext, doctor_id: str) -> m.Doctor:
    row = ctx.session.get(m.Doctor, doctor_id)
    if row is None or row.clinic_id != ctx.clinic_id:
        raise ToolFailure("UNKNOWN_DOCTOR", "No such doctor at this clinic.")
    return row


def get_patient(ctx: ToolContext, patient_id: str) -> m.Patient:
    row = ctx.session.get(m.Patient, patient_id)
    if row is None or row.clinic_id != ctx.clinic_id:
        raise ToolFailure("NOT_FOUND", "Patient not found. Please verify phone number and name first.")
    return row


def own_appointment(ctx: ToolContext, appointment_id: str, patient_id: str) -> m.Appointment:
    """The appointment, only if it belongs to this patient. Same message otherwise (no probing)."""
    row = ctx.session.get(m.Appointment, appointment_id)
    if row is None or row.clinic_id != ctx.clinic_id or row.patient_id != patient_id:
        raise ToolFailure("NOT_FOUND", "No matching appointment for this patient.")
    return row


def audit(ctx: ToolContext, action: str, entity: str, entity_id: str | None,
          before: dict | None = None, after: dict | None = None) -> None:
    actor = "a2a" if ctx.channel == "a2a" else ("staff" if ctx.channel == "staff" else "agent")
    ctx.session.add(m.AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id,
                               before=before, after=after))


def appt_summary(ctx: ToolContext, row: m.Appointment) -> dict:
    doctor = ctx.session.get(m.Doctor, row.doctor_id)
    typ = ctx.session.get(m.AppointmentType, row.type_id)
    tz = ctx.tz
    start = repo._aware(row.start, tz)
    style = repo.booking_style(doctor, ctx.clinic)
    out = {
        "appointment_id": row.id, "doctor_id": doctor.id, "doctor": doctor.name,
        "appointment_type": typ.name, "fee_inr": typ.fee_inr, "status": row.status,
        "booking_style": style, "start": iso(start, tz), "when": label(start, tz),
    }
    if style == "token":
        out["token_no"] = row.token_no
        out["when"] = f"{start.astimezone(tz).strftime('%a %d %b').replace(' 0', ' ')}, token {row.token_no}" \
            if row.token_no else out["when"]
    return out


def _day_bookings(ctx: ToolContext, doctor_id: str, day: date):
    return repo.load_bookings(ctx.session, [doctor_id], day, day, ctx.tz)


# ----------------------------------------------------------------------------- tool inputs
class NoArgs(BaseModel):
    pass


class RouteToDoctorIn(BaseModel):
    need: str = Field(..., max_length=200, description="What the patient needs, in their words (e.g. 'skin rash', 'child fever').")


class FindSlotsIn(BaseModel):
    appointment_type: str = Field(..., description="'new', 'follow_up' or a 'procedure:<name>' type from get_clinic_info.")
    doctor_id: str | None = Field(None, description="Specific doctor, if the patient asked for one.")
    specialty: str | None = Field(None, description="Specialty from route_to_doctor, if no doctor was named.")
    date: str = Field("today", description="'today', 'tomorrow' or YYYY-MM-DD (first day to search).")
    days: int = Field(1, ge=1, le=MAX_SEARCH_DAYS, description="How many days to search from `date`.")
    window: Literal["morning", "afternoon", "evening", "any"] = "any"
    preferred_time: str | None = Field(None, description="HH:MM the patient asked for, if any.")
    language: str | None = Field(None, description="Patient's language code (en, hi, kn...).")
    previous_doctor_id: str | None = Field(None, description="Doctor seen last time (follow-ups).")

    @field_validator("preferred_time")
    @classmethod
    def _hhmm(cls, v):
        if v is not None:
            time.fromisoformat(v)
        return v


class HoldSlotIn(BaseModel):
    doctor_id: str
    start: str = Field(..., description="Exact `start` value returned by find_slots.")
    appointment_type: str
    patient_id: str | None = None


class RegisterPatientIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    age: int = Field(..., ge=0, le=120)
    phone: str
    language: str = Field("en", max_length=10)
    consent: bool = Field(..., description="True only if the patient explicitly agreed to the privacy notice.")


class FindPatientIn(BaseModel):
    phone: str
    name: str = Field(..., min_length=2, max_length=100)


class BookIn(BaseModel):
    hold_id: str
    patient_id: str
    reason_short: str = Field("", max_length=300, description="Short reason in the patient's words (e.g. 'fever 3 days'). No medical history.")


class RescheduleIn(BaseModel):
    appointment_id: str
    patient_id: str
    new_start: str = Field(..., description="Exact `start` value returned by find_slots.")


class CancelIn(BaseModel):
    appointment_id: str
    patient_id: str


class AppointmentIn(BaseModel):
    appointment_id: str


class QueueStatusIn(BaseModel):
    appointment_id: str
    patient_id: str


class JoinWaitlistIn(BaseModel):
    patient_id: str
    doctor_id: str
    appointment_type: str
    date: str = Field(..., description="'today', 'tomorrow' or YYYY-MM-DD")
    window: Literal["morning", "afternoon", "evening", "any"] = "any"


class HandoffIn(BaseModel):
    reason: str = Field(..., max_length=200)
    summary: str = Field("", max_length=1000)


class EmergencyIn(BaseModel):
    summary: str = Field("", max_length=1000)


# ----------------------------------------------------------------------------- tools
def get_clinic_info(ctx: ToolContext, _: NoArgs) -> dict:
    c = ctx.clinic
    doctors = ctx.session.scalars(select(m.Doctor).where(m.Doctor.clinic_id == c.id).order_by(m.Doctor.name)).all()
    types = ctx.session.scalars(select(m.AppointmentType).where(m.AppointmentType.clinic_id == c.id)
                                .order_by(m.AppointmentType.minutes)).all()
    return ok(
        clinic=c.name, address=c.address, languages=c.languages, emergency_numbers=c.emergency_numbers,
        appointment_types=[{"name": t.name, "minutes": t.minutes, "fee_inr": t.fee_inr} for t in types],
        doctors=[{"doctor_id": d.id, "name": d.name, "specialty": d.specialty, "languages": d.languages,
                  "hours": d.hours, "booking_style": repo.booking_style(d, c),
                  "on_leave": sorted(x for x in (d.leave or []) if x >= ctx.local_now.date().isoformat())}
                 for d in doctors],
    )


def route_to_doctor(ctx: ToolContext, args: RouteToDoctorIn) -> dict:
    """Clinic-defined keyword table only. The model never decides who treats what (§3.1)."""
    need = args.need.casefold()
    rules = ctx.session.scalars(select(m.RoutingRule).where(m.RoutingRule.clinic_id == ctx.clinic_id)).all()
    hits = [r for r in rules if r.keyword.casefold() in need]
    specialty = None
    if hits:
        best = max(hits, key=lambda r: (r.priority or 0, len(r.keyword)))   # clinic priority, then most specific
        specialty = best.specialty or (ctx.session.get(m.Doctor, best.doctor_id).specialty if best.doctor_id else None)
    matched = specialty is not None
    if not matched:
        specialty = "General Physician"
    doctors = ctx.session.scalars(select(m.Doctor).where(
        m.Doctor.clinic_id == ctx.clinic_id, func.lower(m.Doctor.specialty) == specialty.lower())).all()
    return ok(specialty=specialty, matched_rule=matched,
              note=None if matched else "No routing rule matched; defaulting to a general physician.",
              doctors=[{"doctor_id": d.id, "name": d.name, "booking_style": repo.booking_style(d, ctx.clinic)}
                       for d in doctors])


def find_slots(ctx: ToolContext, args: FindSlotsIn) -> dict:
    typ = repo.to_engine_type(get_type(ctx, args.appointment_type))
    first = parse_day(args.date, ctx)
    if first < ctx.local_now.date():
        raise ToolFailure("IN_PAST", "That date has already passed.")
    last = first + timedelta(days=args.days - 1)

    q = select(m.Doctor).where(m.Doctor.clinic_id == ctx.clinic_id)
    if args.doctor_id:
        q = q.where(m.Doctor.id == get_doctor(ctx, args.doctor_id).id)
    elif args.specialty:
        q = q.where(func.lower(m.Doctor.specialty) == args.specialty.lower())
    doctor_rows = ctx.session.scalars(q).all()
    if not doctor_rows:
        raise ToolFailure("UNKNOWN_DOCTOR", "No doctor matches that request.")

    tz, now = ctx.tz, ctx.now
    bookings, _ = repo.load_bookings(ctx.session, [d.id for d in doctor_rows], first, last, tz)
    window = WINDOWS.get(args.window)
    preferred = None
    if args.preferred_time:
        preferred = datetime.combine(first, time.fromisoformat(args.preferred_time), tzinfo=tz)

    offers: list[dict] = []
    timed_candidates: list[tuple[eng.Doctor, eng.Interval]] = []
    day = first
    while day <= last:
        for row in doctor_rows:
            doc = repo.to_engine_doctor(row)
            if repo.booking_style(row, ctx.clinic) == "token":
                slots = [s for s in eng.free_slots(doc, day, typ.minutes, bookings, now, tz, step_min=5)
                         if not window or window[0] <= s.start.astimezone(tz).time() < window[1]]
                if slots:
                    s = slots[0]
                    ahead = len([b for b in eng.queue_order(bookings, doc.id, day, tz) if b.start < s.start])
                    offers.append({
                        "doctor_id": doc.id, "doctor": doc.name, "booking_style": "token",
                        "start": iso(s.start, tz), "date": day.isoformat(),
                        "next_token": eng.next_token_no(bookings, doc.id, day, tz),
                        "patients_ahead": ahead, "estimated_time": label(s.start, tz),
                        "fee_inr": typ.fee_inr,
                    })
            else:
                for s in eng.free_slots(doc, day, typ.minutes, bookings, now, tz, lead_min=TIMED_LEAD_MIN):
                    timed_candidates.append((doc, s))
        day += timedelta(days=1)

    prefs = eng.Preferences(preferred_start=preferred, window=window, preferred_doctor_id=args.doctor_id,
                            previous_doctor_id=args.previous_doctor_id, language=args.language)
    for r in eng.rank_slots(timed_candidates, bookings, prefs, now, tz, k=3):
        name = next(d.name for d, _ in timed_candidates if d.id == r.doctor_id)
        offers.append({"doctor_id": r.doctor_id, "doctor": name, "booking_style": "timed",
                       "start": iso(r.slot.start, tz), "when": label(r.slot.start, tz),
                       "minutes": typ.minutes, "fee_inr": typ.fee_inr, "why": r.reasons})
    if not offers:
        return ok(slots=[], suggestion="Nothing free in that range. Offer another day, another doctor, or join_waitlist.")
    return ok(slots=offers, appointment_type=typ.name)


def hold_slot(ctx: ToolContext, args: HoldSlotIn) -> dict:
    doctor = get_doctor(ctx, args.doctor_id)
    typ_row = get_type(ctx, args.appointment_type)
    if args.patient_id:
        get_patient(ctx, args.patient_id)
    start = parse_start(args.start, ctx)
    bookings, _ = _day_bookings(ctx, doctor.id, start.astimezone(ctx.tz).date())
    held = eng.hold(repo.to_engine_doctor(doctor), start, repo.to_engine_type(typ_row), bookings,
                    ctx.now, ctx.tz, patient_id=args.patient_id)
    row = repo.new_row_from(held, ctx.clinic_id, typ_row.id, ctx.channel if ctx.channel != "staff" else "staff")
    row.a2a_caller_id = ctx.a2a_caller_id
    ctx.session.add(row)
    audit(ctx, "hold", "appointment", row.id, after={"start": iso(held.start, ctx.tz), "doctor_id": doctor.id})
    return ok(hold_id=row.id, doctor=doctor.name, when=label(held.start, ctx.tz), start=iso(held.start, ctx.tz),
              minutes=typ_row.minutes, fee_inr=typ_row.fee_inr,
              expires_at=iso(held.hold_expires_at, ctx.tz),
              next_step="Read the details back and ask the patient to confirm before calling book.")


def register_patient(ctx: ToolContext, args: RegisterPatientIn) -> dict:
    if not args.consent:
        raise ToolFailure("CONSENT_REQUIRED",
                          "Explain what we store (name, age, phone, language, short reason) and why, then ask for consent.")
    phone = normalise_phone(args.phone)
    name = " ".join(args.name.split())
    existing = ctx.session.scalars(select(m.Patient).where(
        m.Patient.clinic_id == ctx.clinic_id, m.Patient.phone == phone)).all()
    for p in existing:
        if p.name.casefold() == name.casefold():
            if p.consent_at is None:
                p.consent_at = repo.utc(ctx.now)
            return ok(patient_id=p.id, name=p.name, existing=True)
    p = m.Patient(clinic_id=ctx.clinic_id, name=name, age=args.age, phone=phone,
                  language=args.language, consent_at=repo.utc(ctx.now))
    ctx.session.add(p)
    ctx.session.flush()
    audit(ctx, "register", "patient", p.id, after={"language": p.language})
    return ok(patient_id=p.id, name=p.name, existing=False)


def find_patient(ctx: ToolContext, args: FindPatientIn) -> dict:
    phone = normalise_phone(args.phone)
    name = " ".join(args.name.split()).casefold()
    p = next((p for p in ctx.session.scalars(select(m.Patient).where(
        m.Patient.clinic_id == ctx.clinic_id, m.Patient.phone == phone)).all()
        if p.name.casefold() == name), None)
    if p is None:
        raise ToolFailure("NOT_FOUND", "No patient with that phone number and name. Offer to register.")
    upcoming = ctx.session.scalars(select(m.Appointment).where(
        m.Appointment.patient_id == p.id, m.Appointment.status.in_(("confirmed", "checked_in")),
        m.Appointment.end >= ctx.now.astimezone(UTC)).order_by(m.Appointment.start)).all()
    return ok(patient_id=p.id, name=p.name, language=p.language,
              upcoming=[appt_summary(ctx, a) for a in upcoming])


def book(ctx: ToolContext, args: BookIn) -> dict:
    row = ctx.session.get(m.Appointment, args.hold_id)
    if row is None or row.clinic_id != ctx.clinic_id or row.status != "held":
        raise ToolFailure("HOLD_NOT_FOUND", "That hold doesn't exist any more. Search and hold a slot again.")
    patient = get_patient(ctx, args.patient_id)
    if patient.consent_at is None:
        raise ToolFailure("CONSENT_REQUIRED", "Ask for consent before booking.")
    if row.patient_id and row.patient_id != patient.id:
        raise ToolFailure("HOLD_NOT_FOUND", "That hold belongs to someone else.")
    doctor = ctx.session.get(m.Doctor, row.doctor_id)
    day = repo._aware(row.start, ctx.tz).date()
    bookings, rows = _day_bookings(ctx, doctor.id, day)
    target = next(b for b in bookings if b.id == row.id)
    target.patient_id = patient.id
    eng.confirm(target, repo.to_engine_doctor(doctor), bookings, ctx.now, ctx.tz,
                token_style=repo.booking_style(doctor, ctx.clinic) == "token")
    repo.write_back(target, row)
    row.patient_id = patient.id
    row.reason_short = " ".join(args.reason_short.split())[:120] or None
    audit(ctx, "book", "appointment", row.id, after={"status": "confirmed", "token_no": row.token_no})
    return ok(**appt_summary(ctx, row),
              next_step="Offer the payment link (send_payment_link) and tell the patient the confirmation details.")


def reschedule(ctx: ToolContext, args: RescheduleIn) -> dict:
    row = own_appointment(ctx, args.appointment_id, args.patient_id)
    doctor = ctx.session.get(m.Doctor, row.doctor_id)
    new_start = parse_start(args.new_start, ctx)
    old_day, new_day = repo._aware(row.start, ctx.tz).date(), new_start.astimezone(ctx.tz).date()
    bookings, _ = repo.load_bookings(ctx.session, [doctor.id], min(old_day, new_day), max(old_day, new_day), ctx.tz)
    target = next(b for b in bookings if b.id == row.id)
    before = {"start": iso(target.start, ctx.tz)}
    eng.reschedule(target, repo.to_engine_doctor(doctor), new_start, bookings, ctx.now, ctx.tz)
    repo.write_back(target, row)
    if repo.booking_style(doctor, ctx.clinic) == "token" and new_day != old_day:
        row.token_no = eng.next_token_no([b for b in bookings if b.id != row.id], doctor.id, new_day, ctx.tz)
    audit(ctx, "reschedule", "appointment", row.id, before=before, after={"start": iso(target.start, ctx.tz)})
    return ok(**appt_summary(ctx, row))


def _waitlist_match(ctx: ToolContext, doctor_id: str, freed: eng.Interval) -> str | None:
    entries = ctx.session.scalars(select(m.WaitlistEntry).where(
        m.WaitlistEntry.doctor_id == doctor_id, m.WaitlistEntry.status == "waiting")).all()
    reqs = []
    for e in entries:
        typ = ctx.session.get(m.AppointmentType, e.type_id)
        reqs.append(eng.WaitlistRequest(
            id=e.id, patient_id=e.patient_id, doctor_id=e.doctor_id, duration_min=typ.minutes,
            window_start=repo._aware(e.window_start, ctx.tz), window_end=repo._aware(e.window_end, ctx.tz),
            created_at=repo._aware(e.created_at, ctx.tz)))
    hit = eng.match_freed_slot(reqs, doctor_id, freed, ctx.now)
    return hit[0].id if hit else None


def cancel(ctx: ToolContext, args: CancelIn) -> dict:
    row = own_appointment(ctx, args.appointment_id, args.patient_id)
    b = repo.to_engine_booking(row, ctx.tz)
    freed = eng.cancel(b)
    repo.write_back(b, row)
    for p in ctx.session.scalars(select(m.Payment).where(m.Payment.appointment_id == row.id)).all():
        if p.status == "pending":
            p.status = "expired"
    audit(ctx, "cancel", "appointment", row.id, after={"status": "cancelled"})
    match = _waitlist_match(ctx, row.doctor_id, freed)
    paid = ctx.session.scalar(select(func.count()).select_from(m.Payment).where(
        m.Payment.appointment_id == row.id, m.Payment.status == "paid"))
    return ok(appointment_id=row.id, status="cancelled", freed=label(freed.start, ctx.tz),
              refund_note="Refunds are handled by clinic staff." if paid else None,
              waitlist_entry_id=match)   # opaque id for the waitlist flow; no other patient's details


def send_payment_link(ctx: ToolContext, args: AppointmentIn) -> dict:
    row = ctx.session.get(m.Appointment, args.appointment_id)
    if row is None or row.clinic_id != ctx.clinic_id or row.status not in ("confirmed", "checked_in"):
        raise ToolFailure("NOT_FOUND", "Payment links are only for confirmed appointments.")
    existing = ctx.session.scalar(select(m.Payment).where(
        m.Payment.appointment_id == row.id, m.Payment.status.in_(("pending", "paid"))))
    if existing:
        return ok(payment_id=existing.id, url=existing.link_url, amount_inr=existing.amount_inr, status=existing.status)
    typ = ctx.session.get(m.AppointmentType, row.type_id)            # amount from the fee table only
    doctor = ctx.session.get(m.Doctor, row.doctor_id)
    patient = ctx.session.get(m.Patient, row.patient_id) if row.patient_id else None
    pay = m.Payment(appointment_id=row.id, amount_inr=typ.fee_inr, status="pending")
    ctx.session.add(pay)
    ctx.session.flush()
    provider = get_provider()
    link = provider.create_link(pay.id, typ.fee_inr,
                                f"{ctx.clinic.name}: {typ.name} with {doctor.name}",
                                patient.phone if patient else None)
    pay.link_url, pay.provider, pay.provider_ref = link.url, provider.name, link.provider_ref
    audit(ctx, "payment_link", "payment", pay.id, after={"amount_inr": typ.fee_inr})
    return ok(payment_id=pay.id, url=link.url, amount_inr=typ.fee_inr, status="pending",
              note="Never ask for card, UPI PIN or OTP; the link handles payment.")


def queue_status(ctx: ToolContext, args: QueueStatusIn) -> dict:
    row = own_appointment(ctx, args.appointment_id, args.patient_id)
    doctor = ctx.session.get(m.Doctor, row.doctor_id)
    day = repo._aware(row.start, ctx.tz).date()
    if row.status not in ("confirmed", "checked_in"):
        raise ToolFailure("NOT_IN_QUEUE", f"This appointment is {row.status}.")
    if day != ctx.local_now.date():
        return ok(**appt_summary(ctx, row), live=False,
                  note="Live wait times are available on the day of the appointment.")
    bookings, _ = _day_bookings(ctx, doctor.id, day)
    target = next(b for b in bookings if b.id == row.id)
    if target.started_at:
        return ok(**appt_summary(ctx, row), live=True, with_doctor_now=True)
    est = eng.estimate_wait(target, repo.to_engine_doctor(doctor), bookings, ctx.now, ctx.tz)
    return ok(**appt_summary(ctx, row), live=True, position=est.position, patients_ahead=est.patients_ahead,
              now_serving=est.now_serving, estimated_wait_min=est.minutes)


def join_waitlist(ctx: ToolContext, args: JoinWaitlistIn) -> dict:
    get_patient(ctx, args.patient_id)
    doctor = get_doctor(ctx, args.doctor_id)
    typ = get_type(ctx, args.appointment_type)
    day = parse_day(args.date, ctx)
    lo, hi = WINDOWS.get(args.window, (time(0, 0), time(23, 59)))
    start = datetime.combine(day, lo, tzinfo=ctx.tz)
    end = datetime.combine(day, hi, tzinfo=ctx.tz)
    if end <= ctx.now:
        raise ToolFailure("IN_PAST", "That time has already passed.")
    entry = m.WaitlistEntry(patient_id=args.patient_id, doctor_id=doctor.id, type_id=typ.id,
                            window_start=start.astimezone(UTC), window_end=end.astimezone(UTC))
    ctx.session.add(entry)
    ctx.session.flush()
    ahead = ctx.session.scalar(select(func.count()).select_from(m.WaitlistEntry).where(
        m.WaitlistEntry.doctor_id == doctor.id, m.WaitlistEntry.status == "waiting",
        m.WaitlistEntry.id != entry.id, m.WaitlistEntry.window_start < end.astimezone(UTC),
        m.WaitlistEntry.window_end > start.astimezone(UTC)))
    audit(ctx, "waitlist_join", "waitlist_entry", entry.id)
    return ok(waitlist_entry_id=entry.id, doctor=doctor.name, date=day.isoformat(), window=args.window,
              people_ahead=ahead, note="We'll offer the first matching slot that frees up.")


def handoff_to_staff(ctx: ToolContext, args: HandoffIn) -> dict:
    if ctx.conversation_id and (conv := ctx.session.get(m.Conversation, ctx.conversation_id)):
        conv.handoff = True
    ctx.session.add(m.GuardrailEvent(conversation_id=ctx.conversation_id, rail="handoff", type="handoff",
                                     outcome="escalate", detail=args.reason[:200]))
    audit(ctx, "handoff", "conversation", ctx.conversation_id, after={"reason": args.reason[:200]})
    return ok(handoff=True, message="A member of the clinic staff will take over shortly.")


def raise_emergency_alert(ctx: ToolContext, args: EmergencyIn) -> dict:
    """Called by the emergency guardrail, never offered to the model."""
    if ctx.conversation_id and (conv := ctx.session.get(m.Conversation, ctx.conversation_id)):
        conv.emergency_flag = True
    ctx.session.add(m.GuardrailEvent(conversation_id=ctx.conversation_id, rail="emergency_screen",
                                     type="emergency", outcome="escalate", detail=args.summary[:200]))
    audit(ctx, "emergency_alert", "conversation", ctx.conversation_id)
    nums = ctx.clinic.emergency_numbers or {}
    return ok(alerted=True, numbers=nums)


# ----------------------------------------------------------------------------- registry
@dataclass
class ToolDef:
    name: str
    fn: Callable[[ToolContext, BaseModel], dict]
    input: type[BaseModel]
    description: str
    model_visible: bool = True
    writes: bool = False


TOOLS: dict[str, ToolDef] = {t.name: t for t in [
    ToolDef("get_clinic_info", get_clinic_info, NoArgs, "Clinic address, doctors, specialties, hours, leave, appointment types and fees."),
    ToolDef("route_to_doctor", route_to_doctor, RouteToDoctorIn, "Which specialty/doctor handles a need, from the clinic's own routing table."),
    ToolDef("find_slots", find_slots, FindSlotsIn, "Best available slots (timed doctors) or next token and estimated time (token-queue doctors)."),
    ToolDef("hold_slot", hold_slot, HoldSlotIn, "Hold a slot for 5 minutes while the patient confirms.", writes=True),
    ToolDef("register_patient", register_patient, RegisterPatientIn, "Register a new patient (only after explicit consent).", writes=True),
    ToolDef("find_patient", find_patient, FindPatientIn, "Verify an existing patient by phone number and name; returns their upcoming appointments."),
    ToolDef("book", book, BookIn, "Confirm a held slot. Call ONLY after the patient explicitly says yes.", writes=True),
    ToolDef("reschedule", reschedule, RescheduleIn, "Move the verified patient's appointment to a new start from find_slots.", writes=True),
    ToolDef("cancel", cancel, CancelIn, "Cancel the verified patient's appointment.", writes=True),
    ToolDef("send_payment_link", send_payment_link, AppointmentIn, "Create a payment link for a confirmed appointment (fee from the clinic's table).", writes=True),
    ToolDef("queue_status", queue_status, QueueStatusIn, "Token number and live estimated wait for the verified patient's appointment today."),
    ToolDef("join_waitlist", join_waitlist, JoinWaitlistIn, "Add the patient to the waitlist when nothing fits.", writes=True),
    ToolDef("handoff_to_staff", handoff_to_staff, HandoffIn, "Hand the conversation to clinic staff.", writes=True),
    ToolDef("raise_emergency_alert", raise_emergency_alert, EmergencyIn, "Emergency alert (guardrail only).",
            model_visible=False, writes=True),
]}


def _clean_schema(schema: dict) -> dict:
    schema = {k: v for k, v in schema.items() if k != "title"}
    if "properties" in schema:
        schema["properties"] = {k: {kk: vv for kk, vv in v.items() if kk != "title"}
                                for k, v in schema["properties"].items()}
    return schema


def openai_tool_specs(names: list[str] | None = None) -> list[dict]:
    """Function-calling specs for the model. Guardrail-only tools are never included."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": _clean_schema(t.input.model_json_schema())}}
            for t in TOOLS.values() if t.model_visible and (names is None or t.name in names)]


def run_tool(name: str, args: dict | None, ctx: ToolContext, *, allow_hidden: bool = False) -> dict:
    tool = TOOLS.get(name)
    if tool is None or (not tool.model_visible and not allow_hidden):
        return fail("UNKNOWN_TOOL", f"No tool named {name!r}.")
    try:
        parsed = tool.input.model_validate(args or {})
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "arguments"
        return fail("INVALID_ARGUMENTS", f"{where}: {first.get('msg')}")
    try:
        result = tool.fn(ctx, parsed)
        if tool.writes:
            ctx.session.commit()
        return result
    except eng.BookingError as e:
        ctx.session.rollback()
        return fail(e.code.value, e.message)
    except ToolFailure as e:
        ctx.session.rollback()
        return fail(e.code, e.message)
    except Exception:  # noqa: BLE001
        ctx.session.rollback()
        log.exception("tool %s failed", name)
        return fail("TOOL_ERROR", "Something went wrong. Offer to hand over to clinic staff.")
