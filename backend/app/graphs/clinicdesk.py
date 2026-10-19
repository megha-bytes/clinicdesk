"""ClinicDeskGraph v2 (Day 7): booking, consent and confirmation inside LangGraph.

    START → understand ─┬─ booking ─→ flow (deterministic, booking_flow.py) ─┬─ card → confirm ⏸ → execute → flow
                        │                                                     └──────────────────────→ respond → END
                        ├─ other data ─→ agent ⇄ tools ─┬─ card → confirm ⏸ → execute → agent
                        │                               └──────────────────────────────────────────→ respond
                        └─ smalltalk ──────────────────────────────────────────────────────────────→ respond

`understand` (Lightning) classifies the message AND extracts details (doctor, date, time, name, phone,
age, option number). Bookings then run as plain code; the model never decides a booking step.

Safety rules enforced by the graph, not the prompt
- **Consent**: `register_patient` never runs on the model's say-so. The graph pauses with a fixed
  consent notice and uses only the patient's answer (the model's `consent` argument is ignored).
- **Confirmation**: `book`, `reschedule` and `cancel` pause with a summary built from the database
  (doctor, time, fee), never from model text, and run only on an explicit "yes".
- **Identity**: tools that act on a patient use the patient verified *in this conversation*
  (via `find_patient` or `register_patient`); a model-supplied patient_id is overwritten.
- **Real details only**: registration needs a name and phone number the patient actually typed.
  Consent is asked once per conversation.
- **Hold → confirmation card**: a successful hold immediately becomes a booking confirmation card,
  so a booking can't stall waiting for a typed "yes".
- **No false claims**: a reply that says something was booked/confirmed when no booking happened
  this turn is regenerated, then replaced. Internal ids never reach the patient.
- **Bounded**: at most MAX_TOOL_ROUNDS tool steps per patient turn.
- **Durable**: with a checkpointer, a paused confirmation survives a server restart.
"""
from __future__ import annotations

import json
import logging
import operator
import re
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import models as m
from app import repo
from app.agent.models import ModelRouter, ModelUnavailable
from app.agent.tools import ToolContext, appt_summary, label, openai_tool_specs, parse_start, run_tool
from app.graphs.booking_flow import flow_active, render, run_flow, unverified_times
from app.graphs.language import detect as detect_language

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
INTENTS = ("info", "book", "reschedule", "cancel", "queue", "pay", "human", "smalltalk", "other")
DATA_INTENTS = {"info", "book", "reschedule", "cancel", "queue", "pay", "human"}
LANG_NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada"}

AGENT_TOOLS = ["get_clinic_info", "route_to_doctor", "find_slots", "find_patient", "register_patient",
               "hold_slot", "book", "reschedule", "cancel", "send_payment_link", "queue_status",
               "join_waitlist", "handoff_to_staff"]
GATED = {"register_patient", "book", "reschedule", "cancel"}             # graph asks the patient first
NEEDS_PATIENT = {"book", "reschedule", "cancel", "queue_status", "join_waitlist"}
SETS_PATIENT = {"find_patient", "register_patient"}

INTENT_PROMPT = """You classify messages sent to a clinic's front desk and extract details from them.
Today is {today}.
Return ONLY a JSON object with these keys (null when the patient didn't say it in THIS message):
{{"intent": one of {intents}, "language": "en" | "hi" | "kn" | "other",
 "doctor": doctor name as said, "need": reason for the visit in a few words,
 "date": "today" | "tomorrow" | "YYYY-MM-DD", "time": "HH:MM" (24h), "window": "morning" | "afternoon" | "evening",
 "name": patient's name, "age": number, "phone": digits exactly as typed, "new_patient": true | false,
 "follow_up": true | false, "option": number of the option they picked}}
- info: timings, fees, doctors, address questions
- book / reschedule / cancel / queue (token or wait time) / pay
- human: wants to talk to a person
- smalltalk: greetings, thanks, goodbyes
- other: anything else (including medical questions)
- A short answer (yes, no, a time, a number, a name, a phone number) belongs to whatever the assistant's
  last message asked about; while booking, that's "book".
Language is the language the patient wrote in (Hindi in Latin script counts as "hi")."""

