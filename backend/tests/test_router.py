import pytest
import yaml

from app.agent.models import (
    DEFAULT_MODELS, MemoryLedger, ModelRouter, ModelUnavailable, clean_reply, cost_usd, load_tiers,
)
from tests.fakes import FakeClient, msg


def router_with(handler):
    client = FakeClient(handler)
    ledger = MemoryLedger()
    return ModelRouter(client, tiers=load_tiers(), ledger=ledger), client, ledger


# ------------------------------------------------------------------ config
def test_defaults_without_config_files(tmp_path):
    tiers = load_tiers(tmp_path / "none.yaml", tmp_path / "none2.yaml")
    assert {k: v.model for k, v in tiers.items()} == DEFAULT_MODELS
    assert tiers["super"].price_in == 0.20 and tiers["ultra"].price_out == 2.50   # code defaults (Render)
    assert tiers["lightning"].reasoning is False and tiers["ultra"].reasoning is True


def test_yaml_overrides_and_todo_ignored(tmp_path):
    (tmp_path / "m.yaml").write_text(yaml.safe_dump({"tiers": {
        "super": {"model": "TODO-nemotron-3-super-id"}, "nano": {"model": "nvidia/other", "timeout_s": 3}}}))
    (tmp_path / "p.yaml").write_text(yaml.safe_dump({"models": {"nvidia/other": {"input": 1, "output": 2}}}))
    t = load_tiers(tmp_path / "m.yaml", tmp_path / "p.yaml")
    assert t["super"].model == DEFAULT_MODELS["super"]              # TODO placeholder ignored
    assert (t["nano"].model, t["nano"].timeout_s, t["nano"].price_in) == ("nvidia/other", 3.0, 1.0)


def test_cost_formula():
    t = load_tiers()["super"]
    assert cost_usd(t, 1_000_000, 500_000) == pytest.approx(t.price_in + t.price_out / 2)


# ------------------------------------------------------------------ reasoning hygiene
@pytest.mark.parametrize("raw,clean,leaked", [
    ("<think>plan it</think>We open at 9.", "We open at 9.", False),
    ("planning...</think> We open at 9.", "We open at 9.", False),
    ("<think>never closed", "", True),
    ("Here's a thinking process: 1. **Analyze**", "Here's a thinking process: 1. **Analyze**", True),
    ("The user is asking about timings.", "The user is asking about timings.", True),
    ("Dr. Rao sees patients 9 to 1.", "Dr. Rao sees patients 9 to 1.", False),
])
def test_clean_reply(raw, clean, leaked):
    assert clean_reply(raw) == (clean, leaked)


