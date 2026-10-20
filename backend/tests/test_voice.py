"""Voice: spoken input becomes what the booking flow reads; replies get a version written for the ear."""
import json
from datetime import date

import pytest
from tests.keys import KeyedClient as TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app import chat_api
from app import models as m
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.graphs.booking_flow import render
from app.graphs.clinicdesk import build_graph
from app.main import app
from app.seed import seed
from app.voice.speech import speak_card, to_speech
from app.voice.spoken import normalise, short_answer
from tests.fakes import FakeClient
from tests.test_booking_flow import Fake

TODAY = date(2026, 10, 20)        # a Tuesday


# ------------------------------------------------------------------ spoken input
@pytest.mark.parametrize("said, typed", [
    ("nine eight four five zero one two three four five", "9845012345"),
    ("my number is 98450 double one two three four five", "my number is 98450112345"),
    ("nau aath chaar paanch shunya ek do teen chaar paanch", "9845012345"),
    ("९८४५०१२३४५", "9845012345"),
    ("೯೮೪೫೦೧೨೩೪೫", "9845012345"),
    ("Neha Rao, 30, nine one two three four five six seven eight nine", "Neha Rao, 30, 9123456789"),
    ("option two", "2"), ("two please", "2"), ("दो नंबर", "2"), ("ಎರಡು", "2"), ("one", "1"),
    ("kar do", "kar do"), ("book kar do", "book kar do"), ("the second one", "the second one"),
    ("I want to see Dr Rao at one", "I want to see Dr Rao at one"),
])
def test_spoken_numbers_become_digits(said, typed):
    assert normalise(said, voice=True) == typed


def test_typed_text_only_gets_native_digits():
    assert normalise("option two") == "option two"
    assert normalise("मेरा नंबर ९८४५०१२३४५") == "मेरा नंबर 9845012345"


@pytest.mark.parametrize("said, answer", [
    ("yes", True), ("yes please", True), ("Yes, book it.", True), ("haan ji theek hai", True), ("हाँ जी", True),
    ("ಹೌದು ಮಾಡಿ", True), ("sure go ahead", True), ("okay thank you", True),
    ("no", False), ("no thanks", False), ("nahi rehne do", False), ("ಬೇಡ", False), ("नहीं, अभी नहीं", False),
    ("no, the third one", None), ("yes but tomorrow", None), ("yes 5pm", None), ("yes I want Dr Rao instead", None),
    ("what", None),
])
def test_short_spoken_answers(said, answer):
    assert short_answer(said) is answer


# ------------------------------------------------------------------ replies for the ear
OPTS = {"options": [{"doctor": "Dr. Ananya Rao", "label": "Wed 21 Oct, 9:30 AM", "short": "Wed 21 Oct, 9:30 AM", "fee_inr": 500},
                    {"doctor": "Dr. Ananya Rao", "label": "Thu 22 Oct, 4:10 PM", "short": "Thu 22 Oct, 4:10 PM", "fee_inr": 500}]}


def test_options_read_aloud():
    en = to_speech(render(OPTS, "en"), "en", TODAY)
    assert en == ("Free with Dr. Ananya Rao, 500 rupees: Option 1: tomorrow, 9:30 AM. "
                  "Option 2: Thursday 22 October, 4:10 PM. Say the option number.")
    hi = to_speech(render(OPTS, "hi"), "hi", TODAY)
    assert "कल, सुबह 9:30" in hi and "गुरुवार, 22 अक्टूबर, शाम 4:10" in hi and "500 रुपये" in hi
    assert "विकल्प का नंबर बोलिए" in hi and "₹" not in hi and "Wed" not in hi
    kn = to_speech(render(OPTS, "kn"), "kn", TODAY)
    assert "ನಾಳೆ, ಬೆಳಿಗ್ಗೆ 9:30" in kn and "ಆಯ್ಕೆ 2" in kn and "ಸಂಜೆ 4:10" in kn


def test_booked_reply_has_no_emoji_or_link():
    say = {"booked": {"doctor": "Dr. Farah Khan", "when": "Tue 20 Oct, token 4", "fee_inr": 400, "pay_url": "https://pay.example/x"}}
    s = to_speech(render(say, "en"), "en", TODAY)
    assert s == "Booked: Dr. Farah Khan, today, token number 4, 400 rupees. You can pay with the link on your screen, or at the clinic."
    assert "https" not in to_speech(render(say, "kn"), "kn", TODAY)


