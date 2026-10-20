"""Real-time layer (Day 11): graph steps → live events → AG-UI SSE for patients, WebSocket for staff."""
import asyncio
import json

import pytest
from tests.keys import KeyedClient as TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app import chat_api
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.graphs.clinicdesk import build_graph, send
from app.main import app
from app.realtime import runner
from app.realtime.hub import Hub, hub, ops_channel, thread_channel
from app.seed import seed
from tests.fakes import FakeClient
from tests.test_booking_flow import NEHA, NEHA_EXT, NOW, Fake, graph_for

PHONE = "9123456789"


@pytest.fixture(autouse=True)
def clean_hub():
    hub.reset()
    yield
    hub.reset()


@pytest.fixture
def clinic(session):
    return seed(session, now=NOW)["clinic_id"]


def types(events):
    return [e["type"] for e in events]


def test_runner_streams_steps_status_tools_cost_and_reply(session, clinic):
    fake = Fake({"Dr. Rao tomorrow": {"intent": "book", "doctor": "Dr. Rao", "date": "tomorrow"}})
    g, ledger = graph_for(session, clinic, fake)
    seen = []
    out = runner.run(g, "t1", text="Dr. Rao tomorrow", clinic_id=clinic, on_event=seen.append)
    t = types(seen)
    assert t[0] == "run.started" and t[-1] == "run.finished"
    assert ("node.started", "intent") in [(e["type"], e["node"]) for e in seen]
    statuses = [e["payload"]["text"] for e in seen if e["type"] == "status"]
    assert statuses[:2] == ["Reading your message…", "Checking Dr. Ananya Rao's calendar…"]
    tools = [e["payload"]["tool"] for e in seen if e["type"] == "tool.started"]
    assert tools == ["find_slots"]
    assert [e["seq"] for e in seen] == sorted(e["seq"] for e in seen)
    costs = [e for e in seen if e["type"] == "cost"]
    assert len(costs) == len(ledger.records) == 1 and costs[0]["payload"]["tier"] == "lightning"
    assert seen[-1]["payload"]["reply"] == out["reply"] and "Free with Dr. Ananya Rao" in out["reply"]


def test_ops_channel_gets_no_patient_text(session, clinic):
    fake = Fake({NEHA: NEHA_EXT, "1": {"intent": "book", "option": 1}})
    g, _ = graph_for(session, clinic, fake)
    runner.run(g, "t2", text=NEHA, clinic_id=clinic)
    runner.run(g, "t2", approved=True, clinic_id=clinic)
    runner.run(g, "t2", text="1", clinic_id=clinic)
    runner.run(g, "t2", approved=True, clinic_id=clinic)
    ops = hub.history(ops_channel(clinic))
    blob = json.dumps(ops, ensure_ascii=False)
    assert PHONE not in blob and "Neha" not in blob and "reply" not in blob
    booked = [e for e in ops if e["type"] == "booking.changed"]
    assert len(booked) == 1 and booked[0]["payload"]["doctor"] == "Dr. Ananya Rao"
    assert booked[0]["payload"]["status"] == "confirmed" and booked[0]["payload"]["channel"] == "web"
    interrupts = [e["payload"]["kind"] for e in ops if e["type"] == "interrupt"]
    assert interrupts == ["consent", "confirm"]
    # the patient's own channel does carry the reply
    assert any("Free with" in (e["payload"].get("reply") or "") for e in hub.history(thread_channel("t2")))


def test_emergency_and_guardrail_events_reach_staff(session, clinic):
    g, _ = graph_for(session, clinic, Fake({}))
    runner.run(g, "t3", text="I have severe chest pain", clinic_id=clinic)
    ops = types(hub.history(ops_channel(clinic)))
    assert "emergency" in ops and "rail.fired" in ops
    rail = next(e for e in hub.history(ops_channel(clinic)) if e["type"] == "rail.fired")
    assert rail["payload"]["rail"] == "emergency_screen" and rail["payload"]["outcome"] == "escalate"


def test_hindi_status_lines(session, clinic):
    fake = Fake({"कल डॉ. राव": {"intent": "book", "doctor": "Dr. Rao", "date": "tomorrow"}})
    g, _ = graph_for(session, clinic, fake)
    seen = []
    runner.run(g, "t4", text="कल डॉ. राव", clinic_id=clinic, on_event=seen.append)
    assert "Dr. Ananya Rao का कैलेंडर देख रहे हैं…" in [e["payload"]["text"] for e in seen if e["type"] == "status"]


def test_runner_matches_plain_send(session, clinic):
    """Streaming must not change behaviour: same outcome as the plain helper."""
    g1, _ = graph_for(session, clinic, Fake({NEHA: NEHA_EXT}))
    g2, _ = graph_for(session, clinic, Fake({NEHA: NEHA_EXT}))
    a = runner.run(g1, "x", text=NEHA)
    b = send(g2, "y", NEHA)
    assert a["interrupt"] == b["interrupt"] and a["reply"] == b["reply"] is None


