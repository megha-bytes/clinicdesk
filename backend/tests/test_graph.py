"""ClinicDeskGraph v2: booking with consent and confirmation enforced by the graph.

The fake model plays a deliberately over-eager agent: it always passes consent=True and tries to
book under someone else's patient_id. The graph must override both.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from app import models as m
from app import repo
from app.agent.models import DEFAULT_MODELS, MemoryLedger, ModelRouter, load_tiers
from app.agent.tools import ToolContext
from app.graphs.clinicdesk import MAX_TOOL_ROUNDS, answer, build_graph, is_yes, pending_interrupt, send
from app.seed import seed
from tests.fakes import FakeClient, msg

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 12, 9, 47, tzinfo=IST)
NEW_PATIENT = dict(name="Neha Rao", age=30, phone="9123456789", language="en", consent=True)


def done_tools(messages):
    """[(tool_name, result_dict)] from the scratch part of an agent request, in order."""
    names = {}
    out = []
    for x in messages:
        for c in (x.get("tool_calls") or []):
            names[c["id"]] = c["function"]["name"]
        if x.get("role") == "tool":
            out.append((names.get(x["tool_call_id"]), json.loads(x["content"])))
    return out


def last(done, name):
    return next((r for n, r in reversed(done) if n == name), None)


def make_handler(planner, intent="book", language="en"):
    """planner(done) -> ("tool", args) | None (meaning DONE)."""
    def handler(kw):
        system = kw["messages"][0]["content"] if kw["messages"][0]["role"] == "system" else ""
        if "You classify messages" in system:
            return msg(json.dumps({"intent": intent, "language": language}))
        if "action step" in system:
            step = planner(done_tools(kw["messages"]))
            return msg("DONE") if step is None else msg(None, tool_calls=[step])
        assert kw["model"] == DEFAULT_MODELS["super"]
        facts = json.loads(system.split("TOOL RESULTS (this turn):\n", 1)[1])
        summary = "; ".join(f"{f['tool']}:{'ok' if f['result'].get('ok') else f['result'].get('error')}" for f in facts)
        return msg(f"REPLY[{summary}]")
    return handler


@pytest.fixture
def clinic(session):
    return seed(session, now=NOW)["clinic_id"]


def doctor(session, name="Dr. Ananya Rao"):
    return session.scalar(select(m.Doctor).where(m.Doctor.name == name))


def graph_for(session, clinic_id, handler, checkpointer=None):
    ledger = MemoryLedger()
    router = ModelRouter(FakeClient(handler), tiers=load_tiers(), ledger=ledger)
    g = build_graph(router, lambda: ToolContext(session=session, clinic_id=clinic_id, now=NOW), checkpointer)
    return g, ledger


def booking_planner(rao_id, book_as="someone-else"):
    def plan(done):
        names = [n for n, _ in done]
        reg = last(done, "register_patient")
        if reg is None:
            return ("register_patient", NEW_PATIENT)
        if not reg.get("ok"):
            return None
        if "find_slots" not in names:
            return ("find_slots", {"appointment_type": "new", "doctor_id": rao_id, "date": "tomorrow"})
        if "hold_slot" not in names:
            slot = last(done, "find_slots")["slots"][0]
            return ("hold_slot", {"doctor_id": rao_id, "start": slot["start"], "appointment_type": "new"})
        booked = last(done, "book")
        if booked is None:
            return ("book", {"hold_id": last(done, "hold_slot")["hold_id"], "patient_id": book_as})
        if booked.get("ok") and "send_payment_link" not in names:
            return ("send_payment_link", {"appointment_id": booked["appointment_id"]})
        return None
    return plan


# ------------------------------------------------------------------ booking
def test_books_new_patient_with_consent_and_confirmation(session, clinic):
    rao = doctor(session)
    g, ledger = graph_for(session, clinic, make_handler(booking_planner(rao.id)))
    out = send(g, "t1", "Book me with Dr. Rao tomorrow. Neha Rao, 30, 9123456789")
    assert out["interrupt"]["kind"] == "consent" and "Do you agree?" in out["interrupt"]["summary"]
    assert session.scalar(select(func.count()).select_from(m.Patient)) == 60       # nothing stored yet

    out = answer(g, "t1", "yes")
    q = out["interrupt"]
    assert q["kind"] == "confirm" and q["action"] == "book"
    assert "Dr. Ananya Rao" in q["summary"] and "₹500" in q["summary"]            # from the DB, not the model

    out = answer(g, "t1", True)
    assert out["reply"].startswith("REPLY[") and "book:ok" in out["reply"] and "send_payment_link:ok" in out["reply"]
    patient = session.scalar(select(m.Patient).where(m.Patient.phone == "9123456789"))
    appt = session.scalar(select(m.Appointment).where(m.Appointment.patient_id == patient.id))
    assert patient.consent_at is not None
    assert appt.status == "confirmed"                       # booked for the VERIFIED patient, not "someone-else"
    assert session.scalar(select(m.Payment).where(m.Payment.appointment_id == appt.id)).amount_inr == 500
    assert {r.purpose for r in ledger.records} == {"intent", "tool_selection", "reply"}


def test_declined_consent_stores_nothing_even_though_model_said_true(session, clinic):
    g, _ = graph_for(session, clinic, make_handler(booking_planner(doctor(session).id)))
    send(g, "t1", "Book me please. Neha Rao, 30, 9123456789")
    out = answer(g, "t1", "no")
    assert "register_patient:PATIENT_DECLINED" in out["reply"]
    assert session.scalar(select(func.count()).select_from(m.Patient)) == 60
    assert session.scalar(select(m.Patient).where(m.Patient.phone == "9123456789")) is None


def test_declined_booking_changes_nothing(session, clinic):
    g, _ = graph_for(session, clinic, make_handler(booking_planner(doctor(session).id)))
    send(g, "t1", "Book me. Neha Rao, 30, 9123456789")
    answer(g, "t1", "yes")                                  # consent
    out = answer(g, "t1", "no")                             # but not the booking
    assert "book:PATIENT_DECLINED" in out["reply"]
    patient = session.scalar(select(m.Patient).where(m.Patient.phone == "9123456789"))
    statuses = session.scalars(select(m.Appointment.status).where(m.Appointment.patient_id == patient.id)).all()
    assert "confirmed" not in statuses


# ------------------------------------------------------------------ changes to existing appointments
def _patient_with_upcoming(session, doctor_name="Dr. Ananya Rao"):
    d = doctor(session, doctor_name)
    rows = session.scalars(select(m.Appointment).where(m.Appointment.doctor_id == d.id,
                                                       m.Appointment.status == "confirmed")).all()
    rows = [r for r in rows if repo._aware(r.start, IST) > NOW + timedelta(days=1)]
    appt = rows[0]
    return session.get(m.Patient, appt.patient_id), appt


def test_cancel_needs_verification_then_confirmation(session, clinic):
    patient, appt = _patient_with_upcoming(session)

    def plan(done):
        names = [n for n, _ in done]
        if not names:
            return ("cancel", {"appointment_id": appt.id, "patient_id": patient.id})   # tries without verifying
        if "find_patient" not in names:
            return ("find_patient", {"phone": patient.phone, "name": patient.name})
        if names.count("cancel") < 2:
            return ("cancel", {"appointment_id": appt.id, "patient_id": "whoever"})
        return None
    g, _ = graph_for(session, clinic, make_handler(plan, intent="cancel"))
    out = send(g, "t1", "cancel my appointment")
    assert out["interrupt"]["kind"] == "confirm" and out["interrupt"]["summary"].startswith("Cancel your appointment")
    out = answer(g, "t1", "haan")
    assert "cancel:VERIFY_FIRST" in out["reply"] and "cancel:ok" in out["reply"]
    session.expire_all()
    assert session.get(m.Appointment, appt.id).status == "cancelled"


def test_cannot_cancel_someone_elses_appointment(session, clinic):
    me, _ = _patient_with_upcoming(session)
    other_appt = session.scalars(select(m.Appointment).where(m.Appointment.patient_id != me.id,
                                                             m.Appointment.status == "confirmed")).first()

    def plan(done):
        names = [n for n, _ in done]
        if "find_patient" not in names:
            return ("find_patient", {"phone": me.phone, "name": me.name})
        if "cancel" not in names:
            return ("cancel", {"appointment_id": other_appt.id, "patient_id": other_appt.patient_id})
        if "send_payment_link" not in names:
            return ("send_payment_link", {"appointment_id": other_appt.id})
        return None
    g, _ = graph_for(session, clinic, make_handler(plan, intent="cancel"))
    out = send(g, "t1", "cancel")
    assert out["interrupt"] is None                         # no confirmation card for someone else's visit
    assert "cancel:NOT_FOUND" in out["reply"] and "send_payment_link:NOT_FOUND" in out["reply"]
    session.expire_all()
    assert session.get(m.Appointment, other_appt.id).status == "confirmed"


def test_reschedule_with_confirmation(session, clinic):
    patient, appt = _patient_with_upcoming(session)
    old_start = appt.start

    def plan(done):
        names = [n for n, _ in done]
        if "find_patient" not in names:
            return ("find_patient", {"phone": patient.phone, "name": patient.name})
        if "find_slots" not in names:
            return ("find_slots", {"appointment_type": "new", "doctor_id": appt.doctor_id, "date": "2026-10-16",
                                   "window": "evening"})
        if "reschedule" not in names:
            return ("reschedule", {"appointment_id": appt.id, "new_start": last(done, "find_slots")["slots"][0]["start"]})
        return None
    g, _ = graph_for(session, clinic, make_handler(plan, intent="reschedule"))
    out = send(g, "t1", "move my appointment to Friday evening")
    assert out["interrupt"]["summary"].startswith("Move your appointment with Dr. Ananya Rao")
    out = answer(g, "t1", "yes")
    assert "reschedule:ok" in out["reply"]
    session.expire_all()
    assert session.get(m.Appointment, appt.id).start != old_start


# ------------------------------------------------------------------ durability, limits, misc
def test_paused_confirmation_survives_a_restart(session, clinic, tmp_path):
    path = str(tmp_path / "cp.db")
    handler = make_handler(booking_planner(doctor(session).id))
    g1, _ = graph_for(session, clinic, handler, SqliteSaver(sqlite3.connect(path, check_same_thread=False)))
    send(g1, "t1", "Book me. Neha Rao, 30, 9123456789")
    answer(g1, "t1", "yes")                                 # consent → now waiting on the booking confirmation
    del g1                                                  # "server restart"

    g2, _ = graph_for(session, clinic, handler, SqliteSaver(sqlite3.connect(path, check_same_thread=False)))
    q = pending_interrupt(g2, "t1")
    assert q and q["kind"] == "confirm" and "Dr. Ananya Rao" in q["summary"]
    out = answer(g2, "t1", "yes")
    assert "book:ok" in out["reply"]


def test_tool_rounds_are_bounded(session, clinic):
    calls = {"n": 0}

    def plan(done):
        calls["n"] += 1
        return ("get_clinic_info", {})
    g, _ = graph_for(session, clinic, make_handler(plan, intent="info"))
    out = send(g, "t1", "timings?")
    assert out["reply"] and calls["n"] <= MAX_TOOL_ROUNDS + 1


def test_smalltalk_skips_the_agent(session, clinic):
    def plan(done):
        raise AssertionError("agent must not run for smalltalk")
    g, ledger = graph_for(session, clinic, make_handler(plan, intent="smalltalk", language="hi"))
    out = send(g, "t1", "Namaste")
    assert out["reply"] == "REPLY[]" and [r.purpose for r in ledger.records] == ["intent", "reply"]


def test_conversation_remembers_previous_turns(session, clinic):
    seen = {}

    def handler(kw):
        system = kw["messages"][0]["content"]
        if "You classify messages" in system:
            return msg('{"intent": "smalltalk", "language": "en"}')
        seen["history"] = [x["content"] for x in kw["messages"][1:]]
        return msg("ok")
    g, _ = graph_for(session, clinic, handler)
    send(g, "t1", "hello")
    send(g, "t1", "thanks")
    assert seen["history"] == ["hello", "ok", "thanks"]


def test_model_outage_gives_safe_reply(session, clinic):
    g, _ = graph_for(session, clinic, lambda kw: (_ for _ in ()).throw(ConnectionError("down")))
    out = send(g, "t1", "What are your timings?")
    assert "clinic staff" in out["reply"]


@pytest.mark.parametrize("value,expected", [
    ("yes", True), ("Haan", True), ("ಹೌದು", True), ("हाँ", True), ({"approved": True}, True), (True, True),
    ("no", False), ("nahi", False), ("", False), (False, False), ({"approved": False}, False),
])
def test_is_yes(value, expected):
    assert is_yes(value) is expected
