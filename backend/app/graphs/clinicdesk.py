"""ClinicDeskGraph v1 (Day 6): intent → tools loop → reply.

    START → intent (Lightning, JSON) ─┬─ needs data → tools (Lightning picks tools, engine answers) ─┐
                                      └─ chit-chat / unsupported ─────────────────────────────────────┴→ respond (Super) → END

Rules baked in
- The reply model (Super) only sees facts that came back from tools, and is told to state nothing else.
- Tool selection uses the fast tier with reasoning off; the loop stops after MAX_TOOL_ROUNDS.
- Day 6 exposes read-only tools. Booking, consent and confirmation steps arrive on Day 7;
  guardrail nodes on Days 9–10.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.models import ModelRouter, ModelUnavailable
from app.agent.tools import ToolContext, openai_tool_specs, run_tool

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
INTENTS = ("info", "book", "reschedule", "cancel", "queue", "pay", "human", "smalltalk", "other")
DATA_INTENTS = {"info", "book", "reschedule", "cancel", "queue", "pay"}
READ_ONLY_TOOLS = ["get_clinic_info", "route_to_doctor", "find_slots"]
LANG_NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada"}

INTENT_PROMPT = """You classify messages sent to a clinic's front desk.
Return ONLY a JSON object: {"intent": <one of %s>, "language": <"en" | "hi" | "kn" | "other">}.
- info: timings, fees, doctors, address, availability questions
- book / reschedule / cancel / queue (token or wait time) / pay
- human: wants to talk to a person
- smalltalk: greetings, thanks
- other: anything else (including medical questions)
Language is the language the patient wrote in (Hindi in Latin script counts as "hi").""" % (list(INTENTS),)

TOOLS_PROMPT = """You are the data step of a clinic front-desk assistant for {clinic}.
Call tools to fetch the facts needed to answer the patient's latest message. Today is {today} ({tz}).
- Use get_clinic_info for timings, fees, doctors and address.
- Use route_to_doctor to find which doctor handles a need, then find_slots for availability.
- Call several tools at once when useful. When you have enough facts, reply with the single word DONE.
Never answer the patient yourself and never guess."""

RESPOND_PROMPT = """You are the friendly receptionist of {clinic}. Reply in {language}, in 1–3 short sentences.
Hard rules:
- State ONLY times, dates, fees, doctor names, token numbers and wait times that appear in TOOL RESULTS below.
  If the needed fact is not there, say you'll check with the clinic staff. Never guess.
- Never give medical advice, diagnosis, or medication information. Offer to book a doctor instead.
- Booking changes aren't available in this version yet: if asked to book, share the options from TOOL RESULTS
  and say the clinic team will confirm.
TOOL RESULTS:
{tool_results}"""


class State(TypedDict, total=False):
    messages: list[dict]          # chat history: {"role": "user"|"assistant", "content": str}
    intent: str
    language: str
    tool_results: list[dict]      # [{"tool", "args", "result"}]
    reply: str
    conversation_id: str | None


def _last_user(state: State) -> str:
    return next((m["content"] for m in reversed(state["messages"]) if m["role"] == "user"), "")


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def build_graph(router: ModelRouter, make_ctx: Callable[[], ToolContext]):
    """`make_ctx` returns a ToolContext (DB session + clinic) for the current turn."""

    def intent(state: State) -> State:
        try:
            res = router.chat("lightning", [{"role": "system", "content": INTENT_PROMPT},
                                            {"role": "user", "content": _last_user(state)}],
                              purpose="intent", node="intent", conversation_id=state.get("conversation_id"),
                              max_tokens=60, temperature=0)
            data = _parse_json(res.content)
        except ModelUnavailable:
            data = {}
        it = data.get("intent") if data.get("intent") in INTENTS else "info"
        lang = data.get("language") if data.get("language") in LANG_NAMES else "en"
        return {"intent": it, "language": lang, "tool_results": []}

    def route(state: State) -> str:
        return "tools" if state.get("intent") in DATA_INTENTS else "respond"

    def tools(state: State) -> State:
        ctx = make_ctx()
        clinic = ctx.clinic
        specs = openai_tool_specs(READ_ONLY_TOOLS)
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": TOOLS_PROMPT.format(
                clinic=clinic.name, today=ctx.local_now.strftime("%A %d %B %Y, %H:%M"), tz=clinic.timezone)},
            *state["messages"][-6:],
        ]
        results: list[dict] = []
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                res = router.chat("lightning", msgs, tools=specs, purpose="tool_selection", node="tools",
                                  conversation_id=state.get("conversation_id"), max_tokens=300, temperature=0)
            except ModelUnavailable:
                break
            if not res.tool_calls:
                break
            msgs.append(res.raw_message)
            for call in res.tool_calls:
                args = _parse_json(call["arguments"]) if call["arguments"].strip() else {}
                if call["name"] not in READ_ONLY_TOOLS:
                    out = {"ok": False, "error": "UNKNOWN_TOOL", "message": "Not available in this step."}
                else:
                    out = run_tool(call["name"], args, ctx)
                results.append({"tool": call["name"], "args": args, "result": out})
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(out, default=str)})
        return {"tool_results": results}

    def respond(state: State) -> State:
        ctx = make_ctx()
        facts = json.dumps([{"tool": r["tool"], "result": r["result"]} for r in state.get("tool_results", [])],
                           ensure_ascii=False, default=str) or "[]"
        system = RESPOND_PROMPT.format(clinic=ctx.clinic.name,
                                       language=LANG_NAMES.get(state.get("language", "en"), "English"),
                                       tool_results=facts)
        try:
            res = router.chat("super", [{"role": "system", "content": system}, *state["messages"][-8:]],
                              purpose="reply", node="respond", conversation_id=state.get("conversation_id"),
                              max_tokens=400, temperature=0.3)
            reply = res.content
        except ModelUnavailable:
            reply = ""
        if not reply:
            reply = "Sorry, I'm having trouble right now. Our clinic staff will help you shortly."
        return {"reply": reply, "messages": [*state["messages"], {"role": "assistant", "content": reply}]}

    g = StateGraph(State)
    g.add_node("intent", intent)
    g.add_node("tools", tools)
    g.add_node("respond", respond)
    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", route, {"tools": "tools", "respond": "respond"})
    g.add_edge("tools", "respond")
    g.add_edge("respond", END)
    return g.compile()


def ask(graph, history: list[dict], text: str, conversation_id: str | None = None) -> State:
    """Run one patient turn. Returns the final state (reply, intent, language, tool_results, messages)."""
    return graph.invoke({"messages": [*history, {"role": "user", "content": text}],
                         "conversation_id": conversation_id})
