"""ExecutionEvent: the one typed event stream every run emits (plan §5.6).

Consumers: AG-UI (patient app), the ops WebSocket hub (staff dashboard), the A2A adapter and
OpenTelemetry. Events must be PII-safe by construction: payloads carry ids, names of nodes and
tools, statuses and counts, never phone numbers or free text from patients.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class EventType(str, Enum):
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    RUN_ERROR = "run.error"
    NODE_STARTED = "node.started"
    NODE_FINISHED = "node.finished"
    STATUS = "status"                 # friendly progress line ("Checking Dr. Rao's calendar…")
    TOKEN = "token"                   # streamed reply text (patient's own stream only)
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    RAIL_FIRED = "rail.fired"
    INTERRUPT = "interrupt"           # waiting for the patient (consent, confirmation, identity)
    STATE_DELTA = "state.delta"
    EMERGENCY = "emergency"
    HANDOFF = "handoff"
    COST = "cost"
    BOOKING_CHANGED = "booking.changed"   # ops feed: schedule cell updates


class ExecutionEvent(BaseModel):
    v: int = SCHEMA_VERSION
    run_id: str
    thread_id: str
    seq: int
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventFactory:
    """Creates events for one run with a strictly increasing `seq` (used for reconnect/replay)."""

    def __init__(self, thread_id: str, run_id: str | None = None) -> None:
        self.thread_id = thread_id
        self.run_id = run_id or uuid.uuid4().hex
        self._seq = itertools.count(1)

    def make(self, type: EventType, node: str | None = None, **payload: Any) -> ExecutionEvent:
        return ExecutionEvent(run_id=self.run_id, thread_id=self.thread_id, seq=next(self._seq),
                              type=type, node=node, payload=payload)