AGENT_PROMPT = """You are the action step of the front desk of {clinic}. Today is {today} ({tz}).
Decide which tools to call for the patient's latest message. You never write to the patient yourself.
Workflow for appointments:
1. Identify the patient: an existing patient → find_patient(phone, name). A new patient → register_patient
   with name, age, phone and language (the clinic will ask the patient for consent; just call it).
   Use only details the patient actually typed. If any are missing, reply DONE so the receptionist asks.
2. Find options: route_to_doctor (if no doctor named) → find_slots.
3. When the patient has chosen an option, hold_slot with that exact `start`. The clinic then shows the
   patient a confirmation card and books it. Never ask the patient to type "yes" yourself.
4. After booking, call send_payment_link.
Reschedule/cancel/queue: identify the patient first, then use their appointment_id from find_patient.
If the patient asks for a human, call handoff_to_staff. When nothing more is needed, reply with the word DONE.
Never invent ids, times or fees; use only values returned by tools."""

RESPOND_PROMPT = """You are the friendly receptionist of {clinic}. Reply in {language}.
Be brief: at most 2 short sentences, under 30 words; it may be read aloud on a phone call. No markdown.
Hard rules:
- State ONLY times, dates, fees, doctor names, token numbers, wait times and links that appear in TOOL RESULTS.
  If a needed fact is missing, say you'll check with the clinic staff. Never guess.
- Never give medical advice, diagnosis or medication information. Offer to book a doctor instead.
- If you need details to continue (name, age, phone number, preferred day or time, which option), ask for them.
- If a tool result says PATIENT_DECLINED, accept it politely. If it says VERIFY_FIRST, ask for phone number and name.
- Only say an appointment is booked, confirmed, moved or cancelled if a TOOL RESULT below shows it succeeded.
- Don't mention clinic policies, documents to bring, arrival times or rules unless they are in TOOL RESULTS.
- Never show ids (patient, hold or appointment ids).
TOOL RESULTS (this turn):
{tool_results}"""

CONSENT_TEXT = {                 # short enough to read aloud; covers what, why, and deletion
    "en": "We'll save your name, age and phone only for your appointments at {clinic}. You can ask us to delete it. OK?",
    "hi": "हम आपका नाम, उम्र और फ़ोन सिर्फ़ {clinic} में आपकी अपॉइंटमेंट के लिए रखेंगे। आप इसे कभी भी हटवा सकते हैं। ठीक है?",
    "kn": "ನಿಮ್ಮ ಹೆಸರು, ವಯಸ್ಸು ಮತ್ತು ಫೋನ್ ಅನ್ನು {clinic} ನಲ್ಲಿ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್‌ಗಾಗಿ ಮಾತ್ರ ಇಡುತ್ತೇವೆ. ಯಾವಾಗ ಬೇಕಾದರೂ ಅಳಿಸಲು ಹೇಳಬಹುದು. ಸರಿಯೇ?",
}
YES_WORDS = {"yes", "y", "ok", "okay", "sure", "confirm", "haan", "ha", "han", "ji", "haan ji", "हाँ", "हां", "ಹೌದು", "sari", "ಸರಿ"}


class State(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]   # visible chat: {"role": "user"|"assistant", "content"}
    scratch: list[dict]          # this turn's tool-calling transcript (model + tool messages)
    tool_results: list[dict]     # this turn's [{"tool", "args", "result"}]
    intent: str
    language: str
    patient_id: str | None       # verified in THIS conversation
    pending: dict | None         # gated call waiting for the patient: {"call", "kind", "summary"}
    consented: bool              # patient agreed to the privacy notice in this conversation
    ext: dict                    # details extracted from the latest message
    booking: dict                # booking in progress: doctor, date, details, offered options, chosen
    in_flow: bool                # this turn is handled by the deterministic booking flow
    say: dict | None             # what the booking flow needs the reply to contain
    hold_id: str | None          # an active hold waiting to be booked
    rounds: int
    reply: str
    conversation_id: str | None


