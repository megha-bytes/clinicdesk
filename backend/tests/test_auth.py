"""Staff login, roles, clinic scoping, demo sessions, WebSocket tickets and patient thread keys (Day 21)."""
from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app import auth, chat_api
from app import models as m
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.graphs.clinicdesk import build_graph
from app.main import app
from app.seed import seed
from tests.fakes import FakeClient
from tests.keys import KeyedClient, login_as
from tests.test_booking_flow import Fake
from tests.test_staff_api import workday

NEHA = "Book me with Dr. Rao. Neha Rao, 30, 9123456789"


@pytest.fixture
def client(session, monkeypatch):
    info = seed(session)
    fake = Fake({NEHA: {"intent": "book", "doctor": "Dr. Rao", "name": "Neha Rao", "age": 30, "phone": "9123456789"}})
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=MemoryLedger())
    monkeypatch.setattr(chat_api._Runtime, "graph", build_graph(router, chat_api._make_ctx, InMemorySaver()))
    chat_api._hits.clear()
    from app.auth import routes
    routes._hits.clear()
    with KeyedClient(app) as c:
        c.clinic_id = info["clinic_id"]
        yield c


# ------------------------------------------------------------------ passwords and login
def test_password_hashing():
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.verify_password("correct horse battery", h) and not auth.verify_password("wrong", h)
    assert auth.hash_password("correct horse battery") != h                   # salted
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_login_me_logout(client, session):
    auth.create_user(session, clinic_id=client.clinic_id, email="Owner@Clinic.in", name="Dr. Mehta", role="owner",
                     password="correct horse battery")
    session.commit()
    r = client.post("/auth/login", json={"email": "owner@clinic.in", "password": "correct horse battery"})
    assert r.status_code == 200
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert r.json()["user"]["role"] == "owner" and "stats" in r.json()["user"]["permissions"]
    assert client.get("/auth/me", headers=h).json()["name"] == "Dr. Mehta"
    assert session.scalar(select(m.StaffSession.token_hash)) == auth.token_hash(token)   # only the hash is stored
    client.post("/auth/logout", headers=h)
    assert client.get("/auth/me", headers=h).status_code == 401
    assert session.scalars(select(m.AuditLog.action).where(m.AuditLog.entity == "staff_user")).all() == ["login"]


def test_wrong_password_and_unknown_email_look_the_same_then_lock(client, session):
    auth.create_user(session, clinic_id=client.clinic_id, email="r@clinic.in", name="Ravi", role="receptionist",
                     password="correct horse battery")
    session.commit()
    a = client.post("/auth/login", json={"email": "nobody@clinic.in", "password": "x"}).json()
    b = client.post("/auth/login", json={"email": "r@clinic.in", "password": "x"}).json()
    assert a == b == {"detail": "Wrong email or password."}
    for _ in range(4):
        client.post("/auth/login", json={"email": "r@clinic.in", "password": "x"})
    r = client.post("/auth/login", json={"email": "r@clinic.in", "password": "correct horse battery"})
    assert r.status_code == 401 and "Too many attempts" in r.json()["detail"]      # locked even with the right one


def test_login_rate_limit_per_ip(client):
    codes = [client.post("/auth/login", json={"email": "x@y.z", "password": "x"}).status_code for _ in range(11)]
    assert codes[-1] == 429


def test_sessions_expire_when_idle_or_old(client, session):
    login_as(client, session, client.clinic_id, "owner")
    assert client.get("/auth/me").status_code == 200
    row = session.scalar(select(m.StaffSession))
    row.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=3)
    session.commit()
    assert client.get("/auth/me").status_code == 401
    row.last_seen_at = datetime.now(timezone.utc)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    assert client.get("/auth/me").status_code == 401


def test_disabled_user_loses_access(client, session):
    login_as(client, session, client.clinic_id, "receptionist")
    session.scalar(select(m.StaffUser)).active = False
    session.commit()
    assert client.get("/ops/schedule").status_code == 401


# ------------------------------------------------------------------ roles
MATRIX = {  # path → roles allowed
    "/ops/schedule": {"owner", "receptionist", "doctor"},
    "/ops/alerts": {"owner", "receptionist", "doctor"},
    "/ops/stats": {"owner"},
    "/ops/graph": {"owner"},
    "/ops/recent": {"owner"},
}


@pytest.mark.parametrize("role", ["owner", "receptionist", "doctor"])
def test_role_matrix(client, session, role):
    login_as(client, session, client.clinic_id, role)
    for path, allowed in MATRIX.items():
        code = client.get(path).status_code
        assert (code == 200) == (role in allowed), (role, path, code)


def _confirmed(client, doctor_filter=None):
    sch = client.get(f"/ops/schedule?day={workday()}").json()
    return [a for a in sch["appointments"] if a["status"] == "confirmed" and (doctor_filter is None or doctor_filter(a))], sch


def test_doctor_sees_and_runs_only_their_own_column(client, session):
    owner = login_as(client, session, client.clinic_id, "owner")
    all_appts, sch = _confirmed(client)
    rao = next(d["id"] for d in sch["doctors"] if "Rao" in d["name"])
    other = next(a for a in all_appts if a["doctor_id"] != rao)
    mine = next(a for a in all_appts if a["doctor_id"] == rao)
    client.post(f"/ops/appointments/{mine['id']}/check-in", headers=owner)
    client.post(f"/ops/appointments/{other['id']}/check-in", headers=owner)

    client.headers.pop("Authorization")
    login_as(client, session, client.clinic_id, "doctor")
    appts, sch = _confirmed(client, lambda a: True)
    sch = client.get(f"/ops/schedule?day={workday()}").json()
    assert [d["id"] for d in sch["doctors"]] == [rao] and all(a["doctor_id"] == rao for a in sch["appointments"])
    assert client.post(f"/ops/appointments/{mine['id']}/start").status_code == 200
    assert client.post(f"/ops/appointments/{other['id']}/start").status_code == 403    # another doctor's patient
    assert client.post(f"/ops/appointments/{mine['id']}/no-show").status_code == 403  # front desk's job


