"""Payment-link providers. The agent never sees card numbers, UPI PINs or OTPs (plan §3.4):
it only asks a provider for a link, and the amount always comes from the clinic's fee table."""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class PaymentLink:
    url: str
    provider_ref: str


class PaymentProvider(Protocol):
    name: str

    def create_link(self, payment_id: str, amount_inr: int, description: str,
                    customer_phone: str | None = None) -> PaymentLink: ...


class MockPaymentProvider:
    """Demo provider: the link opens our own /pay/<id> page, where a test button marks it paid."""
    name = "mock"

    def __init__(self, public_base_url: str) -> None:
        self.base = public_base_url.rstrip("/")

    def create_link(self, payment_id, amount_inr, description, customer_phone=None) -> PaymentLink:
        return PaymentLink(url=f"{self.base}/pay/{payment_id}", provider_ref=f"mock_{payment_id}")


class RazorpayProvider:
    """Razorpay Payment Links (use TEST keys only). Amount is sent in paise."""
    name = "razorpay"
    API = "https://api.razorpay.com/v1/payment_links"

    def __init__(self, key_id: str, key_secret: str, client: httpx.Client | None = None) -> None:
        if not key_id.startswith("rzp_test_"):
            raise ValueError("Only Razorpay TEST keys (rzp_test_…) are allowed in this project")
        self.key_id, self.key_secret = key_id, key_secret
        self.client = client or httpx.Client(timeout=15)

    def create_link(self, payment_id, amount_inr, description, customer_phone=None) -> PaymentLink:
        body = {"amount": amount_inr * 100, "currency": "INR", "description": description[:2048],
                "reference_id": payment_id, "notify": {"sms": False, "email": False}}
        if customer_phone:
            body["customer"] = {"contact": f"+91{customer_phone}"}
        auth = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        r = self.client.post(self.API, json=body, headers={"Authorization": f"Basic {auth}"})
        r.raise_for_status()
        data = r.json()
        return PaymentLink(url=data["short_url"], provider_ref=data["id"])


def verify_razorpay_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    """Razorpay webhooks: HMAC-SHA256 of the raw body with the webhook secret (X-Razorpay-Signature)."""
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def get_provider() -> PaymentProvider:
    from app.settings import get_settings

    s = get_settings()
    if s.payment_provider == "razorpay":
        key_id, secret = s.secret("RAZORPAY_KEY_ID"), s.secret("RAZORPAY_KEY_SECRET")
        if not key_id or not secret:
            raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set")
        return RazorpayProvider(key_id.get_secret_value(), secret.get_secret_value())
    return MockPaymentProvider(s.public_base_url)
