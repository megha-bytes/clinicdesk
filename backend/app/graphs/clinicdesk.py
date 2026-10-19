"""ClinicDeskGraph v2 (Day 7): booking, consent and confirmation inside LangGraph.

    START → intent ──┬─ smalltalk/other ────────────────────────────────────────────┐
                     └─ data/booking → agent ⇄ tools                                  │
                                         │  (gated action)                            │
                                         └→ confirm ⏸ interrupt → execute → agent     │
                                         └─ (no more tool calls) ───────────────────→ respond → END

Safety rules enforced by the graph, not the prompt
- **Consent**: `register_patient` never runs on the model's say-so. The graph pauses with a fixed
  consent notice and uses only the patient's answer (the model's `consent` argument is ignored).
- **Confirmation**: `book`, `reschedule` and `cancel` pause with a summary built from the database
  (doctor, time, fee), never from model text, and run only on an explicit "yes".
- **Identity**: tools that act on a patient use the patient verified *in this conversation*
  (via `find_patient` or `register_patient`); a model-supplied patient_id is overwritten.
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

INTENT_PROMPT = """You classify messages sent to a clinic's front desk.
Return ONLY a JSON object: {"intent": <one of %s>, "language": <"en" | "hi" | "kn" | "other">}.
- info: timings, fees, doctors, address, availability questions
- book / reschedule / cancel / queue (token or wait time) / pay
- human: wants to talk to a person
- smalltalk: greetings, thanks
- other: anything else (including medical questions)
Language is the language the patient wrote in (Hindi in Latin script counts as "hi").""" % (list(INTENTS),)

AGENT_PROMPT = """You are the action step of the front desk of {clinic}. Today is {today} ({tz}).
Decide which tools to call for the patient's latest message. You never write to the patient yourself.
Workflow for appointments:
1. Identify the patient: an existing patient → find_patient(phone, name). A new patient → register_patient
   with name, age, phone and language (the clinic will ask the patient for consent; just call it).
   If you don't have these details yet, reply DONE so the receptionist can ask for them.
2. Find options: route_to_doctor (if no doctor named) → find_slots.
3. When the patient has chosen an option, hold_slot with that exact `start`, then book with the hold_id.
   The clinic asks the patient to confirm before booking; just call book.
4. After booking, call send_payment_link.
Reschedule/cancel/queue: identify the patient first, then use their appointment_id from find_patient.
If the patient asks for a human, call handoff_to_staff. When nothing more is needed, reply with the word DONE.
Never invent ids, times or fees; use only values returned by tools."""

RESPOND_PROMPT = """You are the friendly receptionist of {clinic}. Reply in {language}, in 1–3 short sentences.
Hard rules:
- State ONLY times, dates, fees, doctor names, token numbers, wait times and links that appear in TOOL RESULTS.
  If a needed fact is missing, say you'll check with the clinic staff. Never guess.
- Never give medical advice, diagnosis or medication information. Offer to book a doctor instead.
- If you need details to continue (name, age, phone number, preferred day or time, which option), ask for them.
- If a tool result says PATIENT_DECLINED, accept it politely. If it says VERIFY_FIRST, ask for phone number and name.
- If slots are listed, offer them as a short numbered list.
TOOL RESULTS (this turn):
{tool_results}"""

CONSENT_TEXT = {
    "en": ("To book appointments we store your name, age, phone number, language and a short reason for the visit, "
           "only to manage your appointments at {clinic}. You can ask us to delete it at any time. Do you agree?"),
    "hi": ("अपॉइंटमेंट के लिए हम आपका नाम, उम्र, फ़ोन नंबर, भाषा और विज़िट का छोटा कारण सेव करते हैं, सिर्फ़ {clinic} में "
           "आपकी अपॉइंटमेंट संभालने के लिए। आप कभी भी इसे हटाने के लिए कह सकते हैं। क्या आप सहमत हैं?"),
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
    rounds: int
    reply: str
    conversation_id: str | None


def _parse_json(text: str) -> dict:
    m_ = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m_.group(0)) if m_ else {}
    except json.JSONDecodeError:
        return {}


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
        try:
            res = router.chat("lightning", [{"role": "system", "content": INTENT_PROMPT},
                                            {"role": "user", "content": user}],
                              purpose="intent", node="intent", conversation_id=state.get("conversation_id"),
                              max_tokens=60, temperature=0)
            data = _parse_json(res.content)
        except ModelUnavailable:
            data = {}
        it = data.get("intent") if data.get("intent") in INTENTS else "info"
        lang = data.get("language") if data.get("language") in LANG_NAMES else state.get("language", "en")
        # A new patient turn: clear this turn's working memory.
        return {"intent": it, "language": lang, "scratch": [], "tool_results": [], "rounds": 0, "pending": None}

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
            if out is None and name in GATED:
                if pending is None:
                    pending = {"call": {"id": c["id"], "name": name, "args": args}}
                    continue                                            # result added after the patient answers
                out = {"ok": False, "error": "ONE_AT_A_TIME", "message": "Only one action can be confirmed at a time."}
            if out is None:
                out = run_tool(name, args, ctx)
                if name in SETS_PATIENT and out.get("ok"):
                    patient_id = out["patient_id"]
            results.append({"tool": name, "args": {k: v for k, v in args.items() if k != "phone"}, "result": out})
            scratch.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)})
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
        return {"scratch": scratch, "tool_results": results, "patient_id": patient_id, "pending": pending}

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
        return {"scratch": [*state["scratch"], {"role": "tool", "tool_call_id": call["id"],
                                                "content": json.dumps(out, default=str)}],
                "tool_results": [*state["tool_results"], {"tool": call["name"],
                                                          "args": {k: v for k, v in args.items() if k != "phone"},
                                                          "result": out}],
                "patient_id": patient_id, "pending": None}

    def respond(state: State, ctx: ToolContext) -> State:
        facts = json.dumps([{"tool": r["tool"], "result": r["result"]} for r in state.get("tool_results", [])],
                           ensure_ascii=False, default=str)
        system = RESPOND_PROMPT.format(clinic=ctx.clinic.name, tool_results=facts,
                                       language=LANG_NAMES.get(state.get("language", "en"), "English"))
        try:
            res = router.chat("super", [{"role": "system", "content": system}, *state["messages"][-8:]],
                              purpose="reply", node="respond", conversation_id=state.get("conversation_id"),
                              max_tokens=400, temperature=0.3)
            reply = res.content
        except ModelUnavailable:
            reply = ""
        if not reply:
            reply = "Sorry, I'm having trouble right now. Our clinic staff will help you shortly."
        return {"reply": reply, "messages": [{"role": "assistant", "content": reply}]}

    # ---------------------------------------------------------------- edges
    def after_intent(state: State) -> str:
        return "agent" if state.get("intent") in DATA_INTENTS else "respond"

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
    for name, fn in (("intent", intent), ("agent", with_ctx(agent)), ("tools", with_ctx(tools)),
                     ("confirm", confirm), ("execute", with_ctx(execute)), ("respond", with_ctx(respond))):
        g.add_node(name, fn)
    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", after_intent, {"agent": "agent", "respond": "respond"})
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "respond": "respond"})
    g.add_conditional_edges("tools", after_tools, {"confirm": "confirm", "agent": "agent", "respond": "respond"})
    g.add_edge("confirm", "execute")
    g.add_edge("execute", "agent")
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
