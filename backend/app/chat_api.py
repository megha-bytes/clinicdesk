"""Chat endpoints (JSON for now; the streaming AG-UI version arrives with the real-time layer).

POST /chat/turn     {"text", "thread_id"?}        → {"thread_id", "reply"} or {"thread_id", "interrupt"}
POST /chat/answer   {"thread_id", "approved"}      → the patient's yes/no to a consent or confirmation card
GET  /chat/{thread_id}/pending                     → the question a conversation is waiting on, if any

Protected against runaway spend: a per-IP rate limit and a daily model-spend cap (from the cost ledger).
Uses the demo clinic. Try it from /docs.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, time as dtime, timezone
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import models as m
from app.agent.tools import ToolContext
from app.db import SessionLocal
from app.graphs.clinicdesk import answer, build_graph, pending_interrupt, send
from app.seed import CLINIC_NAME
from app.settings import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])

RATE_LIMIT = 30          # patient turns per IP …
RATE_WINDOW_S = 600      # … per 10 minutes
_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


class _Runtime:
    """Lazily built graph + model router (overridable in tests)."""
    graph = None
    router = None

    @classmethod
    def get(cls):
        if cls.graph is None:
            from app.agent.models import default_router
            from app.graphs.checkpointer import make_checkpointer

            try:
                cls.router = cls.router or default_router()
            except RuntimeError as e:
                raise HTTPException(503, "The assistant isn't configured yet.") from e
            cls.graph = build_graph(cls.router, _make_ctx, make_checkpointer(get_settings().database_url))
        return cls.graph


def _demo_clinic_id(session) -> str:
    cid = session.scalar(select(m.Clinic.id).where(m.Clinic.name == CLINIC_NAME))
    if cid is None:
        raise HTTPException(503, "The demo clinic hasn't been loaded yet.")
    return cid


def _make_ctx() -> ToolContext:
    session = SessionLocal()                     # one short-lived session per graph step (closed by the graph)
    try:
        return ToolContext(session=session, clinic_id=_demo_clinic_id(session), owns_session=True)
    except Exception:
        session.close()
        raise


def _guard(request: Request) -> None:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    now = time.monotonic()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            raise HTTPException(429, "Too many messages. Please wait a few minutes.")
        q.append(now)
    with SessionLocal() as s:
        start = datetime.combine(datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc)
        spent = s.scalar(select(func.coalesce(func.sum(m.LLMCall.cost_usd), 0.0)).where(m.LLMCall.at >= start))
    if spent >= get_settings().daily_spend_cap_usd:
        raise HTTPException(503, "The demo has reached today's usage limit. Please try again tomorrow.")


class TurnIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    thread_id: str | None = None


class AnswerIn(BaseModel):
    thread_id: str
    approved: bool


def _conversation(thread_id: str | None) -> tuple[str, str]:
    with SessionLocal() as s:
        if thread_id:
            conv = s.scalar(select(m.Conversation).where(m.Conversation.thread_id == thread_id))
            if conv is None:
                raise HTTPException(404, "Unknown conversation.")
            return conv.thread_id, conv.id
        conv = m.Conversation(clinic_id=_demo_clinic_id(s), channel="web")
        s.add(conv)
        s.commit()
        return conv.thread_id, conv.id


def _public(thread_id: str, out: dict) -> dict:
    return {"thread_id": thread_id, "reply": out["reply"], "interrupt": out["interrupt"],
            "intent": out["intent"], "language": out["language"], "tools": out["tools"]}


@router.post("/turn", dependencies=[Depends(_guard)])
def chat_turn(body: TurnIn) -> dict:
    graph = _Runtime.get()
    thread_id, conv_id = _conversation(body.thread_id)
    if pending_interrupt(graph, thread_id):
        raise HTTPException(409, "Please answer the pending question first (POST /chat/answer).")
    return _public(thread_id, send(graph, thread_id, body.text, conversation_id=conv_id))


@router.post("/answer", dependencies=[Depends(_guard)])
def chat_answer(body: AnswerIn) -> dict:
    graph = _Runtime.get()
    _conversation(body.thread_id)
    if not pending_interrupt(graph, body.thread_id):
        raise HTTPException(409, "There's no question waiting for an answer.")
    return _public(body.thread_id, answer(graph, body.thread_id, body.approved))


@router.get("/{thread_id}/pending")
def chat_pending(thread_id: str) -> dict:
    graph = _Runtime.get()
    _conversation(thread_id)
    return {"thread_id": thread_id, "interrupt": pending_interrupt(graph, thread_id)}
