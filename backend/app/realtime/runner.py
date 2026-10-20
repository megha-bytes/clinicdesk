"""Run one graph step (a patient message or an answer to a card) and stream what happens.

Turns LangGraph's `tasks` and `custom` stream into ExecutionEvents: node started/finished, friendly
status lines ("Checking Dr. Ananya Rao's calendar…"), tool calls, model calls with cost, guardrails,
emergencies, handoffs, booking changes, the card the patient must answer, and the final reply.
Every event goes to the patient's thread channel; a PII-free copy (no message text) goes to the
clinic's ops channel for the staff dashboard.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langgraph.types import Command

from app.graphs.clinicdesk import _config, _outcome, is_yes
from app.realtime.events import EventFactory, EventType
from app.realtime.hub import hub, ops_channel, thread_channel

STATUS = {
    "intent": {"en": "Reading your message…", "hi": "आपका संदेश पढ़ रहे हैं…", "kn": "ನಿಮ್ಮ ಸಂದೇಶ ಓದುತ್ತಿದ್ದೇವೆ…"},
    "handoff": {"en": "Writing a note for the staff…", "hi": "स्टाफ़ के लिए नोट लिख रहे हैं…", "kn": "ಸಿಬ್ಬಂದಿಗೆ ಟಿಪ್ಪಣಿ ಬರೆಯುತ್ತಿದ್ದೇವೆ…"},
    "find_slots": {"en": "Checking {doctor}'s calendar…", "hi": "{doctor} का कैलेंडर देख रहे हैं…", "kn": "{doctor} ಅವರ ಕ್ಯಾಲೆಂಡರ್ ನೋಡುತ್ತಿದ್ದೇವೆ…"},
    "find_patient": {"en": "Looking up your details…", "hi": "आपकी जानकारी देख रहे हैं…", "kn": "ನಿಮ್ಮ ವಿವರ ಹುಡುಕುತ್ತಿದ್ದೇವೆ…"},
    "register_patient": {"en": "Saving your details…", "hi": "आपकी जानकारी सेव कर रहे हैं…", "kn": "ನಿಮ್ಮ ವಿವರ ಉಳಿಸುತ್ತಿದ್ದೇವೆ…"},
    "hold_slot": {"en": "Holding {when} for you…", "hi": "{when} आपके लिए रोक रहे हैं…", "kn": "{when} ನಿಮಗಾಗಿ ಹಿಡಿದಿಡುತ್ತಿದ್ದೇವೆ…"},
    "book": {"en": "Booking your appointment…", "hi": "अपॉइंटमेंट बुक कर रहे हैं…", "kn": "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡುತ್ತಿದ್ದೇವೆ…"},
    "send_payment_link": {"en": "Creating your payment link…", "hi": "भुगतान लिंक बना रहे हैं…", "kn": "ಪಾವತಿ ಲಿಂಕ್ ಮಾಡುತ್ತಿದ್ದೇವೆ…"},
    "route_to_doctor": {"en": "Finding the right doctor…", "hi": "सही डॉक्टर ढूँढ रहे हैं…", "kn": "ಸರಿಯಾದ ವೈದ್ಯರನ್ನು ಹುಡುಕುತ್ತಿದ್ದೇವೆ…"},
    "get_clinic_info": {"en": "Checking clinic details…", "hi": "क्लिनिक की जानकारी देख रहे हैं…", "kn": "ಕ್ಲಿನಿಕ್ ವಿವರ ನೋಡುತ್ತಿದ್ದೇವೆ…"},
    "queue_status": {"en": "Checking the queue…", "hi": "कतार देख रहे हैं…", "kn": "ಸರತಿ ನೋಡುತ್ತಿದ್ದೇವೆ…"},
    "cancel": {"en": "Cancelling…", "hi": "रद्द कर रहे हैं…", "kn": "ರದ್ದು ಮಾಡುತ್ತಿದ್ದೇವೆ…"},
    "reschedule": {"en": "Moving your appointment…", "hi": "अपॉइंटमेंट बदल रहे हैं…", "kn": "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬದಲಾಯಿಸುತ್ತಿದ್ದೇವೆ…"},
    "join_waitlist": {"en": "Adding you to the waitlist…", "hi": "वेटलिस्ट में जोड़ रहे हैं…", "kn": "ಕಾಯುವ ಪಟ್ಟಿಗೆ ಸೇರಿಸುತ್ತಿದ್ದೇವೆ…"},
    "raise_emergency_alert": {"en": "Alerting the clinic staff…", "hi": "क्लिनिक स्टाफ़ को बता रहे हैं…", "kn": "ಕ್ಲಿನಿಕ್ ಸಿಬ್ಬಂದಿಗೆ ತಿಳಿಸುತ್ತಿದ್ದೇವೆ…"},
    "handoff_to_staff": {"en": "Asking the staff to call you…", "hi": "स्टाफ़ से कॉल करने को कह रहे हैं…", "kn": "ಸಿಬ್ಬಂದಿಗೆ ಕರೆ ಮಾಡಲು ಹೇಳುತ್ತಿದ್ದೇವೆ…"},
}
CUSTOM_TO_TYPE = {
    "tool.started": EventType.TOOL_STARTED, "tool.finished": EventType.TOOL_FINISHED, "model.call": EventType.COST,
    "rail.fired": EventType.RAIL_FIRED, "emergency": EventType.EMERGENCY, "handoff": EventType.HANDOFF,
    "booking.changed": EventType.BOOKING_CHANGED,
}
PRIVATE_KEYS = {"reply"}          # the reply may contain patient details: never sent to the ops channel


def status_text(key: str, lang: str, **kw: Any) -> str | None:
    row = STATUS.get(key)
    if not row:
        return None
    text = row.get(lang, row["en"])
    try:
        return text.format(**{"doctor": "the doctor", "when": "the slot", **{k: v for k, v in kw.items() if v}})
    except (KeyError, IndexError):
        return None


def run(graph, thread_id: str, *, text: str | None = None, approved: bool | str | None = None,
        conversation_id: str | None = None, clinic_id: str | None = None,
        on_event: Callable[[dict], None] | None = None) -> dict:
    """Run the graph for a new message (`text`) or an answer to a pending card (`approved`)."""
    config = _config(thread_id)
    if text is not None:
        inp: Any = {"messages": [{"role": "user", "content": text}]}
        if conversation_id:
            inp["conversation_id"] = conversation_id
    else:
        inp = Command(resume={"approved": is_yes(approved)})
    before = graph.get_state(config).values or {}
    lang = before.get("language", "en")
    f = EventFactory(thread_id)
    totals = {"cost_usd": 0.0, "model_ms": 0.0, "calls": 0}
    t0 = time.perf_counter()

    def send(type_: EventType, node: str | None = None, **payload: Any) -> None:
        ev = f.make(type_, node, **payload).model_dump(mode="json")
        hub.publish(thread_channel(thread_id), ev)
        if clinic_id:
            hub.publish(ops_channel(clinic_id), {**ev, "payload": {k: v for k, v in ev["payload"].items()
                                                                    if k not in PRIVATE_KEYS}})
        if on_event:
            on_event(ev)

    send(EventType.RUN_STARTED, kind="message" if text is not None else "answer")
    try:
        for mode, chunk in graph.stream(inp, config, stream_mode=["tasks", "custom"]):
            if mode == "tasks":
                name = chunk.get("name")
                if "input" in chunk:
                    send(EventType.NODE_STARTED, name)
                    if (s := status_text(name, lang)):
                        send(EventType.STATUS, name, text=s)
                else:
                    result = chunk.get("result") or {}
                    if isinstance(result, dict) and result.get("language"):
                        lang = result["language"]
                    send(EventType.NODE_FINISHED, name, error=bool(chunk.get("error")))
                    for i in chunk.get("interrupts") or []:
                        v = i.get("value") or {}
                        send(EventType.INTERRUPT, name, kind=v.get("kind"), action=v.get("action"),
                             summary=v.get("summary"), options=v.get("options"))
            elif mode == "custom" and isinstance(chunk, dict):
                kind = chunk.get("event")
                payload = dict(chunk.get("data") or {})
                if kind == "tool.started" and (s := status_text(payload.get("tool", ""), lang, **payload)):
                    send(EventType.STATUS, None, text=s)
                if kind == "model.call":
                    totals["cost_usd"] += payload.get("cost_usd") or 0
                    totals["model_ms"] += payload.get("latency_ms") or 0
                    totals["calls"] += 1
                if kind in CUSTOM_TO_TYPE:
                    send(CUSTOM_TO_TYPE[kind], payload.pop("node", None), **payload)
                elif kind == "status" and payload.get("text"):
                    send(EventType.STATUS, None, text=payload["text"])
    except Exception as e:
        send(EventType.RUN_ERROR, error=type(e).__name__)
        raise
    values = graph.get_state(config).values or {}
    snap = graph.get_state(config)
    interrupts = [i for t in (snap.tasks or ()) for i in (t.interrupts or ())]
    result = {**values}
    if interrupts:
        result["__interrupt__"] = interrupts
    out = _outcome(result)
    send(EventType.RUN_FINISHED, reply=out["reply"], interrupt=out["interrupt"], intent=out["intent"],
         language=out["language"], cost_usd=round(totals["cost_usd"], 6), model_calls=totals["calls"],
         total_ms=round((time.perf_counter() - t0) * 1000))
    out["cost_usd"], out["total_ms"] = round(totals["cost_usd"], 6), round((time.perf_counter() - t0) * 1000)
    return out
