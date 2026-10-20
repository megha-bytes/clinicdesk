"""Staff dashboard endpoints (Days 15–16)."""
import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app import chat_api
from app import models as m
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.graphs.clinicdesk import build_graph
from app.main import app
from app.realtime.hub import hub, ops_channel
from app.seed import seed
from app.staff_api import short_name
from tests.fakes import FakeClient
from tests.test_booking_flow import Fake


@pytest.fixture
def client(session, monkeypatch):
    hub.reset()
    info = seed(session)
    fake = Fake({"I have severe chest pain": {"intent": "other"}, "talk to a person": {"intent": "human"}})
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    chat_api._hits.clear()
    with TestClient(app) as c:
        c.clinic_id = info["clinic_id"]
        yield c
    hub.reset()


def workday() -> str:
    """A weekday within the seeded range (the demo clinic is closed on Sundays)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    d = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    while d.weekday() > 4:
        d += timedelta(days=1)
    return d.isoformat()


def test_short_name():
    assert short_name("Asha Reddy") == "Asha R." and short_name("Ravi") == "Ravi" and short_name(None) == "Walk-in"


def test_schedule_has_doctors_and_masked_patients(client, session):
    sch = client.get(f"/ops/schedule?day={workday()}").json()
    assert len(sch["doctors"]) == 4 and {"id", "hours", "now_serving", "waiting"} <= set(sch["doctors"][0])
    assert sch["appointments"], "seeded clinic has appointments today"
    blob = str(sch)
    assert not re.search(r"\b[6-9]\d{9}\b", blob)                       # no phone numbers
    assert all(re.fullmatch(r"\w+( \w\.)?|Walk-in", a["patient"]) for a in sch["appointments"])


def test_stats_and_redteam(client, session):
    session.add_all([m.LLMCall(model="x", tier="lightning", node="intent", cost_usd=0.0001, latency_ms=900),
                     m.LLMCall(model="y", tier="ultra", node="handoff", cost_usd=0.002, latency_ms=2100)])
    session.commit()
    st = client.get("/ops/stats").json()
    assert st["model_calls"] == 2 and st["cost_usd"] == pytest.approx(0.0021)
    assert st["by_tier"]["lightning"]["p50_ms"] == 900 and st["latency_by_node"]["handoff"]["calls"] == 1
    assert st["redteam"]["scripted"]["passed"] == st["redteam"]["scripted"]["total"]


def test_alerts_after_emergency_and_handoff(client):
    client.post("/chat/turn", json={"text": "I have severe chest pain"})
    client.post("/chat/turn", json={"text": "talk to a person"})
    kinds = [a["type"] for a in client.get("/ops/alerts").json()["alerts"]]
    assert "emergency" in kinds and "handoff" in kinds


def test_graph_topology_from_langgraph(client):
    g = client.get("/ops/graph").json()
    assert g["source"] == "langgraph" and {"intent", "guard_in", "flow", "confirm", "respond"} <= set(g["nodes"])
    assert ["intent", "guard_in"] in g["edges"]


def test_staff_check_in_flow_and_live_event(client, session):
    sch = client.get(f"/ops/schedule?day={workday()}").json()
    appt = next(a for a in sch["appointments"] if a["status"] == "confirmed")
    r = client.post(f"/ops/appointments/{appt['id']}/check-in")
    assert r.status_code == 200 and r.json()["status"] == "checked_in"
    assert client.post(f"/ops/appointments/{appt['id']}/check-in").status_code == 409     # already checked in
    assert client.post(f"/ops/appointments/{appt['id']}/start").status_code == 200
    assert client.post(f"/ops/appointments/{appt['id']}/complete").json()["status"] == "completed"
    evs = [e for e in hub.history(ops_channel(client.clinic_id)) if e["type"] == "booking.changed"]
    assert [e["payload"]["action"] for e in evs] == ["check-in", "start", "complete"]
    session.expire_all()
    assert session.get(m.Appointment, appt["id"]).status == "completed"
    audit = session.scalars(select(m.AuditLog.action).where(m.AuditLog.actor == "staff")).all()
    assert audit == ["check-in", "start", "complete"]


def test_staff_token_required_when_set(client, monkeypatch):
    monkeypatch.setenv("STAFF_DASHBOARD_TOKEN", "t0ken")
    assert client.get("/ops/schedule").status_code == 401
    assert client.get("/ops/schedule?token=t0ken").status_code == 200
