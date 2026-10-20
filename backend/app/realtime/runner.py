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
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

from langgraph.types import Command

from app.graphs.clinicdesk import _config, _outcome, is_no, is_yes
from app.realtime.events import EventFactory, EventType
from app.realtime.hub import hub, ops_channel, thread_channel
from app.voice.speech import speak_card, to_speech
from app.voice.spoken import normalise, short_answer

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
PRIVATE_KEYS = {"reply", "ui", "speech"}   # may contain patient details: never sent to the ops channel


def status_text(key: str, lang: str, **kw: Any) -> str | None:
    row = STATUS.get(key)
    if not row:
        return None
    text = row.get(lang, row["en"])
    try:
        return text.format(**{"doctor": "the doctor", "when": "the slot", **{k: v for k, v in kw.items() if v}})
    except (KeyError, IndexError):
        return None


def ui_hints(values: dict) -> dict:
    """Structured data for the app's cards (time buttons, slip, emergency screen). Patient channel only."""
    ui: dict = {}
    say = values.get("say") or {}
    if say:
        from app.graphs.booking_flow import render, t
        lang = values.get("language", "en")
        ui["text"] = render(say, lang, options=False, booked=False)
        if values.get("prefix"):
            ui["text"] = (values["prefix"] + "\n" + ui["text"]).strip()
        if say.get("options"):
            opts = say["options"]
            same_doc = len({o.get("doctor") for o in opts}) == 1
            same_fee = len({o.get("fee_inr") for o in opts}) == 1
            ui["options_title"] = t("options", lang, who=opts[0].get("doctor") if same_doc else say.get("who", ""),
                                    fee=f" (₹{opts[0]['fee_inr']})" if same_fee and opts[0].get("fee_inr") else "")
    if say.get("options"):
        ui["options"] = [{"n": i, "when": o.get("short") or o.get("label"), "doctor": o.get("doctor"),
                          "fee_inr": o.get("fee_inr")} for i, o in enumerate(say["options"], 1)]
    if say.get("booked"):
        ui["booked"] = say["booked"]
    if say.get("chosen"):
        ui["chosen"] = say["chosen"]
    if say.get("ask"):
        ui["ask"] = say["ask"]
    guard = values.get("guard") or {}
    if guard.get("kind") == "emergency":
        from app.guardrails.messages import DEFAULT_NUMBERS
        ui["emergency"] = {"kind": guard.get("detail"), "numbers": {**DEFAULT_NUMBERS,
                                                                    **(values.get("emergency_numbers") or {})}}
    elif guard.get("kind") in ("injection", "privacy", "medical", "payment"):
        ui["refused"] = guard["kind"]
    if any(r.get("tool") == "handoff_to_staff" and (r.get("result") or {}).get("ok")
           for r in values.get("tool_results") or []):
        ui["handoff"] = True
    return ui


def clinic_today() -> date:
    """The clinic's date (the demo clinic is in Bengaluru), for "today"/"tomorrow" in spoken replies."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def spoken_reply(values: dict, out: dict) -> str:
    """What a voice call reads out: the card as a yes/no question, or the full reply (with options) for the ear."""
    lang = out.get("language") or values.get("language") or "en"
    if out.get("interrupt"):
        return speak_card(out["interrupt"], lang, clinic_today())
    text = out.get("reply") or ""
    say = values.get("say") or {}
    if say:                                   # the chat text leaves options out; speech needs them
        from app.graphs.booking_flow import render
        text = render(say, lang)
        if values.get("prefix"):
            text = values["prefix"] + "\n" + text
    return to_speech(text, lang, clinic_today())


def run(graph, thread_id: str, *, text: str | None = None, approved: bool | str | None = None,
        conversation_id: str | None = None, clinic_id: str | None = None, language: str | None = None,
        on_event: Callable[[dict], None] | None = None, voice: bool = False) -> dict:
    """Run the graph for a new message (`text`) or an answer to a pending card (`approved`).

    An answer that is neither yes nor no ("No, I asked for 3, 9:10 AM") declines the card and is then
    handled as a new message, so the patient's words aren't lost (DEVLOG #37). A short spoken or typed
    yes/no ("yes please", "haan ji theek hai") counts as the answer (DEVLOG #44).
    `voice`: the text came from speech recognition, so spoken numbers become digits first."""
    if text is not None:
        text = normalise(text, voice=voice)
    if text is None and isinstance(approved, str):
        approved = normalise(approved, voice=voice)
        plain = short_answer(approved)
        if plain is not None:
            approved = plain
    if (text is None and isinstance(approved, str) and approved.strip()
            and not is_yes(approved) and not is_no(approved)):
        run(graph, thread_id, approved=False, conversation_id=conversation_id, clinic_id=clinic_id, on_event=on_event)
        return run(graph, thread_id, text=approved.strip()[:1000], conversation_id=conversation_id,
                   clinic_id=clinic_id, language=language, on_event=on_event, voice=voice)
    config = _config(thread_id)
    if text is not None:
        inp: Any = {"messages": [{"role": "user", "content": text}]}
        if language in ("en", "hi", "kn"):
            inp["language"] = language            # the app's language picker: used unless the message says otherwise
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
    out["ui"] = ui_hints(values) if not interrupts else {}
    out["speech"] = spoken_reply(values, out)
    send(EventType.RUN_FINISHED, reply=out["reply"], speech=out["speech"], interrupt=out["interrupt"], ui=out["ui"], intent=out["intent"],
         language=out["language"], cost_usd=round(totals["cost_usd"], 6), model_calls=totals["calls"],
         total_ms=round((time.perf_counter() - t0) * 1000))
    out["cost_usd"], out["total_ms"] = round(totals["cost_usd"], 6), round((time.perf_counter() - t0) * 1000)
    return out
