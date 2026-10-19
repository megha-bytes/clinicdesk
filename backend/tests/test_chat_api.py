"""HTTP chat endpoints with a fake model."""
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
from app.seed import seed
from tests.fakes import FakeClient
from tests.test_booking_flow import Fake


@pytest.fixture
def client(session, monkeypatch):
    seed(session)
    fake = Fake({"Book me with Dr. Rao. Neha Rao, 30, 9123456789":
                 {"intent": "book", "doctor": "Dr. Rao", "name": "Neha Rao", "age": 30, "phone": "9123456789"},
                 "1": {"intent": "book", "option": 1}})
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    chat_api._hits.clear()
    with TestClient(app) as c:
        yield c
    chat_api._hits.clear()


def test_booking_over_http(client, session):
    r = client.post("/chat/turn", json={"text": "Book me with Dr. Rao. Neha Rao, 30, 9123456789"})
    body = r.json()
    assert r.status_code == 200 and body["interrupt"]["kind"] == "consent" and body["reply"] is None
    tid = body["thread_id"]

    assert client.get(f"/chat/{tid}/pending").json()["interrupt"]["kind"] == "consent"
    assert client.post("/chat/turn", json={"text": "hello?", "thread_id": tid}).status_code == 409   # answer first

    body = client.post("/chat/answer", json={"thread_id": tid, "approved": True}).json()
    assert body["interrupt"] is None and "1. Dr. Ananya Rao" in body["reply"]
    body = client.post("/chat/turn", json={"text": "1", "thread_id": tid}).json()
    assert body["interrupt"]["kind"] == "confirm" and "₹500" in body["interrupt"]["summary"]
    body = client.post("/chat/answer", json={"thread_id": tid, "approved": True}).json()
    assert body["interrupt"] is None and "✅ Booked: Dr. Ananya Rao" in body["reply"]
    assert client.post("/chat/answer", json={"thread_id": tid, "approved": True}).status_code == 409
    conv = session.scalar(select(m.Conversation).where(m.Conversation.thread_id == tid))
    assert conv is not None and conv.channel == "web"


def test_unknown_thread_is_404(client):
    assert client.post("/chat/turn", json={"text": "hi", "thread_id": "nope"}).status_code == 404
    assert client.get("/chat/nope/pending").status_code == 404


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(chat_api, "RATE_LIMIT", 2)
    codes = [client.post("/chat/turn", json={"text": "hi"}).status_code for _ in range(3)]
    assert codes[:2] != [429, 429] and codes[2] == 429


def test_daily_spend_cap(client, session, monkeypatch):
    monkeypatch.setenv("DAILY_SPEND_CAP_USD", "0.01")
    from app.settings import get_settings
    get_settings.cache_clear()
    session.add(m.LLMCall(model="x", tier="super", cost_usd=0.02, at=datetime.now(timezone.utc)))
    session.commit()
    try:
        r = client.post("/chat/turn", json={"text": "hi"})
        assert r.status_code == 503 and "usage limit" in r.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_validation(client):
    assert client.post("/chat/turn", json={"text": ""}).status_code == 422
    assert client.post("/chat/turn", json={"text": "x" * 1001}).status_code == 422
