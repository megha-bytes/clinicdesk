"""Synthetic demo clinic. ALL data here is fake: names are generated and phone numbers are in the
9000000001–9000000060 range so they can't belong to real patients.

    python -m app.seed            # create the demo clinic if it doesn't exist
    python -m app.seed --reset    # delete it and rebuild with fresh dates (used by the nightly reset)

Bookings fill a week from today. Today's bookings that are already over are marked completed with
realistic consult times (so the live queue has a pace to learn from), and one is "with the doctor".
"""
from __future__ import annotations

import argparse
import random
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import engine as eng
from app import models as m
from app import repo

CLINIC_NAME = "Sunrise Family Clinic"
UTC = timezone.utc
WEEK = ("mon", "tue", "wed", "thu", "fri", "sat")

DOCTORS = [
    dict(key="rao", name="Dr. Ananya Rao", specialty="General Physician", languages=["en", "hi", "kn"],
         hours={d: [["09:00", "13:00"], ["17:00", "20:00"]] for d in WEEK}, breaks=[["11:00", "11:15"]],
         avg_consult_min=12, booking_style="timed"),
    dict(key="nair", name="Dr. Meera Nair", specialty="General Physician", languages=["en", "kn", "ml"],
         hours={d: [["08:30", "12:30"]] for d in WEEK}, breaks=[], avg_consult_min=8, booking_style="token"),
    dict(key="iyer", name="Dr. Vikram Iyer", specialty="Dermatologist", languages=["en", "ta", "kn"],
         hours={d: [["10:00", "13:00"], ["16:00", "19:00"]] for d in ("mon", "wed", "fri")}, breaks=[],
         avg_consult_min=15, daily_cap=18, booking_style="timed"),
    dict(key="khan", name="Dr. Farah Khan", specialty="Pediatrician", languages=["en", "hi", "ur"],
         hours={d: [["10:00", "14:00"]] for d in WEEK}, breaks=[["12:00", "12:15"]],
         avg_consult_min=12, booking_style="timed"),
]

TYPES = [("new", 20, 500), ("follow_up", 10, 300), ("procedure:dressing", 15, 400)]

# Clinic-defined routing (English, Hindi in Latin + Devanagari, Kannada). The model never decides this.
ROUTING = {
    "General Physician": ["fever", "cold", "cough", "headache", "bp", "sugar", "diabetes", "stomach",
                          "bukhar", "khansi", "बुखार", "खांसी", "ಜ್ವರ", "ಕೆಮ್ಮು", "general"],
    "Dermatologist": ["skin", "rash", "acne", "pimple", "itch", "hair fall", "eczema", "khujli",
                      "खुजली", "त्वचा", "ಚರ್ಮ", "ತುರಿಕೆ"],
    "Pediatrician": ["child", "baby", "kid", "infant", "vaccination", "vaccine", "son", "daughter",
                     "bachcha", "बच्चा", "बच्चे", "ಮಗು", "ಮಕ್ಕಳ"],
}

# If a child is mentioned, the pediatrician sees them, whatever the symptom.
ROUTING_PRIORITY = {"Pediatrician": 10}

FIRST = ["Aarav", "Priya", "Rohan", "Kavya", "Arjun", "Sneha", "Vihaan", "Ananya", "Rahul", "Divya",
         "Karthik", "Meghna", "Siddharth", "Pooja", "Aditya", "Lakshmi", "Nikhil", "Shreya", "Varun", "Ishita"]
LAST = ["Sharma", "Reddy", "Gowda", "Iyer", "Patel", "Nair", "Kulkarni", "Hegde", "Rao", "Menon",
        "Shetty", "Joshi", "Bhat", "Das", "Pillai"]


def _clear(session: Session, clinic: m.Clinic) -> None:
    cid = clinic.id
    doctor_ids = select(m.Doctor.id).where(m.Doctor.clinic_id == cid)
    patient_ids = select(m.Patient.id).where(m.Patient.clinic_id == cid)
    appt_ids = select(m.Appointment.id).where(m.Appointment.clinic_id == cid)
    conv_ids = select(m.Conversation.id).where(m.Conversation.clinic_id == cid)
    session.execute(delete(m.Payment).where(m.Payment.appointment_id.in_(appt_ids)))
    session.execute(delete(m.Appointment).where(m.Appointment.clinic_id == cid))
    session.execute(delete(m.WaitlistEntry).where(m.WaitlistEntry.doctor_id.in_(doctor_ids)))
    session.execute(delete(m.GuardrailEvent).where(m.GuardrailEvent.conversation_id.in_(conv_ids)))
    session.execute(delete(m.LLMCall).where(m.LLMCall.conversation_id.in_(conv_ids)))
    session.execute(delete(m.Conversation).where(m.Conversation.clinic_id == cid))
    session.execute(delete(m.RoutingRule).where(m.RoutingRule.clinic_id == cid))
    session.execute(delete(m.Patient).where(m.Patient.id.in_(patient_ids)))
    session.execute(delete(m.Doctor).where(m.Doctor.clinic_id == cid))
    session.execute(delete(m.AppointmentType).where(m.AppointmentType.clinic_id == cid))
    session.execute(delete(m.OnboardingDraft).where(m.OnboardingDraft.clinic_id == cid))
    session.delete(clinic)
    session.flush()


