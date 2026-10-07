import hashlib
import hmac
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app import models as m
from app.agent.tools import ToolContext, run_tool
from app.main import app
from app.payments.base import MockPaymentProvider, RazorpayProvider, verify_razorpay_signature
from app.realtime.events import EventFactory, EventType, ExecutionEvent
from app.seed import seed

NOW = datetime(2026, 10, 12, 9, 47, tzinfo=ZoneInfo("Asia/Kolkata"))


def _paid_link(session):
    clinic = seed(session, now=NOW)["clinic_id"]
    ctx = ToolContext(session=session, clinic_id=clinic, now=NOW)
    pid = run_tool("register_patient", dict(name="Pay Tester", age=40, phone="9333333333", consent=True), ctx)["patient_id"]
    rao = session.query(m.Doctor).filter_by(name="Dr. Ananya Rao").one()
    slot = run_tool("find_slots", dict(appointment_type="new", doctor_id=rao.id, date="tomorrow"), ctx)["slots"][0]
    held = run_tool("hold_slot", dict(doctor_id=rao.id, start=slot["start"], appointment_type="new"), ctx)
    appt = run_tool("book", dict(hold_id=held["hold_id"], patient_id=pid), ctx)["appointment_id"]
    return run_tool("send_payment_link", dict(appointment_id=appt), ctx)


def test_mock_pay_page_and_confirm(session):
    link = _paid_link(session)
    with TestClient(app) as client:
        page = client.get(f"/pay/{link['payment_id']}")
        assert page.status_code == 200 and "₹500" in page.text and "TEST PAYMENT" in page.text
        done = client.post(f"/pay/{link['payment_id']}/confirm")
        assert done.status_code == 200 and "Paid" in done.text
        assert client.get("/pay/does-not-exist").status_code == 404
    session.expire_all()
    pay = session.get(m.Payment, link["payment_id"])
    assert pay.status == "paid" and pay.paid_at is not None


def test_mock_provider_link():
    link = MockPaymentProvider("https://x.example/").create_link("p1", 500, "fee")
    assert link.url == "https://x.example/pay/p1"


def test_razorpay_requires_test_keys():
    with pytest.raises(ValueError):
        RazorpayProvider("rzp_live_abc", "secret")


def test_razorpay_create_link_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"id": "plink_1", "short_url": "https://rzp.io/i/abc"})

    p = RazorpayProvider("rzp_test_abc", "sec", client=httpx.Client(transport=httpx.MockTransport(handler)))
    link = p.create_link("pay_1", 500, "Consultation", customer_phone="9123456789")
    assert link.url == "https://rzp.io/i/abc" and link.provider_ref == "plink_1"
    assert seen["body"]["amount"] == 50000 and seen["body"]["reference_id"] == "pay_1"
    assert seen["body"]["customer"]["contact"] == "+919123456789"
    assert seen["auth"].startswith("Basic ")


def test_razorpay_signature():
    body = b'{"event":"payment_link.paid"}'
    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert verify_razorpay_signature(body, sig, "whsec")
    assert not verify_razorpay_signature(body, sig, "other")
    assert not verify_razorpay_signature(body, "", "whsec")


def test_razorpay_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec")
    with TestClient(app) as client:
        r = client.post("/webhooks/razorpay", content=b"{}", headers={"X-Razorpay-Signature": "bad"})
    assert r.status_code == 401


def test_events_have_increasing_seq_and_roundtrip():
    f = EventFactory(thread_id="t1")
    a = f.make(EventType.NODE_STARTED, node="intent")
    b = f.make(EventType.TOOL_FINISHED, node="tools", tool="find_slots", ok=True)
    assert (a.seq, b.seq) == (1, 2) and a.run_id == b.run_id
    again = ExecutionEvent.model_validate_json(b.model_dump_json())
    assert again.type == EventType.TOOL_FINISHED and again.payload["tool"] == "find_slots"


def test_reset_demo_disabled_without_token(monkeypatch):
    monkeypatch.delenv("DEMO_RESET_TOKEN", raising=False)
    with TestClient(app) as client:
        assert client.post("/admin/reset-demo").status_code == 404


def test_reset_demo_requires_correct_token(monkeypatch, session):
    monkeypatch.setenv("DEMO_RESET_TOKEN", "s3cret-token")
    with TestClient(app) as client:
        assert client.post("/admin/reset-demo", headers={"X-Demo-Reset-Token": "wrong"}).status_code == 401
        r = client.post("/admin/reset-demo", headers={"X-Demo-Reset-Token": "s3cret-token"})
        assert r.status_code == 200 and r.json()["created"] is True and r.json()["patients"] == 60
        again = client.post("/admin/reset-demo", headers={"X-Demo-Reset-Token": "s3cret-token"})
        assert again.json()["clinic_id"] != r.json()["clinic_id"]       # rebuilt, not duplicated
    assert session.query(m.Clinic).count() == 1
    assert "/admin/reset-demo" not in app.openapi()["paths"]            # hidden from public docs
