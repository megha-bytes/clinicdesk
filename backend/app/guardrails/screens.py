"""Guardrail checks and the runner.

Input rails (in order): emergency → prompt injection → other patients' data → medical advice.
Output rail (model-written replies only): medical advice, phone numbers, fees and times not in the facts.

The policy (which rails, in which order) lives in NeMo Guardrails Colang (config/rails.co) and runs
through `nemoguardrails` when it is installed (the deployed container, CI). Where it isn't installable
(e.g. Python 3.14 on a laptop) the same actions run directly in the same order. Either way every check
is deterministic, so emergencies are caught even when the model provider is down.
"""
from __future__ import annotations

import contextvars
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.guardrails import rules

log = logging.getLogger(__name__)

INPUT_RAILS = [("emergency_screen", "screen_emergency"), ("injection_screen", "screen_injection"),
               ("privacy_screen", "screen_privacy"), ("medical_advice_screen", "screen_medical")]

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("guard_ctx", default={})


@dataclass
class Verdict:
    ok: bool = True
    kind: str = "ok"            # emergency | injection | privacy | medical | output_* | ok
    rail: str | None = None
    detail: str = ""            # emergency sub-kind or why an output was blocked
    engine: str = "direct"
    extra: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- actions (shared by both engines)
async def screen_emergency(text: str = "") -> str:
    c = _ctx.get()
    if rules.NOT_URGENT.search(text or ""):
        return "ok"
    kind = rules.emergency_kind(text)
    if kind is None and c.get("model_emergency"):
        kind = "reported"                       # the extraction model flagged it; rules didn't
    if kind:
        c["detail"] = kind
        return "emergency"
    return "ok"


async def screen_injection(text: str = "") -> str:
    return "injection" if rules.INJECTION.search(text or "") else "ok"


async def screen_privacy(text: str = "") -> str:
    return "privacy" if rules.PRIVACY.search(text or "") else "ok"


async def screen_medical(text: str = "") -> str:
    c = _ctx.get()
    return "medical" if rules.MEDICAL.search(text or "") or c.get("model_medical") else "ok"


async def screen_output(text: str = "") -> str:
    c = _ctx.get()
    allowed = c.get("allowed", "")
    if rules.ADVICE_OUT.search(text or ""):
        c["detail"] = "medical advice"
        return "output_medical"
    allowed_digits = "".join(ch for ch in allowed if ch.isdigit())
    for mm in rules.PHONE_OUT.finditer(text or ""):
        digits = "".join(ch for ch in mm.group(0) if ch.isdigit())[-10:]
        if digits not in allowed_digits:
            c["detail"] = "phone number not in the facts"
            return "output_phone"
    allowed_fees = {x.replace(",", "") for x in re.findall(r"\d[\d,]*", allowed)}
    for fee in rules.FEE_OUT.findall(text or ""):
        if fee.replace(",", "") not in allowed_fees:
            c["detail"] = f"fee ₹{fee} not in the facts"
            return "output_fee"
    return "ok"


ACTIONS = {"screen_emergency": screen_emergency, "screen_injection": screen_injection,
           "screen_privacy": screen_privacy, "screen_medical": screen_medical, "screen_output": screen_output}


# ----------------------------------------------------------------------------- engines
class _Nemo:
    def __init__(self) -> None:
        from nemoguardrails import LLMRails, RailsConfig
        cfg = RailsConfig.from_path(str(Path(__file__).parent / "config"))
        self.rails = LLMRails(cfg)
        for name, fn in ACTIONS.items():
            self.rails.register_action(fn, name)

    def run(self, which: str, messages: list[dict]) -> tuple[str, str | None]:
        res = self.rails.generate(messages=messages, options={"rails": [which], "log": {"activated_rails": True}})
        for r in res.log.activated_rails:
            for a in r.executed_actions:
                if a.return_value not in (None, "ok"):
                    return str(a.return_value), r.name
        return "ok", None


_engine: _Nemo | None | bool = None          # None = not tried yet, False = unavailable


def _nemo() -> _Nemo | None:
    global _engine
    if _engine is None:
        try:
            _engine = _Nemo()
            log.info("guardrails: NVIDIA NeMo Guardrails engine loaded")
        except Exception as e:  # noqa: BLE001  (not installed, or failed to load: use the direct runner)
            # Not installed (e.g. Python 3.14) is expected: info, not a warning. A broken install is a warning.
            (log.info if isinstance(e, ModuleNotFoundError) else log.warning)(
                "guardrails: NeMo Guardrails unavailable (%s); running checks directly", type(e).__name__)
            _engine = False
    return _engine or None


def engine_name() -> str:
    return "nemo" if _nemo() else "direct"


def _run_direct(pairs, text: str) -> tuple[str, str | None]:
    import asyncio
    for rail, action in pairs:
        out = asyncio.run(ACTIONS[action](text=text)) if not _in_loop() else _sync(ACTIONS[action], text)
        if out != "ok":
            return out, rail
    return "ok", None


def _in_loop() -> bool:
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _sync(fn, text: str) -> str:
    coro = fn(text=text)
    try:
        coro.send(None)
    except StopIteration as stop:            # the actions never await, so they finish on the first step
        return stop.value
    raise RuntimeError("guardrail action awaited unexpectedly")


_RAIL_NAMES = {"clinicdesk emergency screen": "emergency_screen", "clinicdesk injection screen": "injection_screen",
               "clinicdesk privacy screen": "privacy_screen", "clinicdesk medical advice screen": "medical_advice_screen",
               "clinicdesk output screen": "output_screen"}


def check_input(text: str, *, model_emergency: bool = False, model_medical: bool = False) -> Verdict:
    ctx = {"model_emergency": model_emergency, "model_medical": model_medical}
    token = _ctx.set(ctx)
    try:
        eng = _nemo()
        if eng:
            kind, rail = eng.run("input", [{"role": "user", "content": text}])
            rail = _RAIL_NAMES.get(rail, rail)
        else:
            kind, rail = _run_direct(INPUT_RAILS, text)
    finally:
        _ctx.reset(token)
    return Verdict(ok=kind == "ok", kind=kind, rail=rail, detail=ctx.get("detail", ""),
                   engine="nemo" if _engine else "direct")


def check_output(reply: str, allowed: str) -> Verdict:
    ctx = {"allowed": allowed}
    token = _ctx.set(ctx)
    try:
        eng = _nemo()
        if eng:
            kind, rail = eng.run("output", [{"role": "user", "content": "."}, {"role": "assistant", "content": reply}])
            rail = _RAIL_NAMES.get(rail, rail)
        else:
            kind, rail = _run_direct([("output_screen", "screen_output")], reply)
    finally:
        _ctx.reset(token)
    return Verdict(ok=kind == "ok", kind=kind, rail=rail, detail=ctx.get("detail", ""),
                   engine="nemo" if _engine else "direct")