def test_cards_end_with_yes_or_no():
    card = {"kind": "confirm", "action": "book", "summary": "Book …",
            "details": {"action": "book", "doctor": "Dr. Ananya Rao", "when": "Wed 21 Oct, 9:30 AM", "fee_inr": 500}}
    assert speak_card(card, "en", TODAY) == "Shall I book Dr. Ananya Rao, tomorrow, 9:30 AM, 500 rupees? Say yes or no."
    assert speak_card(card, "hi", TODAY).endswith("बुक कर दूँ? हाँ या नहीं बोलिए।")
    consent = {"kind": "consent", "action": "register_patient", "summary": "We'll save your name… OK?"}
    assert speak_card(consent, "en").endswith("OK? Say yes or no.")


# ------------------------------------------------------------------ a whole call over HTTP
SPOKEN = "Book me with Dr. Rao. Neha Rao, 30, nine one two three four five six seven eight nine"
TYPED = "Book me with Dr. Rao. Neha Rao, 30, 9123456789"


@pytest.fixture
def client(session, monkeypatch):
    seed(session)
    fake = Fake({TYPED: {"intent": "book", "doctor": "Dr. Rao", "name": "Neha Rao", "age": 30, "phone": "9123456789"},
                 "1": {"intent": "book", "option": 1}})
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    chat_api._hits.clear()
    with TestClient(app) as c:
        yield c
    chat_api._hits.clear()


def _sse(client, path, body) -> dict:
    with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200
        evs = [json.loads(x[6:]) for x in r.iter_lines() if x.startswith("data: ")]
    return {"thread_id": evs[0]["threadId"], **evs[-1]["result"]}


def _newest_booking(session) -> m.Appointment:
    session.expire_all()
    return session.scalars(select(m.Appointment).where(m.Appointment.status == "confirmed")
                           .order_by(m.Appointment.created_at.desc())).first()


def test_voice_call_books_with_spoken_answers(client, session):
    v = {"channel": "voice"}
    out = _sse(client, "/chat/stream", {"text": SPOKEN, **v})
    tid = out["thread_id"]
    assert out["interrupt"]["kind"] == "consent" and out["speech"].endswith("Say yes or no.")

    out = _sse(client, "/chat/answer/stream", {"thread_id": tid, "text": "yes please", **v})
    assert "Option 1:" in out["speech"] and out["speech"].endswith("Say the option number.") and "₹" not in out["speech"]

    out = _sse(client, "/chat/stream", {"thread_id": tid, "text": "option one", **v})
    assert out["interrupt"]["kind"] == "confirm" and out["speech"].startswith("Shall I book Dr. Ananya Rao")

    out = _sse(client, "/chat/answer/stream", {"thread_id": tid, "text": "haan ji", **v})
    assert out["speech"].startswith("Booked: Dr. Ananya Rao") and "✅" not in out["speech"]

    session.expire_all()
    conv = session.scalar(select(m.Conversation).where(m.Conversation.thread_id == tid))
    assert conv.channel == "voice"
    assert _newest_booking(session).source == "voice"              # the dashboard shows it with a phone icon


def test_chat_bookings_stay_web(client, session):
    out = client.post("/chat/turn", json={"text": TYPED}).json()
    tid = out["thread_id"]
    client.post("/chat/answer", json={"thread_id": tid, "approved": True})
    client.post("/chat/turn", json={"text": "1", "thread_id": tid})
    out = client.post("/chat/answer", json={"thread_id": tid, "approved": True}).json()
    assert out["reply"].startswith("✅ Booked") and out["speech"].startswith("Booked")
    assert _newest_booking(session).source == "web"


def test_spoken_card_number_is_still_refused(client):
    said = "my card is four five six seven eight nine zero one two three four five six seven eight nine"
    assert normalise(said, voice=True) == "my card is 4567890123456789"
    out = client.post("/chat/turn", json={"text": said, "channel": "voice"}).json()
    assert out["ui"].get("refused") == "payment" and "4567" not in (out["speech"] or "")
