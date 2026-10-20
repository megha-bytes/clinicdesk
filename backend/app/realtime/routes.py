"""Staff dashboard live feed.

WS  /ops/ws?ticket=…&after=N  → JSON events for the staff member's clinic (bookings, emergencies, handoffs,
                                guardrails, node steps, model calls with cost). PII-free: no patient names,
                                phones or text. Get the one-time ticket from POST /auth/ws-ticket (a login
                                token never goes in a URL).
GET /ops/recent               → the last events (owners; for a first paint without a socket)

What each role receives: owners everything; receptionists everything except model cost; doctors only
bookings for their own column, alerts, and the agent's steps without cost.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.auth import Principal, redeem_ticket, require
from app.realtime.hub import hub, ops_channel

router = APIRouter(prefix="/ops", tags=["ops"])
COST_KEYS = {"cost_usd", "model", "tier", "input_tokens", "output_tokens"}


def _doctor_name(doctor_id: str | None) -> str | None:
    if not doctor_id:
        return None
    from app import models as m
    from app.db import SessionLocal
    with SessionLocal() as s:
        d = s.get(m.Doctor, doctor_id)
        return d.name if d else None


def visible(ev: dict, user: Principal, doctor_name: str | None = None) -> dict | None:
    """The event as this staff member may see it (None: not at all)."""
    if user.can("stats"):
        return ev
    if ev.get("type") == "cost":
        return None
    if ev.get("type") == "booking.changed" and not user.can("schedule.all"):
        if (ev.get("payload") or {}).get("doctor") != doctor_name:
            return None
    payload = {k: v for k, v in (ev.get("payload") or {}).items() if k not in COST_KEYS}
    return {**ev, "payload": payload}


@router.get("/recent")
def recent(limit: int = Query(50, ge=1, le=200), user: Principal = Depends(require("xray"))) -> dict:
    return {"events": hub.history(ops_channel(user.clinic_id))[-limit:]}


@router.websocket("/ws")
async def ops_ws(ws: WebSocket, after: int = 0, ticket: str | None = None) -> None:
    user = redeem_ticket(ticket)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    doctor_name = await asyncio.to_thread(_doctor_name, user.doctor_id)
    sub, backlog = hub.subscribe(ops_channel(user.clinic_id), after)

    async def send(ev: dict) -> None:
        if (v := visible(ev, user, doctor_name)) is not None:
            await ws.send_json(v)
    try:
        for ev in backlog:
            await send(ev)
        while True:
            try:
                ev = await asyncio.wait_for(sub.queue.get(), 20)
                await send(ev)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(sub)
