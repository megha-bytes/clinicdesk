"""ClinicDeskGraph v1 with a fake model: the reply must come only from tool results."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.models import DEFAULT_MODELS, MemoryLedger, ModelRouter, load_tiers
from app.agent.tools import ToolContext
from app.graphs.clinicdesk import ask, build_graph
from app.seed import seed
from tests.fakes import FakeClient, msg

NOW = datetime(2026, 10, 12, 9, 47, tzinfo=ZoneInfo("Asia/Kolkata"))


def scripted_handler(intent_json='{"intent": "info", "language": "en"}'):
    """Lightning: intent → one get_clinic_info call → DONE. Super: writes a reply from TOOL RESULTS."""
    def handler(kw):
        system = kw["messages"][0]["content"] if kw["messages"][0]["role"] == "system" else ""
        if "You classify messages" in system:                           # intent step
            return msg(intent_json)
        if kw.get("tools"):                                             # tool step
            if any(m.get("role") == "tool" for m in kw["messages"]):
                return msg("DONE")
            return msg(None, tool_calls=[("get_clinic_info", {})])
        # reply step: build the answer ONLY from what's in TOOL RESULTS
        assert kw["model"] == DEFAULT_MODELS["super"]
        facts = json.loads(system.split("TOOL RESULTS:\n", 1)[1])
        info = next((f["result"] for f in facts if f["tool"] == "get_clinic_info"), None)
        if not info:
            return msg("I'll check with the clinic staff.")
        rao = next(d for d in info["doctors"] if d["name"] == "Dr. Ananya Rao")
        fee = next(t["fee_inr"] for t in info["appointment_types"] if t["name"] == "new")
        hours = rao["hours"]["mon"]
        return msg(f"Dr. Ananya Rao sees patients {hours[0][0]}–{hours[0][1]} and {hours[1][0]}–{hours[1][1]}. "
                   f"A new consultation is ₹{fee}.")
    return handler


def setup(session, handler):
    clinic_id = seed(session, now=NOW)["clinic_id"]
    client, ledger = FakeClient(handler), MemoryLedger()
    router = ModelRouter(client, tiers=load_tiers(), ledger=ledger)
    graph = build_graph(router, lambda: ToolContext(session=session, clinic_id=clinic_id, now=NOW))
    return graph, client, ledger


def test_answers_timings_and_fee_from_tools_only(session):
    graph, client, ledger = setup(session, scripted_handler())
    state = ask(graph, [], "What are Dr. Rao's timings and fee?")
    assert state["intent"] == "info" and state["language"] == "en"
    assert [r["tool"] for r in state["tool_results"]] == ["get_clinic_info"]
    assert "09:00–13:00" in state["reply"] and "17:00–20:00" in state["reply"] and "₹500" in state["reply"]
    assert [rec.purpose for rec in ledger.records] == ["intent", "tool_selection", "tool_selection", "reply"]
    assert all(rec.cost_usd > 0 for rec in ledger.records)
    assert state["messages"][-1] == {"role": "assistant", "content": state["reply"]}


def test_smalltalk_skips_tools(session):
    def handler(kw):
        if "You classify messages" in kw["messages"][0]["content"]:
            return msg('{"intent": "smalltalk", "language": "hi"}')
        assert not kw.get("tools"), "no tool step for smalltalk"
        return msg("नमस्ते! मैं आपकी कैसे मदद कर सकती हूँ?")
    graph, client, ledger = setup(session, handler)
    state = ask(graph, [], "Namaste")
    assert state["intent"] == "smalltalk" and state["language"] == "hi" and state["tool_results"] == []
    assert "Hindi" in client.requests[-1]["messages"][0]["content"]       # reply told to use Hindi


def test_intent_json_wrapped_in_prose_is_parsed(session):
    graph, _, _ = setup(session, scripted_handler(intent_json='Sure! {"intent": "info", "language": "kn"} '))
    assert ask(graph, [], "ಸಮಯ?")["language"] == "kn"


def test_bad_intent_json_defaults_to_info(session):
    graph, _, _ = setup(session, scripted_handler(intent_json="not json at all"))
    state = ask(graph, [], "timings?")
    assert state["intent"] == "info" and state["tool_results"]


def test_model_cannot_call_write_tools_in_v1(session):
    def handler(kw):
        if "You classify messages" in kw["messages"][0]["content"]:
            return msg('{"intent": "book", "language": "en"}')
        if kw.get("tools"):
            names = {t["function"]["name"] for t in kw["tools"]}
            assert names == {"get_clinic_info", "route_to_doctor", "find_slots"}   # read-only in v1
            if any(m.get("role") == "tool" for m in kw["messages"]):
                return msg("DONE")
            return msg(None, tool_calls=[("book", {"hold_id": "x", "patient_id": "y"})])
        return msg("The clinic team will confirm your booking.")
    graph, _, _ = setup(session, handler)
    state = ask(graph, [], "Book me with Dr. Rao")
    assert state["tool_results"][0]["result"]["error"] == "UNKNOWN_TOOL"


def test_model_outage_gives_safe_fallback_reply(session):
    def handler(kw):
        raise ConnectionError("Token Factory down")
    graph, _, _ = setup(session, handler)
    state = ask(graph, [], "What are your timings?")
    assert "clinic staff" in state["reply"]