def _parse_json(text: str) -> dict:
    m_ = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m_.group(0)) if m_ else {}
    except json.JSONDecodeError:
        return {}


_ID = re.compile(r"(?<![/\w])\*{0,2}[0-9a-f]{32}\*{0,2}(?![\w/])")
_HEX = r"\**[0-9a-f]{32}\**"
_ID_LINE = re.compile(r"^[ \t]*[-*•][^\n]*" + _HEX + r"[^\n]*(?:\n|$)", re.M)          # "- **Reference:** <id>"
_ID_SENTENCE = re.compile(r"[^.!?\n]*\b(?:your|the)\s+(?:patient|hold|booking|appointment)\s+(?:id|reference)\s+is\s+"
                          + _HEX + r"\W*?(?:[.!?]|$)", re.I | re.M)
_ID_CLAUSE = re.compile(r"\s*(?:(?:under|with)\s+)?(?:(?:your|the|a)\s+)?(?:patient|hold|booking|appointment)?\s*"
                        r"(?:id|reference|ref)\b\s*(?:is|:|#)?\s*" + _HEX, re.I)
_CLAIM = re.compile(r"\b(?:successfully|has been|have been|is now|is|been)\s+(?:booked|confirmed|scheduled|rescheduled|cancel+ed)\b"
                    r"|\bbooking (?:is )?confirmed\b|\bI(?:'ve| have) (?:booked|confirmed|cancel+ed|rescheduled)\b", re.I)
SUCCESS_TOOLS = {"book", "reschedule", "cancel"}


def scrub_ids(text: str) -> str:
    """Remove internal 32-hex ids from patient-facing text (links like /pay/<id> are kept)."""
    text = _ID_LINE.sub("", text)
    text = _ID_SENTENCE.sub("", text)
    text = _ID_CLAUSE.sub("", text)
    text = _ID.sub("", text)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def shorten(text: str, max_sentences: int = 2, max_words: int = 40) -> str:
    """Keep model replies phone-sized: the first sentences, no markdown."""
    text = re.sub(r"\*\*|__|[#`]", "", text or "").strip()
    sentences = re.split(r"(?<=[.!?।])(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!Mrs\.)(?<!\bSt\.)\s+", text)
    out = " ".join(sentences[:max_sentences])
    words = out.split()
    if len(words) > max_words:
        out = " ".join(words[:max_words]).rstrip(",;:") + "…"
    return out


def false_claim(reply: str, tool_results: list[dict]) -> bool:
    """True if the reply claims a booking/cancellation that no tool confirmed this turn."""
    if not _CLAIM.search(reply or ""):
        return False
    for r in tool_results:
        res = r.get("result") or {}
        if r.get("tool") in SUCCESS_TOOLS and res.get("ok"):
            return False
        if r.get("tool") == "find_patient" and res.get("upcoming"):
            return False
    return True


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _typed_by_patient(args: dict, messages: list[dict]) -> str | None:
    """Error message if the registration details weren't actually typed by the patient."""
    said = " ".join(x["content"] for x in messages if x["role"] == "user")
    phone = _digits(str(args.get("phone", "")))[-10:]
    if len(phone) < 10 or phone not in _digits(said):
        return "Ask the patient for their 10-digit phone number; don't guess it."
    first = str(args.get("name", "")).split()[:1]
    if not first or first[0].casefold() not in said.casefold():
        return "Ask the patient for their name; don't guess it."
    if not re.search(r"\b%d\b" % int(args.get("age", -1) or -1), said):
        return "Ask the patient for their age; don't guess it."
    return None


