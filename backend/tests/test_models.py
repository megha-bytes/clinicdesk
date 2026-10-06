from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Appointment, AppointmentType, Clinic, Doctor, LLMCall, Patient


def _clinic(session):
    c = Clinic(name="Sunrise Clinic", booking_style="timed")
    d = Doctor(clinic=c, name="Dr. Rao", specialty="General Physician", hours={"mon": [["09:00", "13:00"]]})
    t = AppointmentType(clinic=c, name="new", minutes=20, fee_inr=500)
    session.add_all([c, d, t])
    session.commit()
    return c, d, t


def test_create_booking_graph(session):
    c, d, t = _clinic(session)
    p = Patient(clinic_id=c.id, name="Priya", phone="9876543210", language="hi")
    session.add(p)
    session.commit()
    start = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    a = Appointment(clinic_id=c.id, patient_id=p.id, doctor_id=d.id, type_id=t.id,
                    start=start, end=start + timedelta(minutes=t.minutes), status="confirmed")
    session.add(a)
    session.commit()
    assert a.id and a.source == "web" and not a.urgent_by_staff
    assert c.emergency_numbers["ambulance"] == "108"


def test_rejects_end_before_start(session):
    c, d, t = _clinic(session)
    start = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    session.add(Appointment(clinic_id=c.id, doctor_id=d.id, type_id=t.id, start=start, end=start))
    with pytest.raises(IntegrityError):
        session.commit()


def test_rejects_unknown_status(session):
    c, d, t = _clinic(session)
    start = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    session.add(Appointment(clinic_id=c.id, doctor_id=d.id, type_id=t.id, start=start,
                            end=start + timedelta(minutes=10), status="vip"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_rejects_invalid_booking_style(session):
    session.add(Clinic(name="Bad", booking_style="walkin"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_cost_ledger_row(session):
    session.add(LLMCall(model="nvidia/nemotron-nano", tier="nano", input_tokens=120, output_tokens=30,
                        cost_usd=0.00001, latency_ms=410))
    session.commit()
    assert session.query(LLMCall).count() == 1
