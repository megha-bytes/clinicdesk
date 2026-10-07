"""Model router: one place that talks to Nebius Token Factory (plan §5.1).

Tiers (IDs confirmed on Token Factory, 6 Oct 2026; overridable in config/models.yaml)
- lightning  Nemotron 3.5 Lightning: intent, rail checks, tool selection (fast, parallel tool calls)
- super      Nemotron 3 Super: patient-facing replies
- ultra      Nemotron 3 Ultra: summaries and reviews
- nano       Nemotron 3 Nano: fallback for the fast tier

Reasoning: patient-facing and fast-tier calls run with reasoning OFF. The router asks for it with
`chat_template_kwargs.enable_thinking=false` (how vLLM serves Nemotron 3), strips any <think> blocks
that still come back, and *detects* untagged reasoning (Lightning once replied "Here's a thinking
process: …"). A leaked reply is never returned: the router retries with a system-prompt switch,
then falls back to the next tier.

Every call is recorded: an OpenTelemetry span with GenAI attributes, and a ledger record with
tokens, cost and latency (written to the `llm_call` table by `DbLedger`).
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

from app.telemetry import tracer

log = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]

Tier = Literal["lightning", "super", "ultra", "nano"]

DEFAULT_MODELS: dict[str, str] = {
    "lightning": "nvidia/Nemotron-3_5-Lightning",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
}
# USD per 1M tokens. UNVERIFIED third-party listings (Oct 2026); Lightning estimated at Nano's price.
# Defaults live in code because Render builds only backend/, so config/*.yaml isn't in the container;
# the YAML files (repo root) override these when present, e.g. on a laptop.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.20, 0.80),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (0.50, 2.50),
}
DEFAULT_TIMEOUT_S = {"lightning": 15, "super": 30, "ultra": 60, "nano": 15}
# Reasoning off by default except where heavy reasoning is the point.
DEFAULT_REASONING = {"lightning": False, "super": False, "ultra": True, "nano": False}
FALLBACKS: dict[str, list[str]] = {"lightning": ["nano"], "nano": [], "super": ["ultra"], "ultra": []}


# ----------------------------------------------------------------------------- config
@dataclass
class TierConfig:
    model: str
    timeout_s: float
    reasoning: bool
    price_in: float = 0.0     # USD per 1M input tokens
    price_out: float = 0.0    # USD per 1M output tokens


def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def load_tiers(models_path: Path | None = None, pricing_path: Path | None = None) -> dict[str, TierConfig]:
    models_cfg = _load_yaml(models_path or REPO_ROOT / "config" / "models.yaml").get("tiers", {}) or {}
    prices = _load_yaml(pricing_path or REPO_ROOT / "config" / "pricing.yaml").get("models", {}) or {}
    tiers: dict[str, TierConfig] = {}
    for tier, default_model in DEFAULT_MODELS.items():
        cfg = models_cfg.get(tier) or {}
        model = cfg.get("model")
        if not model or str(model).upper().startswith("TODO"):
            model = default_model
        p = prices.get(model) or {}
        d_in, d_out = DEFAULT_PRICES.get(model, (0.0, 0.0))
        tiers[tier] = TierConfig(
            model=model,
            timeout_s=float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S[tier]),
            reasoning=bool(cfg.get("reasoning", DEFAULT_REASONING[tier])),
            price_in=float(p.get("input", d_in) or 0.0), price_out=float(p.get("output", d_out) or 0.0),
        )
    return tiers


# ----------------------------------------------------------------------------- reasoning hygiene
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_LEAK_START = re.compile(
    r"^\s*(here'?s a thinking process|thinking process:|okay,? (so )?(let'?s|let me|the user)|"
    r"let me think|we need to|first,? (i|let)|the user (is asking|asks|wants)|\*\*analy[sz]e)", re.I)


def clean_reply(text: str | None) -> tuple[str, bool]:
    """Returns (clean_text, leaked). Removes <think> blocks; flags untagged reasoning."""
    if not text:
        return "", False
    t = _THINK_BLOCK.sub("", text)
    lower = t.lower()
    close = lower.rfind("</think>")
    if close != -1:                                   # opening tag lived in the prompt template
        t = t[close + len("</think>"):]
    elif "<think>" in lower:                          # unterminated: everything after is reasoning
        t = t[:lower.index("<think>")]
        if not t.strip():
            return "", True
    t = t.strip()
    return t, bool(_LEAK_START.match(t))


# ----------------------------------------------------------------------------- results & ledger
@dataclass
class ChatResult:
    tier: str
    model: str
    content: str
    tool_calls: list[dict] = field(default_factory=list)   # [{"id","name","arguments"}]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    attempts: int = 1
    fell_back_from: str | None = None
    raw_message: dict | None = None                      # assistant message to append to history


@dataclass
class LedgerRecord:
    tier: str
    model: str
    purpose: str | None
    node: str | None
    conversation_id: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    ok: bool
    trace_id: str | None = None
    span_id: str | None = None


class Ledger(Protocol):
    def record(self, rec: LedgerRecord) -> None: ...


class MemoryLedger:
    def __init__(self) -> None:
        self.records: list[LedgerRecord] = []

    def record(self, rec: LedgerRecord) -> None:
        self.records.append(rec)


class DbLedger:
    """Writes one llm_call row per model call (its own short session, so it never interferes
    with the request's transaction)."""

    def record(self, rec: LedgerRecord) -> None:
        from app import models as m
        from app.db import SessionLocal

        try:
            with SessionLocal() as s:
                s.add(m.LLMCall(conversation_id=rec.conversation_id, trace_id=rec.trace_id, span_id=rec.span_id,
                                node=rec.node, model=rec.model, tier=rec.tier, purpose=rec.purpose,
                                input_tokens=rec.input_tokens, output_tokens=rec.output_tokens,
                                cost_usd=rec.cost_usd, latency_ms=rec.latency_ms, ok=rec.ok))
                s.commit()
        except Exception:  # noqa: BLE001  (never break a patient conversation over bookkeeping)
            log.exception("could not write llm_call row")


def cost_usd(cfg: TierConfig, tokens_in: int, tokens_out: int) -> float:
    return round(tokens_in * cfg.price_in / 1e6 + tokens_out * cfg.price_out / 1e6, 8)


# ----------------------------------------------------------------------------- router
class ModelUnavailable(RuntimeError):
    pass


class ChatClient(Protocol):
    """The slice of the OpenAI client we use (easy to fake in tests)."""
    chat: Any


class ModelRouter:
    def __init__(self, client: ChatClient, tiers: dict[str, TierConfig] | None = None,
                 ledger: Ledger | None = None, clock: Callable[[], float] = time.perf_counter) -> None:
        self.client = client
        self.tiers = tiers or load_tiers()
        self.ledger = ledger or MemoryLedger()
        self.clock = clock
        self.kwargs_unsupported: set[str] = set()   # models that reject chat_template_kwargs

    def chat(self, tier: Tier, messages: list[dict], *, tools: list[dict] | None = None,
             purpose: str | None = None, node: str | None = None, conversation_id: str | None = None,
             max_tokens: int = 600, temperature: float = 0.2, json_mode: bool = False) -> ChatResult:
        chain = [tier, *FALLBACKS.get(tier, [])]
        last_error: Exception | None = None
        for i, t in enumerate(chain):
            try:
                res = self._call_tier(t, messages, tools, purpose, node, conversation_id,
                                      max_tokens, temperature, json_mode)
                if i:
                    res.fell_back_from = tier
                return res
            except Exception as e:  # noqa: BLE001
                last_error = e
                log.warning("tier %s failed (%s); trying fallback", t, type(e).__name__)
        raise ModelUnavailable(f"all tiers failed for {tier}: {last_error}") from last_error

    # One tier: up to two attempts (second uses the system-prompt reasoning switch).
    def _call_tier(self, tier, messages, tools, purpose, node, conversation_id, max_tokens, temperature, json_mode):
        cfg = self.tiers[tier]
        if cfg.reasoning:
            styles = ["none"]
        elif cfg.model in self.kwargs_unsupported:
            styles = ["system"]
        else:
            styles = ["kwargs", "system"]
        for n, style in enumerate(styles, 1):
            try:
                res = self._request(tier, cfg, messages, tools, style, purpose, node, conversation_id,
                                    max_tokens, temperature, json_mode)
            except Exception as e:  # noqa: BLE001
                # A 400/422 on the kwargs style means this deployment doesn't accept the switch:
                # remember that and use the system-prompt switch from now on. Anything else is a real failure.
                if style == "kwargs" and getattr(e, "status_code", None) in (400, 422):
                    log.info("%s rejects chat_template_kwargs; using the /no_think switch", cfg.model)
                    self.kwargs_unsupported.add(cfg.model)
                    continue
                raise
            res.attempts = n
            if res.tool_calls or res.content:
                return res
            log.info("tier %s returned leaked/empty reasoning (attempt %d)", tier, n)
        raise ModelUnavailable(f"{tier} kept returning reasoning instead of an answer")

    def _request(self, tier, cfg: TierConfig, messages, tools, style, purpose, node, conversation_id,
                 max_tokens, temperature, json_mode) -> ChatResult:
        msgs = list(messages)
        extra: dict[str, Any] = {}
        if style == "kwargs":
            extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        elif style == "system":
            msgs = [{"role": "system", "content": "/no_think"}, *msgs]
        if tools:
            extra["tools"] = tools
            extra["tool_choice"] = "auto"
        if json_mode:
            extra["response_format"] = {"type": "json_object"}

        with tracer.start_as_current_span(f"gen_ai.chat {tier}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.system", "nebius_token_factory")
            span.set_attribute("gen_ai.request.model", cfg.model)
            span.set_attribute("clinicdesk.tier", tier)
            span.set_attribute("clinicdesk.reasoning_style", style)
            if purpose:
                span.set_attribute("clinicdesk.purpose", purpose)
            t0 = self.clock()
            ok = False
            usage_in = usage_out = 0
            try:
                resp = self.client.chat.completions.create(
                    model=cfg.model, messages=msgs, max_tokens=max_tokens, temperature=temperature,
                    timeout=cfg.timeout_s, **extra)
                ok = True
            finally:
                latency = (self.clock() - t0) * 1000
                if ok:
                    u = getattr(resp, "usage", None)
                    usage_in = getattr(u, "prompt_tokens", 0) or 0
                    usage_out = getattr(u, "completion_tokens", 0) or 0
                cost = cost_usd(cfg, usage_in, usage_out)
                span.set_attribute("gen_ai.usage.input_tokens", usage_in)
                span.set_attribute("gen_ai.usage.output_tokens", usage_out)
                span.set_attribute("clinicdesk.cost_usd", cost)
                ctx = span.get_span_context()
                self.ledger.record(LedgerRecord(
                    tier=tier, model=cfg.model, purpose=purpose, node=node, conversation_id=conversation_id,
                    input_tokens=usage_in, output_tokens=usage_out, cost_usd=cost, latency_ms=round(latency, 1),
                    ok=ok, trace_id=format(ctx.trace_id, "032x") if ctx.trace_id else None,
                    span_id=format(ctx.span_id, "016x") if ctx.span_id else None))

        msg = resp.choices[0].message
        content, leaked = clean_reply(getattr(msg, "content", None))
        calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                 for c in (getattr(msg, "tool_calls", None) or [])]
        span_leak = leaked and not calls
        raw = {"role": "assistant", "content": content or None}
        if calls:
            raw["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
        return ChatResult(tier=tier, model=cfg.model, content="" if span_leak else content, tool_calls=calls,
                          input_tokens=usage_in, output_tokens=usage_out, latency_ms=round(latency, 1),
                          cost_usd=cost, raw_message=raw)


@lru_cache
def default_router() -> ModelRouter:
    """Router backed by the real Token Factory client and the database ledger."""
    from openai import OpenAI

    from app.settings import get_settings

    s = get_settings()
    key = s.token_factory_api_key
    if key is None:
        raise RuntimeError("TOKEN_FACTORY_API_KEY is not set")
    client = OpenAI(base_url=s.token_factory_base_url, api_key=key.get_secret_value(), max_retries=1)
    return ModelRouter(client, ledger=DbLedger())