# ------------------------------------------------------------------ hub
def test_hub_replay_and_slow_clients():
    h = Hub(history=5)

    async def scenario():
        for i in range(7):
            h.publish("c", {"i": i})
        sub, backlog = h.subscribe("c", after=4)
        assert [e["i"] for e in backlog] == [4, 5, 6]               # cursors 5..7, history keeps last 5
        for i in range(1100):                                       # more than the queue holds
            h.publish("c", {"i": i})
        await asyncio.sleep(0)
        assert sub.queue.qsize() == 1000                            # oldest dropped, publisher never blocked
        h.unsubscribe(sub)
    asyncio.run(scenario())


# ------------------------------------------------------------------ HTTP: AG-UI SSE and the staff WebSocket
@pytest.fixture
def client(session, clinic, monkeypatch):
    fake = Fake({NEHA: NEHA_EXT, "1": {"intent": "book", "option": 1}})
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    chat_api._hits.clear()
    with TestClient(app) as c:
        yield c
    chat_api._hits.clear()


def sse(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def test_agui_stream_for_a_booking(client):
    with client.stream("POST", "/chat/stream", json={"text": NEHA}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        evs = sse(r)
    t = [e["type"] for e in evs]
    assert t[0] == "RUN_STARTED" and t[-1] == "RUN_FINISHED" and "STEP_STARTED" in t and "TOOL_CALL_START" in t
    first_status = [e["value"]["text"] for e in evs if e["type"] == "CUSTOM" and e["name"] == "status"]
    assert "Checking Dr. Ananya Rao's calendar…" in first_status and "Looking up your details…" in first_status
    card = next(e for e in evs if e["type"] == "CUSTOM" and e["name"] == "interrupt")
    assert card["value"]["kind"] == "consent"
    tid = evs[0]["threadId"]

    with client.stream("POST", "/chat/answer/stream", json={"thread_id": tid, "approved": True}) as r:
        evs = sse(r)
    text = "".join(e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "Free with Dr. Ananya Rao" in text
    status = [e["value"]["text"] for e in evs if e["type"] == "CUSTOM" and e["name"] == "status"]
    assert "Saving your details…" in status

    with client.stream("POST", "/chat/stream", json={"text": "1", "thread_id": tid}) as r:
        evs = sse(r)
    assert next(e for e in evs if e["type"] == "CUSTOM" and e["name"] == "interrupt")["value"]["kind"] == "confirm"
    with client.stream("POST", "/chat/answer/stream", json={"thread_id": tid, "approved": True}) as r:
        evs = sse(r)
    assert any(e["type"] == "CUSTOM" and e["name"] == "booking" for e in evs)
    assert evs[-1]["result"]["reply"].startswith("✅ Booked: Dr. Ananya Rao")


def test_stream_refuses_when_a_card_is_pending(client):
    with client.stream("POST", "/chat/stream", json={"text": NEHA}) as r:
        tid = sse(r)[0]["threadId"]
    assert client.post("/chat/stream", json={"text": "hello", "thread_id": tid}).status_code == 409


def test_staff_websocket_replays_and_streams(client, session):
    from tests.keys import login_as
    from app.chat_api import demo_clinic_id
    client.post("/chat/turn", json={"text": "I have severe chest pain"})
    login_as(client, session, demo_clinic_id(), "owner")
    ticket = client.post("/auth/ws-ticket").json()["ticket"]
    with client.websocket_connect(f"/ops/ws?after=0&ticket={ticket}") as ws:
        got = []
        while True:
            ev = ws.receive_json()
            got.append(ev)
            if ev["type"] == "run.finished":
                break
    t = [e["type"] for e in got]
    assert "emergency" in t and "rail.fired" in t and all("reply" not in e["payload"] for e in got)
    assert [e["cursor"] for e in got] == sorted(e["cursor"] for e in got)


def test_staff_feed_needs_a_login(client, session):
    from starlette.websockets import WebSocketDisconnect
    from tests.keys import login_as
    from app.chat_api import demo_clinic_id
    assert client.get("/ops/recent").status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ops/ws?after=0") as ws:
            ws.receive_json()
    login_as(client, session, demo_clinic_id(), "owner")
    assert client.get("/ops/recent").status_code == 200
    ticket = client.post("/auth/ws-ticket").json()["ticket"]
    with client.websocket_connect(f"/ops/ws?ticket={ticket}"):
        pass
    with pytest.raises(WebSocketDisconnect):                      # a ticket works once
        with client.websocket_connect(f"/ops/ws?ticket={ticket}") as ws:
            ws.receive_json()


def test_thread_events_replay(client):
    with client.stream("POST", "/chat/stream", json={"text": NEHA}) as r:
        tid = sse(r)[0]["threadId"]

    class Req:
        headers = {}

        async def is_disconnected(self):
            return True

    async def collect():
        return [x async for x in chat_api._subscribe(thread_channel(tid), 0, Req())]
    lines = asyncio.run(collect())
    evs = [json.loads(x.split("data: ", 1)[1]) for x in lines if "data: " in x]
    assert evs[0]["type"] == "run.started" and evs[-1]["type"] == "run.finished"
    assert lines[0].startswith("id: 1\n")
