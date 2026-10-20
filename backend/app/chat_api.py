"""Chat endpoints.

POST /chat/turn            {"text", "thread_id"?}   → JSON {"thread_id", "reply" | "interrupt", ...}
POST /chat/answer          {"thread_id", "approved"} → the patient's yes/no to a consent or confirmation card
POST /chat/stream          {"text", "thread_id"?}   → the same turn as a live AG-UI event stream (SSE)
POST /chat/answer/stream   {"thread_id", "approved"} → the same answer as a live AG-UI event stream (SSE)
GET  /chat/{thread_id}/pending                      → the question a conversation is waiting on, if any
GET  /chat/{thread_id}/events?after=N               → reconnect: replay + live events for a conversation (SSE)

Every run also feeds the staff dashboard's live channel (see app/realtime/routes.py).

Conversations belong to the browser that started them (DEVLOG #48): the first message returns a secret
`thread_key` (JSON field, or the `X-Thread-Key` response header on streams). Every later call for that
thread must send it back in the `X-Thread-Key` header; without it the thread "doesn't exist" (404).
Only its SHA-256 is stored.

Protected against runaway spend: a per-IP rate limit and a daily model-spend cap (from the cost ledger).
Uses the demo clinic. Try it from /docs.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, time as dtime, timezone
from threading import Lock

import asyncio
import contextvars
import json
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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


CHANNEL: contextvars.ContextVar[str] = contextvars.ContextVar("chat_channel", default="web")
Channel = Literal["web", "voice"]


class _Runtime:
    """Lazily built graph + model router + checkpointer (overridable in tests). The A2A server builds its
    own graph from the same router and checkpointer (app/a2a/executor.py)."""
    graph = None
    router = None
    checkpointer = None

    @classmethod
    def parts(cls):
        from app.agent.models import default_router
        from app.graphs.checkpointer import make_checkpointer
        try:
            cls.router = cls.router or default_router()
        except RuntimeError as e:
            raise HTTPException(503, "The assistant isn't configured yet.") from e
        if cls.checkpointer is None:
            cls.checkpointer = make_checkpointer(get_settings().database_url)
        return cls.router, cls.checkpointer

    @classmethod
    def get(cls):
        if cls.graph is None:
            router, checkpointer = cls.parts()
            cls.graph = build_graph(router, _make_ctx, checkpointer)
        return cls.graph


def _demo_clinic_id(session) -> str:
    cid = session.scalar(select(m.Clinic.id).where(m.Clinic.name == CLINIC_NAME))
    if cid is None:
        raise HTTPException(503, "The demo clinic hasn't been loaded yet.")
    return cid


def _make_ctx() -> ToolContext:
    session = SessionLocal()                     # one short-lived session per graph step (closed by the graph)
    try:
        return ToolContext(session=session, clinic_id=_demo_clinic_id(session), owns_session=True,
                           channel=CHANNEL.get())        # "voice": bookings show as phone/voice on the dashboard
    except Exception:
        session.close()
        raise


def _redteam_bypass(request: Request) -> bool:
    """The nightly red-team run (GitHub Actions) sends REDTEAM_TOKEN to skip the per-IP rate limit.
    The daily spend cap still applies."""
    import hmac
    sent = request.headers.get("x-redteam-token")
    expected = get_settings().secret("REDTEAM_TOKEN")
    return bool(sent and expected and hmac.compare_digest(sent, expected.get_secret_value()))


def _guard(request: Request) -> None:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    now = time.monotonic()
    if _redteam_bypass(request):
        ip = None
    with _lock:
        q = _hits[ip] if ip else deque()
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            raise HTTPException(429, "Too many messages. Please wait a few minutes.")
        q.append(now)
    if not spend_ok():
        raise HTTPException(503, "The demo has reached today's usage limit. Please try again tomorrow.")


def spend_ok() -> bool:
    """Today's model spend (from the cost ledger) is under DAILY_SPEND_CAP_USD."""
    with SessionLocal() as s:
        start = datetime.combine(datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc)
        spent = s.scalar(select(func.coalesce(func.sum(m.LLMCall.cost_usd), 0.0)).where(m.LLMCall.at >= start))
    return spent < get_settings().daily_spend_cap_usd


class TurnIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    thread_id: str | None = None
    language: str | None = Field(None, pattern="^(en|hi|kn)$", description="The app's language picker.")
    channel: Channel = Field("web", description='"voice" when the text came from speech recognition (call screen).')


class AnswerIn(BaseModel):
    thread_id: str
    approved: bool | None = None
    text: str | None = Field(None, max_length=1000, description="A typed answer instead of a yes/no tap.")
    channel: Channel = "web"

    def answer(self) -> bool | str:
        if self.text and self.text.strip():
            return self.text
        if self.approved is None:
            raise HTTPException(422, "Send approved (true/false) or text.")
        return self.approved


def demo_clinic_id() -> str:
    with SessionLocal() as s:
        return _demo_clinic_id(s)


def _conversation(thread_id: str | None, channel: str | None = None, key: str | None = None) -> tuple[str, str, str | None]:
    """(thread_id, conversation_id, new_thread_key). Creates the conversation on the first message and returns
    its secret key; for an existing thread the caller must present that key. A conversation that switches to
    the call screen is marked "voice"."""
    import hashlib
    import hmac
    import secrets
    with SessionLocal() as s:
        if thread_id:
            conv = s.scalar(select(m.Conversation).where(m.Conversation.thread_id == thread_id))
            ok = (conv is not None and conv.thread_key_hash and key
                  and hmac.compare_digest(conv.thread_key_hash, hashlib.sha256(key.encode()).hexdigest()))
            if not ok:
                raise HTTPException(404, "Unknown conversation.")
            if channel == "voice" and conv.channel == "web":
                conv.channel = "voice"
                s.commit()
            return conv.thread_id, conv.id, None
        new_key = secrets.token_urlsafe(24)
        conv = m.Conversation(clinic_id=_demo_clinic_id(s), channel=channel or "web",
                              thread_key_hash=hashlib.sha256(new_key.encode()).hexdigest())
        s.add(conv)
        s.commit()
        return conv.thread_id, conv.id, new_key


ThreadKey = Header(None, alias="X-Thread-Key", description="The secret returned with the conversation's first reply.")


def _public(thread_id: str, out: dict, key: str | None = None) -> dict:
    return {"thread_id": thread_id, **({"thread_key": key} if key else {}), "reply": out["reply"], "speech": out.get("speech"), "interrupt": out["interrupt"], "ui": out.get("ui") or {},
            "intent": out["intent"], "language": out["language"], "tools": out["tools"]}


@router.post("/turn", dependencies=[Depends(_guard)])
def chat_turn(body: TurnIn, x_thread_key: str | None = ThreadKey) -> dict:
    graph = _Runtime.get()
    thread_id, conv_id, key = _conversation(body.thread_id, body.channel, x_thread_key)
    if pending_interrupt(graph, thread_id):
        raise HTTPException(409, "Please answer the pending question first (POST /chat/answer).")
    token = CHANNEL.set(body.channel)
    try:
        out = runner.run(graph, thread_id, text=body.text, conversation_id=conv_id, clinic_id=demo_clinic_id(),
                         language=body.language, voice=body.channel == "voice")
    finally:
        CHANNEL.reset(token)
    return _public(thread_id, out, key)


