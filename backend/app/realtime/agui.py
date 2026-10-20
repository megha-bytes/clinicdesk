"""Map ClinicDesk ExecutionEvents to the AG-UI protocol (https://docs.ag-ui.com) for the patient app.

RUN_STARTED/FINISHED/ERROR, STEP_STARTED/FINISHED (graph nodes), TOOL_CALL_START/END (tools),
TEXT_MESSAGE_* (the reply), and CUSTOM events for status lines, confirmation cards, guardrails,
emergencies, handoffs, booking changes and cost.
"""
from __future__ import annotations

import uuid

from ag_ui.core import (CustomEvent, RunErrorEvent, RunFinishedEvent, RunStartedEvent, StepFinishedEvent,
                        StepStartedEvent, TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent,
                        ToolCallEndEvent, ToolCallStartEvent)
from ag_ui.encoder import EventEncoder

_enc = EventEncoder()


def to_agui(ev: dict) -> list:
    t, p, node = ev["type"], ev.get("payload") or {}, ev.get("node")
    meta = {"seq": ev.get("seq"), "cursor": ev.get("cursor")}
    if t == "run.started":
        return [RunStartedEvent(thread_id=ev["thread_id"], run_id=ev["run_id"], metadata=meta)]
    if t == "node.started":
        return [StepStartedEvent(step_name=node or "", metadata=meta)]
    if t == "node.finished":
        return [StepFinishedEvent(step_name=node or "", metadata=meta)]
    if t == "tool.started":
        return [ToolCallStartEvent(tool_call_id=p.get("call_id") or uuid.uuid4().hex[:12],
                                   tool_call_name=p.get("tool", ""), metadata=meta)]
    if t == "tool.finished":
        return [ToolCallEndEvent(tool_call_id=p.get("call_id") or "", metadata={**meta, "ok": p.get("ok"),
                                                                                "error": p.get("error")})]
    if t == "run.finished":
        out = []
        if p.get("reply"):
            mid = uuid.uuid4().hex[:12]
            out.append(TextMessageStartEvent(message_id=mid, role="assistant"))
            lines = p["reply"].split("\n")
            for i, line in enumerate(lines):
                out.append(TextMessageContentEvent(message_id=mid, delta=line + ("\n" if i < len(lines) - 1 else "")))
            out.append(TextMessageEndEvent(message_id=mid))
        out.append(RunFinishedEvent(thread_id=ev["thread_id"], run_id=ev["run_id"],
                                    result={k: p.get(k) for k in ("reply", "speech", "interrupt", "ui", "intent",
                                                                  "language", "cost_usd", "model_calls", "total_ms")}))
        return out
    if t == "run.error":
        return [RunErrorEvent(message="Something went wrong. Our clinic staff can help.", code=p.get("error"))]
    name = {"status": "status", "interrupt": "interrupt", "rail.fired": "guardrail", "emergency": "emergency",
            "handoff": "handoff", "booking.changed": "booking", "cost": "model_call"}.get(t, t)
    return [CustomEvent(name=name, value={**p, "node": node}, metadata=meta)]


def encode(ev: dict) -> str:
    return "".join(_enc.encode(x) for x in to_agui(ev))
