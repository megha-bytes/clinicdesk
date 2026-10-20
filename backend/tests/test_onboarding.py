"""Tavily onboarding: pages → Nemotron draft → each value marked found / check; owners only."""
import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app import chat_api
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.graphs.clinicdesk import build_graph
from app.main import app
from app.onboarding import service
from app.seed import seed
from tests.fakes import FakeClient, msg
from tests.keys import KeyedClient, login_as

PAGE = """# Lotus Skin & Family Clinic
12, 3rd Cross, Indiranagar, Bengaluru. Call 98450 11223.
Our doctors: Dr. Kavya Menon (Dermatologist) Mon–Sat 10:00–13:00, consultation ₹600.
Dr. Arjun Pai, General physician, evenings 17:00–20:00, fee Rs. 400. Walk-in tokens from 5 pm."""
DRAFT = {"clinic_name": "Lotus Skin & Family Clinic", "address": "12, 3rd Cross, Indiranagar, Bengaluru", "phone": "98450 11223",
         "languages": [], "booking_style": "token",
         "doctors": [{"name": "Dr. Kavya Menon", "specialty": "Dermatologist", "days": "Mon–Sat", "hours": "10:00–13:00", "fee_inr": 600},
                     {"name": "Dr. Arjun Pai", "specialty": "General physician", "days": None, "hours": "17:00–20:00", "fee_inr": 450}],
         "services": [], "clinic_hours": None}


def tavily_transport(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer tvly-test"
    body = json.loads(request.content)
    if request.url.path == "/extract":
        assert body["urls"] == ["https://lotus.example/"]
        return httpx.Response(200, json={"results": [{"url": "https://lotus.example/", "raw_content": PAGE}], "failed_results": []})
    return httpx.Response(200, json={"results": [{"url": "https://lotus.example/", "title": "Lotus", "raw_content": PAGE}]})


def test_review_marks_what_the_page_does_not_say():
    notes = {n["field"]: n["status"] for n in service.review(DRAFT, PAGE)}
    assert notes["doctors[0].name"] == notes["doctors[0].fee_inr"] == notes["phone"] == notes["address"] == "found"
    assert notes["doctors[1].fee_inr"] == "check"                         # the page says 400, the draft says 450


def test_gather_uses_tavily_extract_and_search(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    c = httpx.Client(transport=httpx.MockTransport(tavily_transport))
    assert service.gather("https://lotus.example/", None, c)[0]["text"] == PAGE
    assert service.gather(None, "Lotus clinic Indiranagar", c)[0]["url"] == "https://lotus.example/"


def test_no_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(service.OnboardingError, match="TAVILY_API_KEY"):
        service.gather("https://lotus.example/", None)


@pytest.fixture
def client(session, monkeypatch):
    info = seed(session)
    router = ModelRouter(FakeClient(lambda kw: msg(json.dumps(DRAFT))), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "router", router)
    monkeypatch.setattr(chat_api._Runtime, "checkpointer", InMemorySaver())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    monkeypatch.setattr(service, "gather", lambda url, name, client=None: [{"url": "https://lotus.example/", "title": "Lotus", "text": PAGE}])
    with KeyedClient(app) as c:
        c.clinic_id = info["clinic_id"]
        yield c


def test_owner_drafts_reviews_and_approves(client, session):
    login_as(client, session, client.clinic_id, "owner")
    r = client.post("/ops/onboarding/draft", json={"url": "https://lotus.example/"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["draft"]["doctors"][0]["name"] == "Dr. Kavya Menon" and d["sources"][0]["url"] == "https://lotus.example/"
    assert {"field": "doctors[1].fee_inr", "value": 450, "status": "check"} in d["review"]
    assert client.get("/ops/onboarding/drafts").json()["drafts"][0]["id"] == d["id"]
    assert client.post(f"/ops/onboarding/drafts/{d['id']}/approve").json()["status"] == "approved"


def test_only_owners_and_valid_input(client, session):
    login_as(client, session, client.clinic_id, "receptionist")
    assert client.post("/ops/onboarding/draft", json={"url": "https://lotus.example/"}).status_code == 403
    client.headers.pop("Authorization")
    login_as(client, session, client.clinic_id, "owner")
    assert client.post("/ops/onboarding/draft", json={}).status_code == 422
    assert client.post("/ops/onboarding/draft", json={"url": "javascript:alert(1)"}).status_code == 422
