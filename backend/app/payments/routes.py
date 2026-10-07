"""Payment endpoints.

- GET  /pay/{id}          demo payment page for the mock provider (clearly labelled TEST)
- POST /pay/{id}/confirm  the page's "Pay (test)" button: marks the payment paid
- POST /webhooks/razorpay Razorpay test-mode webhook (signature-verified)
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.db import get_session
from app.payments.base import verify_razorpay_signature
from app.settings import get_settings

router = APIRouter()

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f6f7f9;margin:0;display:grid;place-items:center;min-height:100vh}}
.card{{background:#fff;border-radius:16px;padding:28px;max-width:360px;width:calc(100% - 32px);box-shadow:0 4px 24px #0001}}
.amt{{font-size:2.2rem;font-weight:700;margin:8px 0}} .muted{{color:#667}} .test{{background:#fff4d6;color:#7a5200;
padding:4px 10px;border-radius:99px;font-size:.8rem;font-weight:600}} button{{width:100%;padding:14px;border:0;
border-radius:12px;background:#1f6feb;color:#fff;font-size:1rem;font-weight:600;margin-top:20px;cursor:pointer}}
.ok{{color:#137333;font-weight:700;font-size:1.2rem}}</style></head><body><div class="card">
<span class="test">TEST PAYMENT · no real money</span>{body}</div></body></html>"""


def _payment(session: Session, payment_id: str) -> m.Payment:
    pay = session.get(m.Payment, payment_id)
    if pay is None:
        raise HTTPException(404, "Payment not found")
    return pay


@router.get("/pay/{payment_id}", response_class=HTMLResponse)
def pay_page(payment_id: str, session: Session = Depends(get_session)) -> str:
    pay = _payment(session, payment_id)
    appt = session.get(m.Appointment, pay.appointment_id)
    doctor = session.get(m.Doctor, appt.doctor_id) if appt else None
    clinic = session.get(m.Clinic, appt.clinic_id) if appt else None
    who = html.escape(f"{clinic.name if clinic else ''} · {doctor.name if doctor else ''}")
    if pay.status == "paid":
        body = f'<p class="muted">{who}</p><div class="amt">₹{pay.amount_inr}</div><p class="ok">✓ Paid</p>'
    elif pay.status != "pending":
        body = f'<p class="muted">{who}</p><p>This payment link has {html.escape(pay.status)}.</p>'
    else:
        body = (f'<p class="muted">{who}</p><div class="amt">₹{pay.amount_inr}</div>'
                f'<p class="muted">Consultation fee</p>'
                f'<form method="post" action="/pay/{html.escape(pay.id)}/confirm"><button>Pay ₹{pay.amount_inr} (test)</button></form>')
    return _PAGE.format(title="Pay consultation fee", body=body)


@router.post("/pay/{payment_id}/confirm", response_class=HTMLResponse)
def pay_confirm(payment_id: str, session: Session = Depends(get_session)) -> str:
    pay = _payment(session, payment_id)
    if pay.provider != "mock":
        raise HTTPException(400, "Only mock payments can be confirmed here")
    if pay.status == "pending":
        pay.status, pay.paid_at = "paid", datetime.now(timezone.utc)
        session.add(m.AuditLog(actor="staff", action="payment_paid", entity="payment", entity_id=pay.id,
                               after={"amount_inr": pay.amount_inr, "provider": "mock"}))
        session.commit()
    return pay_page(payment_id, session)


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(""),
                           session: Session = Depends(get_session)) -> dict:
    secret = get_settings().secret("RAZORPAY_WEBHOOK_SECRET")
    raw = await request.body()
    if secret is None or not verify_razorpay_signature(raw, x_razorpay_signature, secret.get_secret_value()):
        raise HTTPException(401, "Invalid signature")
    event = json.loads(raw)
    if event.get("event") == "payment_link.paid":
        ref = event["payload"]["payment_link"]["entity"].get("reference_id")
        pay = session.scalar(select(m.Payment).where(m.Payment.id == ref))
        if pay and pay.status == "pending":
            pay.status, pay.paid_at = "paid", datetime.now(timezone.utc)
            session.add(m.AuditLog(actor="job", action="payment_paid", entity="payment", entity_id=pay.id,
                                   after={"provider": "razorpay"}))
            session.commit()
    return {"ok": True}
