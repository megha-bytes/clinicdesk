"""Clinic onboarding endpoints (owners).

POST /ops/onboarding/draft              {url} or {name}  → Tavily reads the clinic's pages → Nemotron draft + review
GET  /ops/onboarding/drafts                              → the clinic's recent drafts
POST /ops/onboarding/drafts/{id}/{approve|reject}        → the owner's decision (audited)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from app import models as m
from app.auth import Principal, require
from app.db import SessionLocal
from app.onboarding import service

router = APIRouter(prefix="/ops/onboarding", tags=["onboarding"])
DEMO_DAILY_LIMIT = 15


class DraftIn(BaseModel):
    url: str | None = Field(None, max_length=500, pattern=r"^https?://[^\s]+$")
    name: str | None = Field(None, min_length=3, max_length=120)

    @model_validator(mode="after")
    def one_of(self):
        if not (self.url or self.name):
            raise ValueError("Give the clinic's website (url) or its name and area (name).")
        return self


def _out(d: m.OnboardingDraft) -> dict:
    return {"id": d.id, "source": d.source, "source_value": d.source_value, "sources": d.tavily_sources,
            "draft": d.extracted_json, "review": d.review_notes, "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None}


@router.post("/draft")
def create_draft(body: DraftIn, user: Principal = Depends(require("onboarding"))) -> dict:
    with SessionLocal() as s:
        since = datetime.now(timezone.utc) - timedelta(days=1)
        n = s.scalar(select(func.count()).select_from(m.OnboardingDraft).where(
            m.OnboardingDraft.clinic_id == user.clinic_id, m.OnboardingDraft.created_at >= since)) or 0
        if user.demo and n >= DEMO_DAILY_LIMIT:
            raise HTTPException(429, "The demo has made enough drafts today. Try again tomorrow.")
    from app.chat_api import _Runtime, spend_ok
    if not spend_ok():
        raise HTTPException(503, "The demo has reached today's usage limit.")
    router_, _ = _Runtime.parts()
    try:
        pages = service.gather(body.url, body.name)
        result = service.build_draft(router_, pages)
    except service.OnboardingError as e:
        raise HTTPException(503 if "configured" in str(e) else 502, str(e)) from e
    with SessionLocal() as s:
        d = m.OnboardingDraft(clinic_id=user.clinic_id, source="url" if body.url else "name",
                              source_value=body.url or body.name, tavily_sources=[{"url": p["url"], "title": p["title"]} for p in pages],
                              extracted_json=result["draft"], review_notes=result["review"], status="draft")
        s.add(d)
        s.add(m.AuditLog(actor="staff", action="onboarding_draft", entity="onboarding_draft", entity_id=d.id,
                         after={"by": user.user_id, "source": d.source}))
        s.commit()
        return _out(d)


@router.get("/drafts")
def list_drafts(user: Principal = Depends(require("onboarding"))) -> dict:
    with SessionLocal() as s:
        rows = s.scalars(select(m.OnboardingDraft).where(m.OnboardingDraft.clinic_id == user.clinic_id)
                         .order_by(m.OnboardingDraft.created_at.desc()).limit(10)).all()
        return {"drafts": [_out(d) for d in rows]}


@router.post("/drafts/{draft_id}/{decision}")
def decide(draft_id: str, decision: Literal["approve", "reject"], user: Principal = Depends(require("onboarding"))) -> dict:
    with SessionLocal() as s:
        d = s.get(m.OnboardingDraft, draft_id)
        if d is None or d.clinic_id != user.clinic_id:
            raise HTTPException(404, "Unknown draft.")
        d.status = "approved" if decision == "approve" else "rejected"
        d.reviewed_by = user.name[:100]
        s.add(m.AuditLog(actor="staff", action=f"onboarding_{decision}", entity="onboarding_draft", entity_id=d.id,
                         after={"by": user.user_id}))
        s.commit()
        return _out(d)
