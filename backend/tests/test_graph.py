"""ClinicDeskGraph: the tool-calling agent path (cancel, reschedule, info) and reply checks.

The fake model plays a deliberately over-eager agent: it tries actions without verifying the patient
and under someone else's patient_id. The graph must stop both. Bookings: tests/test_booking_flow.py.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app import models as m
from app import repo
from app.agent.models import DEFAULT_MODELS, MemoryLedger, ModelRouter, load_tiers
from app.agent.tools import ToolContext
from app.graphs.clinicdesk import MAX_TOOL_ROUNDS, answer, build_graph, is_yes, send
from app.seed import seed
from tests.fakes import FakeClient, msg

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 12, 9, 47, tzinfo=IST)


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


def make_handler(planner, intent="info", language="en"):
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


# ------------------------------------------------------------------ reply checks (DEVLOG #27)
def test_false_booking_claim_is_replaced(session, clinic):
    """Live run: 'yes' was treated as smalltalk and the reply writer said 'successfully booked'."""
    replies = []

    def handler(kw):
        system = kw["messages"][0]["content"]
        if "You classify messages" in system:
            return msg(json.dumps({"intent": "smalltalk", "language": "en"}))
        replies.append(1)
        return msg("Great! Your appointment has been successfully booked. Patient ID: c04a9c9ed67b4179b92265b2e1fe57f4.")
    g, _ = graph_for(session, clinic, handler)
    out = send(g, "r4", "yes")
    assert len(replies) == 2                                               # regenerated once, then replaced
    assert "booked" not in out["reply"].replace("haven't booked", "") and "c04a9c9e" not in out["reply"]


def test_ids_are_scrubbed_but_payment_links_kept():
    from app.graphs.clinicdesk import scrub_ids
    text = ("Thank you, John! Your patient ID is **c04a9c9ed67b4179b92265b2e1fe57f4**. "
            "Pay here: https://x.onrender.com/pay/ff3097c84ae547d086b7f85ce247ec85 ok")
    out = scrub_ids(text)
    assert "c04a9c9e" not in out and "/pay/ff3097c84ae547d086b7f85ce247ec85" in out and out.startswith("Thank you")


def test_real_booking_claim_is_allowed():
    from app.graphs.clinicdesk import false_claim
    assert false_claim("Your appointment has been booked.", [])
    assert not false_claim("Your appointment has been booked.", [{"tool": "book", "result": {"ok": True}}])
    assert not false_claim("Here are three times.", [])
