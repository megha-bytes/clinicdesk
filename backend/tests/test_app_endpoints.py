"""Endpoints the patient app needs (Day 12): clinic info, history after reload, card details, UI hints,
language picker hint, typed card answers over HTTP."""
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app import chat_api
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.graphs.clinicdesk import build_graph
from app.main import app
from app.seed import seed
from tests.fakes import FakeClient
from tests.test_booking_flow import NEHA, NEHA_EXT, NOW, Fake


@pytest.fixture
def client(session, monkeypatch):
    seed(session, now=NOW)
    fake = Fake({NEHA: NEHA_EXT, "1": {"intent": "book", "option": 1}, "Kal Dr. Rao": {"intent": "book", "doctor": "Dr. Rao"},
                 "I have severe chest pain": {"intent": "other"}})
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    chat_api._hits.clear()
    with TestClient(app) as c:
        yield c
    chat_api._hits.clear()


def test_clinic_info(client):
    info = client.get("/chat/clinic").json()
    assert info["name"] == "Sunrise Family Clinic" and info["emergency_numbers"]["ambulance"] == "108"
    rao = next(d for d in info["doctors"] if d["name"] == "Dr. Ananya Rao")
    assert rao["hours"]["mon"] == [["09:00", "13:00"], ["17:00", "20:00"]]


def test_ui_hints_card_details_and_history(client):
    body = client.post("/chat/turn", json={"text": NEHA}).json()
    tid = body["thread_id"]
    body = client.post("/chat/answer", json={"thread_id": tid, "approved": True}).json()
    ui = body["ui"]
    assert ui["options_title"] == "Free with Dr. Ananya Rao (₹500):"
    assert [o["n"] for o in ui["options"]] == [1, 2, 3] and ", " in ui["options"][0]["when"]
    assert "1." not in ui["text"] and "Reply with a number" not in ui["text"]          # the app draws the buttons
    assert f"1. {ui['options'][0]['when']}" in body["reply"]                            # voice/chat get the full text

    body = client.post("/chat/turn", json={"text": "1", "thread_id": tid}).json()
    d = body["interrupt"]["details"]
    assert d["doctor"] == "Dr. Ananya Rao" and d["fee_inr"] == 500 and d["type"] == "New consultation"
    body = client.post("/chat/answer", json={"thread_id": tid, "approved": True}).json()
    b = body["ui"]["booked"]
    assert b["doctor"] == "Dr. Ananya Rao" and b["fee_inr"] == 500 and b["pay_url"] and body["ui"]["text"] == ""

    h = client.get(f"/chat/{tid}/history").json()
    roles = [m["role"] for m in h["messages"]]
    assert roles[0] == "user" and "assistant" in roles and h["interrupt"] is None


def test_history_shows_a_waiting_card(client):
    tid = client.post("/chat/turn", json={"text": NEHA}).json()["thread_id"]
    assert client.get(f"/chat/{tid}/history").json()["interrupt"]["kind"] == "consent"


def test_language_picker_hint(client):
    body = client.post("/chat/turn", json={"text": "Kal Dr. Rao", "language": "kn"}).json()
    assert body["language"] == "hi"                    # "kal" is Hindi: the message wins over the picker
    body = client.post("/chat/turn", json={"text": "I have severe chest pain", "language": "kn"}).json()
    assert body["language"] == "en" or "ತುರ್ತು" in body["reply"]
    assert client.post("/chat/turn", json={"text": "hi", "language": "fr"}).status_code == 422


def test_picker_language_used_for_neutral_messages(client):
    body = client.post("/chat/turn", json={"text": "I have severe chest pain", "language": "hi"}).json()
    assert body["ui"]["emergency"]["numbers"]["ambulance"] == "108"


def test_typed_answer_over_http(client):
    tid = client.post("/chat/turn", json={"text": NEHA}).json()["thread_id"]
    body = client.post("/chat/answer", json={"thread_id": tid, "text": "yes"}).json()
    assert "options" in body["ui"]
    assert client.post("/chat/answer", json={"thread_id": tid}).status_code in (409, 422)