def test_receptionist_checks_in_but_cannot_run_visits(client, session):
    login_as(client, session, client.clinic_id, "receptionist")
    appt = _confirmed(client)[0][0]
    assert client.post(f"/ops/appointments/{appt['id']}/check-in").status_code == 200
    assert client.post(f"/ops/appointments/{appt['id']}/start").status_code == 403


def test_another_clinics_staff_cannot_touch_this_clinic(client, session):
    login_as(client, session, client.clinic_id, "owner")
    appt = _confirmed(client)[0][0]
    other = m.Clinic(name="Other Clinic")
    session.add(other)
    session.commit()
    client.headers.pop("Authorization")
    login_as(client, session, other.id, "receptionist")
    assert client.post(f"/ops/appointments/{appt['id']}/check-in").status_code == 404
    sch = client.get(f"/ops/schedule?day={workday()}").json()
    assert sch["doctors"] == [] and sch["appointments"] == []


def test_real_sessions_see_full_names_demo_sessions_short_ones(client, session):
    login_as(client, session, client.clinic_id, "receptionist")
    full = [a["patient"] for a in client.get(f"/ops/schedule?day={workday()}").json()["appointments"]]
    client.headers.pop("Authorization")
    login_as(client, session, client.clinic_id, "owner", demo=True)
    short = [a["patient"] for a in client.get(f"/ops/schedule?day={workday()}").json()["appointments"]]
    assert any(not p.endswith(".") and " " in p for p in full) and all(" " not in p or p.endswith(".") for p in short)


# ------------------------------------------------------------------ demo sessions
def test_demo_login_is_off_unless_enabled(client, monkeypatch):
    assert client.get("/auth/config").json() == {"demo_login": False}
    assert client.post("/auth/demo", json={"role": "receptionist"}).status_code == 404
    monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
    from app.settings import get_settings
    get_settings.cache_clear()
    try:
        r = client.post("/auth/demo", json={"role": "doctor"})
        assert r.status_code == 200 and r.json()["user"]["demo"] and r.json()["user"]["role"] == "doctor"
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        sch = client.get(f"/ops/schedule?day={workday()}", headers=h).json()
        assert len(sch["doctors"]) == 1 and "Rao" in sch["doctors"][0]["name"]
        r = client.post("/auth/demo", json={"role": "owner"})
        assert "users.manage" not in r.json()["user"]["permissions"]                 # demo owners can't manage staff
        assert client.post("/auth/login", json={"email": auth.DEMO_USERS["owner"][0], "password": "x"}).status_code == 401
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------------ WebSocket: role filtering
def test_feed_hides_cost_from_non_owners():
    from app.realtime.routes import visible
    p = lambda role, doctor=None: auth.Principal("u", "c", role, "x", doctor, False, "s")  # noqa: E731
    cost = {"type": "cost", "payload": {"cost_usd": 0.001, "tier": "super"}}
    booking = {"type": "booking.changed", "payload": {"doctor": "Dr. Farah Khan", "when": "10:00"}}
    finished = {"type": "run.finished", "payload": {"cost_usd": 0.002, "total_ms": 900}}
    assert visible(cost, p("owner")) == cost
    assert visible(cost, p("receptionist")) is None
    assert "cost_usd" not in visible(finished, p("receptionist"))["payload"]
    assert visible(booking, p("doctor"), "Dr. Ananya Rao") is None
    assert visible(booking, p("doctor"), "Dr. Farah Khan") is not None


# ------------------------------------------------------------------ patients own their conversations
def test_a_thread_id_alone_does_not_open_a_conversation(client):
    body = client.post("/chat/turn", json={"text": NEHA}).json()
    tid, key = body["thread_id"], body["thread_key"]
    assert client.get(f"/chat/{tid}/history").status_code == 200                  # the same browser (has the key)
    client.send_keys = False                                                       # another browser
    assert client.get(f"/chat/{tid}/history").status_code == 404
    assert client.get(f"/chat/{tid}/history", headers={"X-Thread-Key": "guess"}).status_code == 404
    assert client.get(f"/chat/{tid}/pending").status_code == 404
    assert client.post("/chat/answer", json={"thread_id": tid, "approved": True}).status_code == 404
    assert client.post("/chat/turn", json={"text": "cancel it", "thread_id": tid}).status_code == 404
    assert client.get(f"/chat/{tid}/history", headers={"X-Thread-Key": key}).status_code == 200


def test_stream_returns_the_key_in_a_header(client, session):
    with client.stream("POST", "/chat/stream", json={"text": NEHA}) as r:
        key, tid = r.headers["x-thread-key"], r.headers["x-thread-id"]
        list(r.iter_lines())
    conv = session.scalar(select(m.Conversation).where(m.Conversation.thread_id == tid))
    assert conv.thread_key_hash == auth.token_hash(key) and key not in str(conv.__dict__)


def test_security_headers(client):
    h = client.get("/ping").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "default-src 'none'" in h["content-security-policy"]
