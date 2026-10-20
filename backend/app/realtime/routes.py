"""Staff dashboard live feed.

WS  /ops/ws?after=N        → JSON events for the demo clinic (bookings, emergencies, handoffs, guardrails,
                             node steps, model calls with cost). PII-free: no patient names, phones or text.
GET /ops/recent            → the last events (for a first paint without a socket)

If STAFF_DASHBOARD_TOKEN is set, pass it as ?token=… .
"""
from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.realtime.hub import hub, ops_channel
from app.settings import get_settings

router = APIRouter(prefix="/ops", tags=["ops"])


def _token_ok(token: str | None) -> bool:
    expected = get_settings().secret("STAFF_DASHBOARD_TOKEN")
    if expected is None:
        return True
    return token is not None and hmac.compare_digest(token, expected.get_secret_value())


def _clinic() -> str:
    from app.chat_api import demo_clinic_id
    return demo_clinic_id()


@router.get("/recent")
def recent(limit: int = Query(50, ge=1, le=200), token: str | None = None) -> dict:
    if not _token_ok(token):
        raise HTTPException(401, "Staff token required.")
    return {"events": hub.history(ops_channel(_clinic()))[-limit:]}


@router.websocket("/ws")
async def ops_ws(ws: WebSocket, after: int = 0, token: str | None = None) -> None:
    if not _token_ok(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    channel = ops_channel(await asyncio.to_thread(_clinic))
    sub, backlog = hub.subscribe(channel, after)
    try:
        for ev in backlog:
            await ws.send_json(ev)
        while True:
            try:
                ev = await asyncio.wait_for(sub.queue.get(), 20)
                await ws.send_json(ev)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(sub)