# ------------------------------------------------------------------ routing behaviour
def test_fast_tier_sends_reasoning_off_and_records_cost():
    r, client, ledger = router_with(lambda kw: msg("We open at 9.", prompt_tokens=1000, completion_tokens=100))
    res = r.chat("lightning", [{"role": "user", "content": "timings?"}], purpose="intent")
    req = client.requests[0]
    assert req["model"] == DEFAULT_MODELS["lightning"]
    assert req["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert res.content == "We open at 9." and res.attempts == 1
    [rec] = ledger.records
    assert rec.tier == "lightning" and rec.purpose == "intent" and rec.ok
    assert rec.cost_usd == pytest.approx(1000 * 0.06 / 1e6 + 100 * 0.24 / 1e6)


def test_ultra_keeps_reasoning():
    r, client, _ = router_with(lambda kw: msg("<think>long</think>Summary: patient wants a follow-up."))
    res = r.chat("ultra", [{"role": "user", "content": "summarise"}])
    assert "extra_body" not in client.requests[0]
    assert res.content == "Summary: patient wants a follow-up."


def test_leaked_reasoning_retries_with_system_switch():
    replies = iter([msg("Here's a thinking process: 1. Analyze..."), msg("We open at 9.")])
    r, client, ledger = router_with(lambda kw: next(replies))
    res = r.chat("lightning", [{"role": "user", "content": "timings?"}])
    assert res.content == "We open at 9." and res.attempts == 2
    assert client.requests[1]["messages"][0] == {"role": "system", "content": "/no_think"}
    assert len(ledger.records) == 2                      # both attempts are costed


def test_persistent_leak_falls_back_to_next_tier():
    def handler(kw):
        if kw["model"] == DEFAULT_MODELS["lightning"]:
            return msg("Okay, let me think about this...")
        return msg("We open at 9.")
    r, client, _ = router_with(handler)
    res = r.chat("lightning", [{"role": "user", "content": "timings?"}])
    assert res.tier == "nano" and res.fell_back_from == "lightning" and res.content == "We open at 9."


def test_errors_fall_back_then_raise():
    def handler(kw):
        if kw["model"] == DEFAULT_MODELS["super"]:
            raise TimeoutError("slow")
        return msg("Fine from Ultra.")
    r, _, ledger = router_with(handler)
    res = r.chat("super", [{"role": "user", "content": "hi"}])
    assert res.tier == "ultra" and res.fell_back_from == "super"
    assert [x.ok for x in ledger.records] == [False, True]   # failed call is still recorded

    r2, _, _ = router_with(lambda kw: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(ModelUnavailable):
        r2.chat("super", [{"role": "user", "content": "hi"}])


def test_tool_calls_are_returned_with_history_message():
    r, client, _ = router_with(lambda kw: msg(None, tool_calls=[("get_clinic_info", {}), ("find_slots", {"appointment_type": "new"})]))
    res = r.chat("lightning", [{"role": "user", "content": "slots?"}], tools=[{"type": "function", "function": {"name": "x"}}])
    assert [c["name"] for c in res.tool_calls] == ["get_clinic_info", "find_slots"]
    assert res.raw_message["tool_calls"][1]["function"]["name"] == "find_slots"
    assert client.requests[0]["tool_choice"] == "auto"


class BadRequest(Exception):
    status_code = 400


def test_kwargs_rejected_switches_to_system_prompt_and_remembers():
    def handler(kw):
        if "extra_body" in kw:
            raise BadRequest("unknown field chat_template_kwargs")
        return msg("We open at 9.")
    r, client, _ = router_with(handler)
    res = r.chat("lightning", [{"role": "user", "content": "timings?"}])
    assert res.content == "We open at 9." and res.tier == "lightning"
    assert client.requests[1]["messages"][0]["content"] == "/no_think"
    r.chat("lightning", [{"role": "user", "content": "again"}])
    assert "extra_body" not in client.requests[2]                    # remembered: no wasted 400 next time
    assert DEFAULT_MODELS["lightning"] in r.kwargs_unsupported


def test_timeouts_are_not_mistaken_for_unsupported_switch():
    def handler(kw):
        if kw["model"] == DEFAULT_MODELS["lightning"]:
            raise TimeoutError("slow")
        return msg("From Nano.")
    r, _, _ = router_with(handler)
    res = r.chat("lightning", [{"role": "user", "content": "hi"}])
    assert res.tier == "nano" and not r.kwargs_unsupported


def test_db_ledger_writes_llm_call_rows(session):
    from app import models as m
    from app.agent.models import DbLedger

    r = ModelRouter(FakeClient(lambda kw: msg("ok", prompt_tokens=500, completion_tokens=50)),
                    tiers=load_tiers(), ledger=DbLedger())
    r.chat("super", [{"role": "user", "content": "hi"}], purpose="reply", node="respond")
    row = session.query(m.LLMCall).one()
    assert (row.tier, row.purpose, row.node, row.input_tokens, row.output_tokens) == ("super", "reply", "respond", 500, 50)
    assert row.cost_usd == pytest.approx(500 * 0.20 / 1e6 + 50 * 0.80 / 1e6) and row.ok
