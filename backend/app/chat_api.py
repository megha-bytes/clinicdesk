"""Chat endpoints.

POST /chat/turn            {"text", "thread_id"?}   → JSON {"thread_id", "reply" | "interrupt", ...}
POST /chat/answer          {"thread_id", "approved"} → the patient's yes/no to a consent or confirmation card
POST /chat/stream          {"text", "thread_id"?}   → the same turn as a live AG-UI event stream (SSE)
POST /chat/answer/stream   {"thread_id", "approved"} → the same answer as a live AG-UI event stream (SSE)
GET  /chat/{thread_id}/pending                      → the question a conversation is waiting on, if any
GET  /chat/{thread_id}/events?after=N               → reconnect: replay + live events for a conversation (SSE)

Every run also feeds the staff dashboard's live channel (see app/realtime/routes.py).

Protected against runaway spend: a per-IP rate limit and a daily model-spend cap (from the cost ledger).
Uses the demo clinic. Try it from /docs.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, time as dtime, timezone
from threading import Lock

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import models as m
from app.agent.tools import ToolContext
from app.db import SessionLocal
from app.graphs.clinicdesk import build_graph, pending_interrupt
from app.realtime import runner
from app.realtime.agui import encode as agui_encode
from app.realtime.hub import hub, thread_channel
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


def demo_clinic_id() -> str:
    with SessionLocal() as s:
        return _demo_clinic_id(s)


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
    out = runner.run(graph, thread_id, text=body.text, conversation_id=conv_id, clinic_id=demo_clinic_id())
    return _public(thread_id, out)


@router.post("/answer", dependencies=[Depends(_guard)])
def chat_answer(body: AnswerIn) -> dict:
    graph = _Runtime.get()
    _conversation(body.thread_id)
    if not pending_interrupt(graph, body.thread_id):
        raise HTTPException(409, "There's no question waiting for an answer.")
    out = runner.run(graph, body.thread_id, approved=body.approved, clinic_id=demo_clinic_id())
    return _public(body.thread_id, out)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _stream_run(**kwargs) -> StreamingResponse:
    """Run the graph in a worker thread and stream its events as AG-UI server-sent events."""
    async def gen():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        done = object()

        def on_event(ev: dict) -> None:
            loop.call_soon_threadsafe(q.put_nowait, ev)

        def work() -> None:
            try:
                runner.run(on_event=on_event, **kwargs)
            except Exception:  # noqa: BLE001 - already reported as a run.error event
                pass
            finally:
                loop.call_soon_threadsafe(q.put_nowait, done)

        task = loop.run_in_executor(None, work)
        while True:
            ev = await q.get()
            if ev is done:
                break
            yield agui_encode(ev)
        await task
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/stream", dependencies=[Depends(_guard)])
def chat_stream(body: TurnIn) -> StreamingResponse:
    graph = _Runtime.get()
    thread_id, conv_id = _conversation(body.thread_id)
    if pending_interrupt(graph, thread_id):
        raise HTTPException(409, "Please answer the pending question first (POST /chat/answer/stream).")
    return _stream_run(graph=graph, thread_id=thread_id, text=body.text, conversation_id=conv_id,
                       clinic_id=demo_clinic_id())


@router.post("/answer/stream", dependencies=[Depends(_guard)])
def chat_answer_stream(body: AnswerIn) -> StreamingResponse:
    graph = _Runtime.get()
    _conversation(body.thread_id)
    if not pending_interrupt(graph, body.thread_id):
        raise HTTPException(409, "There's no question waiting for an answer.")
    return _stream_run(graph=graph, thread_id=body.thread_id, approved=body.approved, clinic_id=demo_clinic_id())


@router.get("/{thread_id}/pending")
def chat_pending(thread_id: str) -> dict:
    graph = _Runtime.get()
    _conversation(thread_id)
    return {"thread_id": thread_id, "interrupt": pending_interrupt(graph, thread_id)}


@router.get("/{thread_id}/events")
async def chat_events(thread_id: str, request: Request, after: int = 0) -> StreamingResponse:
    """Replay events after `after` (or the Last-Event-ID header), then stream new ones live."""
    _conversation(thread_id)
    last = request.headers.get("last-event-id")
    after = int(last) if last and last.isdigit() else after
    return StreamingResponse(_subscribe(thread_channel(thread_id), after, request), media_type="text/event-stream",
                             headers=SSE_HEADERS)


async def _subscribe(channel: str, after: int, request: Request, keepalive_s: float = 15.0):
    sub, backlog = hub.subscribe(channel, after)
    try:
        for ev in backlog:
            yield f"id: {ev['cursor']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        while not await request.is_disconnected():
            try:
                ev = await asyncio.wait_for(sub.queue.get(), keepalive_s)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield f"id: {ev['cursor']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
    finally:
        hub.unsubscribe(sub)