def seed(session: Session, now: datetime | None = None, reset: bool = False, rng_seed: int = 42) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    existing = session.scalar(select(m.Clinic).where(m.Clinic.name == CLINIC_NAME))
    if existing and not reset:
        return {"clinic_id": existing.id, "created": False}
    if existing:
        _clear(session, existing)

    rng = random.Random(rng_seed)
    clinic = m.Clinic(name=CLINIC_NAME, timezone="Asia/Kolkata", booking_style="timed",
                      address="12 4th Block, Jayanagar, Bengaluru 560011 (demo)",
                      hours={d: [["08:30", "20:00"]] for d in WEEK}, languages=["en", "hi", "kn"])
    session.add(clinic)
    session.flush()
    tz = repo.clinic_tz(clinic)
    today = now.astimezone(tz).date()

    types = {}
    for name, mins, fee in TYPES:
        types[name] = m.AppointmentType(clinic_id=clinic.id, name=name, minutes=mins, fee_inr=fee)
        session.add(types[name])

    doctors: dict[str, m.Doctor] = {}
    for spec in DOCTORS:
        d = {k: v for k, v in spec.items() if k != "key"}
        doctors[spec["key"]] = m.Doctor(clinic_id=clinic.id, **d)
        session.add(doctors[spec["key"]])
    # Dr. Khan is on leave three days from now (shows the ON_LEAVE rule in the demo).
    doctors["khan"].leave = [(today + timedelta(days=3)).isoformat()]
    session.flush()

    for specialty, words in ROUTING.items():
        for w in words:
            session.add(m.RoutingRule(clinic_id=clinic.id, keyword=w, specialty=specialty,
                                      priority=ROUTING_PRIORITY.get(specialty, 0)))

    patients = []
    for i in range(60):
        lang = rng.choices(["en", "hi", "kn"], weights=[4, 3, 3])[0]
        p = m.Patient(clinic_id=clinic.id, name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                      age=rng.randint(1, 80), phone=f"90000{i + 1:05d}", language=lang,
                      consent_at=now - timedelta(days=rng.randint(1, 200)))
        patients.append(p)
        session.add(p)
    session.flush()

    n_appts = _seed_bookings(session, clinic, doctors, types, patients, today, now, tz, rng)

    # Two people waiting for Dr. Rao tomorrow morning (for the waitlist demo).
    tomorrow = today + timedelta(days=1)
    for i, p in enumerate(patients[:2]):
        session.add(m.WaitlistEntry(
            patient_id=p.id, doctor_id=doctors["rao"].id, type_id=types["follow_up"].id,
            window_start=repo.utc(eng.types.at(tomorrow, "09:00", tz)),
            window_end=repo.utc(eng.types.at(tomorrow, "13:00", tz)),
            created_at=now - timedelta(hours=5 - i)))
    session.commit()
    return {"clinic_id": clinic.id, "created": True, "doctors": len(doctors), "patients": len(patients),
            "appointments": n_appts, "first_day": today.isoformat()}


def _seed_bookings(session, clinic, doctors, types, patients, today: date, now: datetime, tz, rng) -> int:
    count = 0
    for key, row in doctors.items():
        doc = repo.to_engine_doctor(row)
        token_style = repo.booking_style(row, clinic) == "token"
        fill = 0.65 if token_style else 0.5
        for offset in range(7):
            day = today + timedelta(days=offset)
            day_bookings: list[eng.Booking] = []
            token = 0
            for window in eng.working_intervals(doc, day, tz):
                cursor = window.start
                while True:
                    tname = rng.choices(["new", "follow_up", "procedure:dressing"], weights=[4, 5, 1])[0]
                    typ = types[tname]
                    end = cursor + timedelta(minutes=typ.minutes)
                    if end > window.end or (doc.daily_cap and len(day_bookings) >= doc.daily_cap):
                        break
                    if rng.random() < fill:
                        status = eng.Status.CONFIRMED
                        started = completed = None
                        if end <= now:                     # already happened today
                            status = eng.Status.COMPLETED
                            started = cursor + timedelta(minutes=rng.randint(0, 4))
                            completed = started + timedelta(minutes=max(4, doc.avg_consult_min + rng.randint(-3, 6)))
                        elif cursor <= now < end:          # with the doctor right now
                            status, started = eng.Status.CHECKED_IN, cursor
                        token = token + 1
                        b = eng.Booking(id=uuid.uuid4().hex, doctor_id=doc.id, start=cursor, end=end, status=status,
                                        type_name=tname, patient_id=rng.choice(patients).id,
                                        token_no=token if token_style else None,
                                        created_at=now - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 12)),
                                        started_at=started, completed_at=completed)
                        day_bookings.append(b)
                        session.add(repo.new_row_from(b, clinic.id, typ.id, rng.choice(["web", "web", "voice", "staff"])))
                        count += 1
                        cursor = end
                    else:
                        cursor += timedelta(minutes=10)
    return count


def main() -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description="Seed the synthetic demo clinic.")
    parser.add_argument("--reset", action="store_true", help="delete and rebuild the demo clinic")
    args = parser.parse_args()
    with SessionLocal() as session:
        print(seed(session, reset=args.reset))


if __name__ == "__main__":
    main()