@router.post("/answer", dependencies=[Depends(_guard)])
def chat_answer(body: AnswerIn, x_thread_key: str | None = ThreadKey) -> dict:
    graph = _Runtime.get()
    _conversation(body.thread_id, body.channel, x_thread_key)
    if not pending_interrupt(graph, body.thread_id):
        raise HTTPException(409, "There's no question waiting for an answer.")
    token = CHANNEL.set(body.channel)
    try:
        out = runner.run(graph, body.thread_id, approved=body.answer(), clinic_id=demo_clinic_id(),
                         voice=body.channel == "voice")
    finally:
        CHANNEL.reset(token)
    return _public(body.thread_id, out)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _stream_run(channel: str = "web", key: str | None = None, **kwargs) -> StreamingResponse:
    """Run the graph in a worker thread and stream its events as AG-UI server-sent events."""
    async def gen():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        done = object()

        def on_event(ev: dict) -> None:
            loop.call_soon_threadsafe(q.put_nowait, ev)

        def work() -> None:
            CHANNEL.set(channel)                  # this worker thread's own context (run_in_executor doesn't copy it)
            try:
                runner.run(on_event=on_event, voice=channel == "voice", **kwargs)
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
    headers = {**SSE_HEADERS, **({"X-Thread-Key": key, "X-Thread-Id": kwargs["thread_id"]} if key else {})}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@router.post("/stream", dependencies=[Depends(_guard)])
def chat_stream(body: TurnIn, x_thread_key: str | None = ThreadKey) -> StreamingResponse:
    graph = _Runtime.get()
    thread_id, conv_id, key = _conversation(body.thread_id, body.channel, x_thread_key)
    if pending_interrupt(graph, thread_id):
        raise HTTPException(409, "Please answer the pending question first (POST /chat/answer/stream).")
    return _stream_run(channel=body.channel, key=key, graph=graph, thread_id=thread_id, text=body.text, conversation_id=conv_id,
                       clinic_id=demo_clinic_id(), language=body.language)


@router.post("/answer/stream", dependencies=[Depends(_guard)])
def chat_answer_stream(body: AnswerIn, x_thread_key: str | None = ThreadKey) -> StreamingResponse:
    graph = _Runtime.get()
    _conversation(body.thread_id, body.channel, x_thread_key)
    if not pending_interrupt(graph, body.thread_id):
        raise HTTPException(409, "There's no question waiting for an answer.")
    return _stream_run(channel=body.channel, graph=graph, thread_id=body.thread_id, approved=body.answer(), clinic_id=demo_clinic_id())


@router.get("/{thread_id}/pending")
def chat_pending(thread_id: str, x_thread_key: str | None = ThreadKey) -> dict:
    graph = _Runtime.get()
    _conversation(thread_id, key=x_thread_key)
    return {"thread_id": thread_id, "interrupt": pending_interrupt(graph, thread_id)}


@router.get("/clinic")
def clinic_info() -> dict:
    """Public facts for the app's side panel: name, address, doctors' hours, languages, emergency numbers."""
    from app.guardrails.messages import DEFAULT_NUMBERS
    with SessionLocal() as s:
        c = s.get(m.Clinic, _demo_clinic_id(s))
        doctors = s.scalars(select(m.Doctor).where(m.Doctor.clinic_id == c.id).order_by(m.Doctor.name)).all()
        return {"name": c.name, "address": c.address, "languages": c.languages,
                "emergency_numbers": {**DEFAULT_NUMBERS, **(c.emergency_numbers or {})},
                "doctors": [{"name": d.name, "specialty": d.specialty, "hours": d.hours, "languages": d.languages}
                            for d in doctors]}


@router.get("/{thread_id}/history")
def chat_history(thread_id: str, x_thread_key: str | None = ThreadKey) -> dict:
    """The visible conversation so far (for the app after a reload) and any card waiting for an answer."""
    graph = _Runtime.get()
    _conversation(thread_id, key=x_thread_key)
    values = graph.get_state({"configurable": {"thread_id": thread_id}}).values or {}
    msgs = [{"role": x["role"], "content": x["content"]} for x in values.get("messages", [])
            if x.get("role") in ("user", "assistant") and x.get("content")]
    return {"thread_id": thread_id, "messages": msgs[-50:], "language": values.get("language", "en"),
            "interrupt": pending_interrupt(graph, thread_id)}


@router.get("/{thread_id}/events")
async def chat_events(thread_id: str, request: Request, after: int = 0,
                      x_thread_key: str | None = ThreadKey) -> StreamingResponse:
    """Replay events after `after` (or the Last-Event-ID header), then stream new ones live."""
    await asyncio.to_thread(_conversation, thread_id, None, x_thread_key)
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