def is_yes(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("approved", value.get("answer"))
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower().strip(".!") in YES_WORDS


def _type_label(name: str) -> str:
    if name.startswith("procedure:"):
        return name.split(":", 1)[1].replace("_", " ").capitalize()
    return {"new": "New consultation", "follow_up": "Follow-up"}.get(name, name.replace("_", " "))


def _summary(ctx: ToolContext, call: dict, lang: str) -> tuple[str, str]:
    """Deterministic (kind, text) for the confirmation card, built from the database."""
    name, args = call["name"], call["args"]
    tz = ctx.tz
    if name == "register_patient":
        text = CONSENT_TEXT.get(lang, CONSENT_TEXT["en"]).format(clinic=ctx.clinic.name)
        return "consent", text
    if name == "book":
        row = ctx.session.get(m.Appointment, args.get("hold_id", ""))
        if row is None or row.status != "held":
            return "", ""
        s = appt_summary(ctx, row)
        token = f", token {row.token_no}" if row.token_no else ""
        return "confirm", f"Book {s['doctor']} · {s['when']}{token} · {_type_label(s['appointment_type'])} · ₹{s['fee_inr']}?"
    if name in ("reschedule", "cancel"):
        row = ctx.session.get(m.Appointment, args.get("appointment_id", ""))
        if row is None or row.patient_id != args.get("patient_id"):
            return "", ""
        s = appt_summary(ctx, row)
        if name == "cancel":
            return "confirm", f"Cancel your appointment with {s['doctor']} on {s['when']}?"
        try:
            new = label(parse_start(args.get("new_start", ""), ctx), tz)
        except Exception:  # noqa: BLE001
            return "", ""
        return "confirm", f"Move your appointment with {s['doctor']} from {s['when']} to {new}?"
    return "", ""


def _reply_problem(reply: str, results: list[dict], allowed: str) -> str | None:
    """Why a model-written reply must be rewritten (None if it's fine)."""
    if false_claim(reply, results):
        return ("Nothing was booked, moved or cancelled in this turn. Rewrite the reply without claiming that; "
                "offer the next step instead.")
    if "[tool" in reply.lower() or "tool:" in reply.lower():
        return "Don't describe tools or tool calls. Write only the message to the patient."
    bad = unverified_times(reply, allowed)
    if bad:
        return ("Your reply mentions times that are not in the facts. Remove them; "
                + "only use times from TOOL RESULTS.")
    return None


def _public_result(result: dict) -> dict:
    """Tool result without internal ids, for the reply writer."""
    if not isinstance(result, dict):
        return result
    out = {}
    for k, v in result.items():
        if k == "id" or k.endswith("_id") or k == "next_step":
            continue
        if isinstance(v, list):
            v = [_public_result(x) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, dict):
            v = _public_result(v)
        out[k] = v
    return out


def build_graph(router: ModelRouter, make_ctx: Callable[[], ToolContext], checkpointer=None):
    """`make_ctx` returns a ToolContext (DB session + clinic) for the current step."""

    def with_ctx(fn):
        """Give a node a fresh ToolContext and release its DB session afterwards."""
        def node(state: State) -> State:
            ctx = make_ctx()
            try:
                return fn(state, ctx)
            finally:
                if ctx.owns_session:
                    ctx.session.close()
        node.__name__ = fn.__name__
        return node

    # ---------------------------------------------------------------- nodes
    def intent(state: State) -> State:
        user = next((x["content"] for x in reversed(state["messages"]) if x["role"] == "user"), "")
        prev = next((x["content"] for x in reversed(state["messages"]) if x["role"] == "assistant"), "")
        content = f"Assistant's last message: {prev[:400]}\nPatient: {user}" if prev else user
        ctx = make_ctx()
        try:
            today = ctx.local_now.strftime("%A %Y-%m-%d")
        finally:
            if ctx.owns_session:
                ctx.session.close()
        try:
            res = router.chat("lightning", [{"role": "system", "content": INTENT_PROMPT.format(
                                                today=today, intents=list(INTENTS))},
                                            {"role": "user", "content": content}],
                              purpose="intent", node="intent", conversation_id=state.get("conversation_id"),
                              max_tokens=200, temperature=0)
            data = _parse_json(res.content)
        except ModelUnavailable:
            data = {}
        it = data.get("intent") if data.get("intent") in INTENTS else "info"
        lang = detect_language(user, data.get("language"), state.get("language"))   # sticky (DEVLOG #35)
        upd = {"intent": it, "language": lang, "ext": data, "say": None,
               # a new patient turn: clear this turn's working memory
               "scratch": [], "tool_results": [], "rounds": 0, "pending": None}
        upd["in_flow"] = flow_active({**state, **upd})
        return upd

    def agent(state: State, ctx: ToolContext) -> State:
        system = AGENT_PROMPT.format(clinic=ctx.clinic.name, tz=ctx.clinic.timezone,
                                     today=ctx.local_now.strftime("%A %d %B %Y, %H:%M"))
        if state.get("patient_id"):
            system += f"\nVerified patient_id for this conversation: {state['patient_id']}"
        msgs = [{"role": "system", "content": system}, *state["messages"][-8:], *state.get("scratch", [])]
        try:
            res = router.chat("lightning", msgs, tools=openai_tool_specs(AGENT_TOOLS), purpose="tool_selection",
                              node="agent", conversation_id=state.get("conversation_id"), max_tokens=400, temperature=0)
        except ModelUnavailable:
            return {"pending": None, "scratch": [*state.get("scratch", []), {"role": "assistant", "content": "DONE"}],
                    "rounds": MAX_TOOL_ROUNDS}
        if not res.tool_calls:
            return {"scratch": [*state.get("scratch", []), {"role": "assistant", "content": res.content or "DONE"}],
                    "rounds": state.get("rounds", 0) + 1, "pending": None}
        return {"scratch": [*state.get("scratch", []), res.raw_message],
                "rounds": state.get("rounds", 0) + 1, "pending": None}

    def tools(state: State, ctx: ToolContext) -> State:
        """Run every non-gated call from the last model step; queue the first gated one."""
        scratch = list(state.get("scratch", []))
        results = list(state.get("tool_results", []))
        patient_id = state.get("patient_id")
        hold_id = state.get("hold_id")
        pending = None
        calls = scratch[-1].get("tool_calls", []) if scratch else []
        for c in calls:
            name = c["function"]["name"]
            raw = c["function"].get("arguments") or "{}"
            args = _parse_json(raw) if raw.strip() else {}
            out: dict | None = None
            if name not in AGENT_TOOLS:
                out = {"ok": False, "error": "UNKNOWN_TOOL", "message": "Not available."}
            elif name in NEEDS_PATIENT or name == "send_payment_link":
                if not patient_id:
                    out = {"ok": False, "error": "VERIFY_FIRST",
                           "message": "Verify the patient first with find_patient (phone + name) or register them."}
                elif name == "send_payment_link":
                    row = ctx.session.get(m.Appointment, args.get("appointment_id", ""))
                    if row is None or row.patient_id != patient_id:  # only the verified patient's own visits
                        out = {"ok": False, "error": "NOT_FOUND", "message": "No matching appointment for this patient."}
                else:
                    args["patient_id"] = patient_id                     # never trust a model-supplied id
            elif name == "hold_slot" and patient_id:
                args["patient_id"] = patient_id
            elif name == "register_patient":
                from app.agent.tools import RegisterPatientIn
                try:
                    RegisterPatientIn.model_validate({**args, "consent": True})
                    problem = _typed_by_patient(args, state.get("messages", []))
                except Exception:  # noqa: BLE001
                    problem = "Ask the patient for their name, age and phone number."
                if problem:
                    out = {"ok": False, "error": "MISSING_DETAILS", "message": problem}
                elif state.get("consented"):                            # already agreed in this conversation
                    out = run_tool(name, {**args, "consent": True}, ctx)
            if out is None and name in GATED:
                if pending is None:
                    pending = {"call": {"id": c["id"], "name": name, "args": args}}
                    continue                                            # result added after the patient answers
                out = {"ok": False, "error": "ONE_AT_A_TIME", "message": "Only one action can be confirmed at a time."}
            if out is None:
                out = run_tool(name, args, ctx)
            if name in SETS_PATIENT and out.get("ok"):
                patient_id = out["patient_id"]
            if name == "hold_slot" and out.get("ok"):
                hold_id = out["hold_id"]
            results.append({"tool": name, "args": {k: v for k, v in args.items() if k != "phone"}, "result": out})
            scratch.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)})
        if pending is None and hold_id and patient_id and hold_id != state.get("hold_id"):
            # A fresh hold: go straight to the booking confirmation card.
            call = {"id": f"auto_book_{hold_id[:8]}", "name": "book",
                    "args": {"hold_id": hold_id, "patient_id": patient_id}}
            scratch.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": call["id"], "type": "function",
                 "function": {"name": "book", "arguments": json.dumps({"hold_id": hold_id})}}]})
            pending = {"call": call}
        if pending:
            kind, summary = _summary(ctx, pending["call"], state.get("language", "en"))
            if not kind:                                              # invalid target: let the tool explain why
                call = pending["call"]
                out = run_tool(call["name"], call["args"], ctx)
                results.append({"tool": call["name"], "args": call["args"], "result": out})
                scratch.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(out, default=str)})
                pending = None
            else:
                pending.update(kind=kind, summary=summary)
        return {"scratch": scratch, "tool_results": results, "patient_id": patient_id, "pending": pending,
                "hold_id": hold_id}

    def confirm(state: State) -> State:
        """Pause for the patient. On resume, the answer decides whether the action runs."""
        p = state["pending"]
        answer = interrupt({"kind": p["kind"], "action": p["call"]["name"], "summary": p["summary"],
                            "options": ["yes", "no"]})
        return {"pending": {**p, "approved": is_yes(answer)}}

    def execute(state: State, ctx: ToolContext) -> State:
        p = state["pending"]
        call = p["call"]
        args = dict(call["args"])
        if call["name"] == "register_patient":
            args["consent"] = p["approved"]                             # the patient's answer, not the model's
        if p["approved"]:
            out = run_tool(call["name"], args, ctx)
        elif call["name"] == "register_patient":
            out = {"ok": False, "error": "PATIENT_DECLINED", "message": "The patient did not consent; nothing was stored."}
        else:
            out = {"ok": False, "error": "PATIENT_DECLINED", "message": "The patient said no; nothing was changed."}
        patient_id = state.get("patient_id")
        if call["name"] in SETS_PATIENT and out.get("ok"):
            patient_id = out["patient_id"]
        extra: dict = {"ext": {}}
        if call["name"] == "register_patient" and p["approved"]:
            extra["consented"] = True
        if call["name"] == "book":
            extra["hold_id"] = None
        return {**extra, "scratch": [*state["scratch"], {"role": "tool", "tool_call_id": call["id"],
                                                "content": json.dumps(out, default=str)}],
                "tool_results": [*state["tool_results"], {"tool": call["name"],
                                                          "args": {k: v for k, v in args.items() if k != "phone"},
                                                          "result": out}],
                "patient_id": patient_id, "pending": None}

    def flow(state: State, ctx: ToolContext) -> State:
        upd = run_flow(state, ctx)
        p = upd.get("pending")
        if p:
            kind, summary = _summary(ctx, p["call"], state.get("language", "en"))
            if kind:
                p.update(kind=kind, summary=summary)
            else:
                upd["pending"] = None
        else:
            upd["pending"] = None
        return upd

    def respond(state: State, ctx: ToolContext) -> State:
        results = state.get("tool_results", [])
        say = state.get("say")
        if say is not None:                                   # booking step: templates, no model (DEVLOG #32)
            reply = render(say, state.get("language", "en"))
            return {"reply": reply, "messages": [{"role": "assistant", "content": reply}]}
        lang_name = LANG_NAMES.get(state.get("language", "en"), "English")
        user = next((x["content"] for x in reversed(state["messages"]) if x["role"] == "user"), "")
        facts = json.dumps([{"tool": r["tool"], "result": _public_result(r["result"])} for r in results],
                           ensure_ascii=False, default=str)
        system = RESPOND_PROMPT.format(clinic=ctx.clinic.name, tool_results=facts, language=lang_name)
        allowed = facts + " " + user
        msgs = [{"role": "system", "content": system}, *state["messages"][-8:]]
        reply = ""
        for attempt in range(2):
            try:
                res = router.chat("super", msgs, purpose="reply", node="respond",
                                  conversation_id=state.get("conversation_id"), max_tokens=150, temperature=0.3)
                reply = res.content or ""
            except ModelUnavailable:
                reply = ""
                break
            problem = _reply_problem(reply, results, allowed)
            if not problem:
                break
            log.warning("reply rejected (%s); regenerating", problem)
            msgs = [*msgs, {"role": "assistant", "content": reply}, {"role": "system", "content": problem}]
        else:
            if _reply_problem(reply, results, allowed):
                reply = "I haven't booked anything yet. Shall I check the available times for you?"
        reply = shorten(scrub_ids(reply))
        if not reply:
            reply = "Sorry, I'm having trouble right now. Our clinic staff will help you shortly."
        return {"reply": reply, "messages": [{"role": "assistant", "content": reply}]}

    # ---------------------------------------------------------------- edges
    def after_intent(state: State) -> str:
        if state.get("in_flow"):
            return "flow"
        if state.get("intent") in DATA_INTENTS or state.get("hold_id"):
            return "agent"
        return "respond"

    def after_flow(state: State) -> str:
        return "confirm" if state.get("pending") else "respond"

    def after_execute(state: State) -> str:
        return "flow" if state.get("in_flow") else "agent"

    def after_agent(state: State) -> str:
        last = state["scratch"][-1] if state.get("scratch") else {}
        if last.get("tool_calls") and state.get("rounds", 0) <= MAX_TOOL_ROUNDS:
            return "tools"
        return "respond"

    def after_tools(state: State) -> str:
        if state.get("pending"):
            return "confirm"
        return "agent" if state.get("rounds", 0) < MAX_TOOL_ROUNDS else "respond"

    g = StateGraph(State)
    for name, fn in (("intent", intent), ("flow", with_ctx(flow)), ("agent", with_ctx(agent)), ("tools", with_ctx(tools)),
                     ("confirm", confirm), ("execute", with_ctx(execute)), ("respond", with_ctx(respond))):
        g.add_node(name, fn)
    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", after_intent, {"flow": "flow", "agent": "agent", "respond": "respond"})
    g.add_conditional_edges("flow", after_flow, {"confirm": "confirm", "respond": "respond"})
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "respond": "respond"})
    g.add_conditional_edges("tools", after_tools, {"confirm": "confirm", "agent": "agent", "respond": "respond"})
    g.add_edge("confirm", "execute")
    g.add_conditional_edges("execute", after_execute, {"flow": "flow", "agent": "agent"})
    g.add_edge("respond", END)
    if checkpointer is None:                    # pausing for the patient needs somewhere to keep state
        from langgraph.checkpoint.memory import InMemorySaver
        checkpointer = InMemorySaver()
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------- helpers for callers
def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _outcome(result: dict) -> dict:
    interrupts = result.get("__interrupt__") or []
    return {"reply": None if interrupts else result.get("reply"),
            "interrupt": interrupts[0].value if interrupts else None,
            "intent": result.get("intent"), "language": result.get("language"),
            "patient_id": result.get("patient_id"),
            "tools": [r["tool"] for r in result.get("tool_results", [])],
            "tool_results": result.get("tool_results", [])}


def send(graph, thread_id: str, text: str, conversation_id: str | None = None) -> dict:
    """One patient message. Returns {"reply"} or, if the graph paused, {"interrupt": {kind, summary, options}}."""
    payload: dict = {"messages": [{"role": "user", "content": text}]}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return _outcome(graph.invoke(payload, _config(thread_id)))


def answer(graph, thread_id: str, approved: bool | str) -> dict:
    """The patient's answer to a paused confirmation or consent question."""
    return _outcome(graph.invoke(Command(resume={"approved": is_yes(approved)}), _config(thread_id)))


def pending_interrupt(graph, thread_id: str) -> dict | None:
    """The question the conversation is waiting on, if any (e.g. after a reconnect or restart)."""
    snap = graph.get_state(_config(thread_id))
    for task in snap.tasks or ():
        for i in task.interrupts or ():
            return i.value
    return None
