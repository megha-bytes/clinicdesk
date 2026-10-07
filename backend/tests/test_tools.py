"""Agent tools against a seeded demo clinic. "Now" is Monday 12 Oct 2026, 09:47 IST."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app import models as m
from app import repo
from app.agent.tools import TOOLS, ToolContext, openai_tool_specs, run_tool
from app.seed import seed

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 12, 9, 47, tzinfo=IST)


@pytest.fixture
def ctx(session):
    info = seed(session, now=NOW)
    return ToolContext(session=session, clinic_id=info["clinic_id"], now=NOW)


def doc(ctx, key_name):
    return ctx.session.scalar(select(m.Doctor).where(m.Doctor.name == key_name))


def call(ctx, tool, **args):
    return run_tool(tool, args, ctx)


def new_patient(ctx, name="Test Patient", phone="9123456789"):
    r = call(ctx, "register_patient", name=name, age=34, phone=phone, language="hi", consent=True)
    assert r["ok"], r
    return r["patient_id"]


# ------------------------------------------------------------------ seed
def test_seed_shape_and_no_overlaps(ctx):
    s = ctx.session
    assert s.scalar(select(func.count()).select_from(m.Doctor)) == 4
    assert s.scalar(select(func.count()).select_from(m.Patient)) == 60
    rows = s.scalars(select(m.Appointment)).all()
    assert len(rows) > 100
    by_doc = {}
    for r in rows:
        by_doc.setdefault(r.doctor_id, []).append((repo._aware(r.start, IST), repo._aware(r.end, IST)))
    for spans in by_doc.values():
        spans.sort()
        assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))


def test_seed_today_has_history_and_one_in_consult(ctx):
    nair = doc(ctx, "Dr. Meera Nair")
    today = ctx.session.scalars(select(m.Appointment).where(m.Appointment.doctor_id == nair.id)).all()
    today = [a for a in today if repo._aware(a.start, IST).date() == NOW.date()]
    statuses = {a.status for a in today}
    assert "completed" in statuses and "confirmed" in statuses
    assert sum(1 for a in today if a.status == "checked_in" and a.started_at) <= 1
    assert all(a.token_no for a in today)                       # token-queue doctor


def test_seed_is_idempotent_and_resettable(session):
    a = seed(session, now=NOW)
    assert seed(session, now=NOW)["created"] is False
    b = seed(session, now=NOW, reset=True)
    assert b["created"] and b["clinic_id"] != a["clinic_id"]
    assert session.scalar(select(func.count()).select_from(m.Clinic)) == 1


def test_seed_phone_numbers_are_obviously_fake(ctx):
    phones = ctx.session.scalars(select(m.Patient.phone)).all()
    assert all(p.startswith("90000") for p in phones)


# ------------------------------------------------------------------ registry & specs
def test_specs_cover_model_tools_and_hide_emergency():
    names = {s["function"]["name"] for s in openai_tool_specs()}
    assert "raise_emergency_alert" not in names
    assert names == {n for n, t in TOOLS.items() if t.model_visible}
    find = next(s for s in openai_tool_specs() if s["function"]["name"] == "find_slots")
    assert "appointment_type" in find["function"]["parameters"]["required"]


def test_hidden_tool_not_callable_by_model(ctx):
    assert call(ctx, "raise_emergency_alert", summary="x")["error"] == "UNKNOWN_TOOL"
    assert run_tool("raise_emergency_alert", {"summary": "chest pain"}, ctx, allow_hidden=True)["ok"]


def test_bad_arguments_never_raise(ctx):
    r = call(ctx, "find_slots", appointment_type="new", days=99)
    assert r["ok"] is False and r["error"] == "INVALID_ARGUMENTS" and "days" in r["message"]
    assert call(ctx, "nope")["error"] == "UNKNOWN_TOOL"


# ------------------------------------------------------------------ info & routing
def test_clinic_info_fees_come_from_table(ctx):
    r = call(ctx, "get_clinic_info")
    assert {t["name"]: t["fee_inr"] for t in r["appointment_types"]} == {
        "new": 500, "follow_up": 300, "procedure:dressing": 400}
    assert r["emergency_numbers"]["ambulance"] == "108"
    khan = next(d for d in r["doctors"] if d["name"] == "Dr. Farah Khan")
    assert khan["on_leave"] == ["2026-10-15"]


@pytest.mark.parametrize("need,specialty", [
    ("fever for 3 days", "General Physician"),
    ("मुझे बुखार है", "General Physician"),
    ("skin rash on arm", "Dermatologist"),
    ("ಮಗು ಕೆಮ್ಮುತ್ತಿದೆ", "Pediatrician"),          # "child is coughing": child rule has clinic priority
    ("my baby has a rash", "Pediatrician"),        # baby + rash → pediatrics, not dermatology
    ("rash on my face", "Dermatologist"),
])
def test_routing_uses_clinic_table(ctx, need, specialty):
    r = call(ctx, "route_to_doctor", need=need)
    assert r["ok"] and r["matched_rule"] and r["specialty"] == specialty


def test_routing_unknown_defaults_to_gp(ctx):
    r = call(ctx, "route_to_doctor", need="something unusual")
    assert r["specialty"] == "General Physician" and r["matched_rule"] is False and len(r["doctors"]) == 2


# ------------------------------------------------------------------ slots, hold, book
def test_find_slots_timed_returns_ranked_valid_options(ctx):
    rao = doc(ctx, "Dr. Ananya Rao")
    r = call(ctx, "find_slots", appointment_type="new", doctor_id=rao.id, date="tomorrow", window="morning")
    assert r["ok"] and 1 <= len(r["slots"]) <= 3
    for s in r["slots"]:
        start = datetime.fromisoformat(s["start"])
        assert s["booking_style"] == "timed" and s["fee_inr"] == 500 and s["minutes"] == 20
        assert start.date() == NOW.date() + timedelta(days=1) and 9 <= start.hour < 12


def test_find_slots_token_doctor_gives_next_token(ctx):
    nair = doc(ctx, "Dr. Meera Nair")
    r = call(ctx, "find_slots", appointment_type="follow_up", doctor_id=nair.id, date="today")
    [offer] = r["slots"]
    assert offer["booking_style"] == "token" and offer["next_token"] > 1
    assert datetime.fromisoformat(offer["start"]) >= NOW


def test_find_slots_on_leave_day_is_empty(ctx):
    khan = doc(ctx, "Dr. Farah Khan")
    r = call(ctx, "find_slots", appointment_type="new", doctor_id=khan.id, date="2026-10-15")
    assert r["ok"] and r["slots"] == [] and "waitlist" in r["suggestion"]


def test_find_slots_rejects_past_dates_and_unknown_type(ctx):
    assert call(ctx, "find_slots", appointment_type="new", date="2026-10-01")["error"] == "IN_PAST"
    assert call(ctx, "find_slots", appointment_type="massage")["error"] == "UNKNOWN_TYPE"


def test_full_booking_flow(ctx):
    pid = new_patient(ctx)
    rao = doc(ctx, "Dr. Ananya Rao")
    slot = call(ctx, "find_slots", appointment_type="new", doctor_id=rao.id, date="tomorrow")["slots"][0]
    held = call(ctx, "hold_slot", doctor_id=rao.id, start=slot["start"], appointment_type="new", patient_id=pid)
    assert held["ok"] and held["fee_inr"] == 500 and held["when"]
    booked = call(ctx, "book", hold_id=held["hold_id"], patient_id=pid, reason_short="fever  3 days\n")
    assert booked["ok"] and booked["status"] == "confirmed" and booked["doctor"] == "Dr. Ananya Rao"
    row = ctx.session.get(m.Appointment, held["hold_id"])
    assert row.reason_short == "fever 3 days" and row.source == "web"
    actions = ctx.session.scalars(select(m.AuditLog.action).where(m.AuditLog.entity_id == row.id)).all()
    assert {"hold", "book"} <= set(actions)


def test_slot_cannot_be_double_held(ctx):
    rao = doc(ctx, "Dr. Ananya Rao")
    slot = call(ctx, "find_slots", appointment_type="new", doctor_id=rao.id, date="tomorrow")["slots"][0]
    assert call(ctx, "hold_slot", doctor_id=rao.id, start=slot["start"], appointment_type="new")["ok"]
    second = call(ctx, "hold_slot", doctor_id=rao.id, start=slot["start"], appointment_type="new")
    assert second["ok"] is False and second["error"] == "OVERLAP"


def test_invented_time_is_rejected(ctx):
    rao = doc(ctx, "Dr. Ananya Rao")
    r = call(ctx, "hold_slot", doctor_id=rao.id, start="2026-10-13T14:30+05:30", appointment_type="new")
    assert r["error"] == "OUTSIDE_HOURS"


def test_expired_hold_cannot_be_booked(ctx):
    pid = new_patient(ctx)
    rao = doc(ctx, "Dr. Ananya Rao")
    slot = call(ctx, "find_slots", appointment_type="new", doctor_id=rao.id, date="tomorrow")["slots"][0]
    held = call(ctx, "hold_slot", doctor_id=rao.id, start=slot["start"], appointment_type="new")
    ctx.now = NOW + timedelta(minutes=6)
    r = call(ctx, "book", hold_id=held["hold_id"], patient_id=pid)
    assert r["error"] == "HOLD_EXPIRED"


def test_token_booking_gets_next_token(ctx):
    pid = new_patient(ctx)
    nair = doc(ctx, "Dr. Meera Nair")
    offer = call(ctx, "find_slots", appointment_type="follow_up", doctor_id=nair.id)["slots"][0]
    held = call(ctx, "hold_slot", doctor_id=nair.id, start=offer["start"], appointment_type="follow_up")
    booked = call(ctx, "book", hold_id=held["hold_id"], patient_id=pid)
    assert booked["token_no"] == offer["next_token"] and "token" in booked["when"]


# ------------------------------------------------------------------ patients & privacy
def test_register_requires_consent_and_valid_phone(ctx):
    r = call(ctx, "register_patient", name="A B", age=30, phone="9123456789", consent=False)
    assert r["error"] == "CONSENT_REQUIRED"
    r = call(ctx, "register_patient", name="A B", age=30, phone="12345", consent=True)
    assert r["error"] == "INVALID_PHONE"


@pytest.mark.parametrize("raw", ["+91 91234 56789", "091234-56789", "9123456789"])
def test_phone_formats_normalise(ctx, raw):
    r = call(ctx, "register_patient", name="Neha Rao", age=30, phone=raw, consent=True)
    assert r["ok"] and ctx.session.get(m.Patient, r["patient_id"]).phone == "9123456789"


def test_register_deduplicates(ctx):
    a = new_patient(ctx, "Neha Rao")
    r = call(ctx, "register_patient", name="neha  rao", age=30, phone="9123456789", consent=True)
    assert r["patient_id"] == a and r["existing"] is True


def test_find_patient_needs_phone_and_name(ctx):
    pid = new_patient(ctx, "Neha Rao")
    assert call(ctx, "find_patient", phone="9123456789", name="NEHA RAO")["patient_id"] == pid
    wrong_name = call(ctx, "find_patient", phone="9123456789", name="Someone Else")
    unknown_phone = call(ctx, "find_patient", phone="9988776655", name="Neha Rao")
    assert wrong_name["error"] == unknown_phone["error"] == "NOT_FOUND"
    assert wrong_name["message"] == unknown_phone["message"]       # no hint whether the phone exists


def _book(ctx, pid, doctor_name="Dr. Ananya Rao", typ="new", day="tomorrow"):
    d = doc(ctx, doctor_name)
    slot = call(ctx, "find_slots", appointment_type=typ, doctor_id=d.id, date=day)["slots"][0]
    held = call(ctx, "hold_slot", doctor_id=d.id, start=slot["start"], appointment_type=typ)
    return call(ctx, "book", hold_id=held["hold_id"], patient_id=pid)


def test_cannot_touch_someone_elses_appointment(ctx):
    owner = new_patient(ctx, "Owner One", "9111111111")
    other = new_patient(ctx, "Other Two", "9222222222")
    appt = _book(ctx, owner)["appointment_id"]
    for tool in ("cancel", "queue_status"):
        assert call(ctx, tool, appointment_id=appt, patient_id=other)["error"] == "NOT_FOUND"
    later = call(ctx, "find_slots", appointment_type="new", date="tomorrow", window="evening",
                 doctor_id=doc(ctx, "Dr. Ananya Rao").id)["slots"][0]["start"]
    assert call(ctx, "reschedule", appointment_id=appt, patient_id=other, new_start=later)["error"] == "NOT_FOUND"


def test_reschedule_own_appointment(ctx):
    pid = new_patient(ctx)
    appt = _book(ctx, pid)["appointment_id"]
    evening = call(ctx, "find_slots", appointment_type="new", date="tomorrow", window="evening",
                   doctor_id=doc(ctx, "Dr. Ananya Rao").id)["slots"][0]["start"]
    r = call(ctx, "reschedule", appointment_id=appt, patient_id=pid, new_start=evening)
    assert r["ok"] and r["start"] == evening


def test_cancel_frees_slot_and_finds_waitlist_match(ctx):
    pid = new_patient(ctx)
    booked = _book(ctx, pid, typ="follow_up")              # Dr. Rao tomorrow morning
    r = call(ctx, "cancel", appointment_id=booked["appointment_id"], patient_id=pid)
    assert r["ok"] and r["status"] == "cancelled"
    assert r["waitlist_entry_id"] is not None              # seed has people waiting for Dr. Rao tomorrow morning
    assert call(ctx, "cancel", appointment_id=booked["appointment_id"], patient_id=pid)["error"] == "INVALID_STATUS"


# ------------------------------------------------------------------ payments, queue, waitlist, handoff
def test_payment_link_amount_from_fee_table_and_idempotent(ctx):
    pid = new_patient(ctx)
    appt = _book(ctx, pid, typ="follow_up")["appointment_id"]
    a = call(ctx, "send_payment_link", appointment_id=appt)
    b = call(ctx, "send_payment_link", appointment_id=appt)
    assert a["ok"] and a["amount_inr"] == 300 and a["url"].endswith(f"/pay/{a['payment_id']}")
    assert a["payment_id"] == b["payment_id"]


def test_no_payment_link_for_held_or_cancelled(ctx):
    rao = doc(ctx, "Dr. Ananya Rao")
    slot = call(ctx, "find_slots", appointment_type="new", doctor_id=rao.id, date="tomorrow")["slots"][0]
    held = call(ctx, "hold_slot", doctor_id=rao.id, start=slot["start"], appointment_type="new")
    assert call(ctx, "send_payment_link", appointment_id=held["hold_id"])["error"] == "NOT_FOUND"


def test_queue_status_live_for_today_token(ctx):
    pid = new_patient(ctx)
    booked = _book(ctx, pid, doctor_name="Dr. Meera Nair", typ="follow_up", day="today")
    r = call(ctx, "queue_status", appointment_id=booked["appointment_id"], patient_id=pid)
    assert r["ok"] and r["live"] and r["token_no"] == booked["token_no"]
    assert r["patients_ahead"] >= 0 and r["estimated_wait_min"] % 5 == 0


def test_queue_status_future_day_not_live(ctx):
    pid = new_patient(ctx)
    booked = _book(ctx, pid, doctor_name="Dr. Meera Nair", typ="follow_up", day="tomorrow")
    r = call(ctx, "queue_status", appointment_id=booked["appointment_id"], patient_id=pid)
    assert r["ok"] and r["live"] is False


def test_join_waitlist(ctx):
    pid = new_patient(ctx)
    rao = doc(ctx, "Dr. Ananya Rao")
    r = call(ctx, "join_waitlist", patient_id=pid, doctor_id=rao.id, appointment_type="follow_up",
             date="tomorrow", window="morning")
    assert r["ok"] and r["people_ahead"] == 2          # two seeded entries ahead
    assert call(ctx, "join_waitlist", patient_id=pid, doctor_id=rao.id, appointment_type="new",
                date="2026-10-01")["error"] == "IN_PAST"


def test_handoff_flags_conversation(ctx):
    conv = m.Conversation(clinic_id=ctx.clinic_id, channel="web")
    ctx.session.add(conv)
    ctx.session.commit()
    ctx.conversation_id = conv.id
    assert call(ctx, "handoff_to_staff", reason="wants a human", summary="asked twice")["ok"]
    assert ctx.session.get(m.Conversation, conv.id).handoff is True
    ev = ctx.session.scalar(select(m.GuardrailEvent).where(m.GuardrailEvent.conversation_id == conv.id))
    assert ev.type == "handoff" and ev.outcome == "escalate"


def test_a2a_bookings_are_tagged(ctx):
    ctx.channel = "a2a"
    pid = new_patient(ctx)
    row = ctx.session.get(m.Appointment, _book(ctx, pid)["appointment_id"])
    assert row.source == "a2a"
    actors = ctx.session.scalars(select(m.AuditLog.actor).where(m.AuditLog.entity_id == row.id)).all()
    assert set(actors) == {"a2a"}
