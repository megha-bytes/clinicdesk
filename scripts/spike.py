"""Day 1 spike: prove Nebius Token Factory + Nemotron work for ClinicDesk.

What it checks
  1. Your API key works and which NVIDIA models Token Factory serves.
  2. Each tier (Nano / Super / Ultra) answers, with latency and token usage.
  3. Super makes a real tool call (find_slots) and uses the tool result.
  4. Replies in Hindi and Kannada.

Run (from the repo root):
  py -m pip install openai python-dotenv pyyaml
  py scripts/spike.py

It reads TOKEN_FACTORY_API_KEY from .env.local (git-ignored). It never prints the key.
At the end it writes the model IDs it found into config/models.yaml.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    from openai import OpenAI
    import yaml
except ImportError:
    sys.exit("Missing packages. Run:  py -m pip install openai python-dotenv pyyaml")

# Windows terminals can choke on Hindi/Kannada/emoji output; force UTF-8.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")

API_KEY = os.getenv("TOKEN_FACTORY_API_KEY") or os.getenv("NEBIUS_API_KEY")
BASE_URL = os.getenv("TOKEN_FACTORY_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")

if not API_KEY:
    sys.exit(
        "No API key found.\n"
        "Create a file named .env.local in the repo root containing:\n"
        "  TOKEN_FACTORY_API_KEY=your-key-here\n"
        "(.env.local is git-ignored, so it will never be committed.)"
    )

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=90)


def header(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------- 1. models
header("1. NVIDIA models available on Token Factory")
try:
    all_ids = sorted(m.id for m in client.models.list().data)
except Exception as e:  # noqa: BLE001
    sys.exit(f"Could not list models. Is the key correct?\n  {type(e).__name__}: {e}")

nvidia = [m for m in all_ids if "nvidia" in m.lower() or "nemotron" in m.lower()]
for m in nvidia:
    print("  ", m)
print(f"\n  ({len(all_ids)} models in total, {len(nvidia)} from NVIDIA)")


def pick(*must: str, avoid: tuple[str, ...] = ()) -> str | None:
    """First NVIDIA model whose id contains all `must` words and none of `avoid`."""
    for m in nvidia:
        low = m.lower()
        if all(w in low for w in must) and not any(a in low for a in avoid):
            return m
    return None


tiers = {
    "nano": pick("nemotron", "nano", avoid=("omni", "vl", "9b", "embed")),
    "super": pick("nemotron", "super", avoid=("49b", "llama")) or pick("nemotron", "super"),
    "ultra": pick("nemotron", "ultra", avoid=("253b", "llama")) or pick("nemotron", "ultra"),
    "omni": pick("nemotron", "omni"),
    "safety": pick("safety") or pick("guard"),
}
print("\n  Tier picks:")
for k, v in tiers.items():
    print(f"   {k:7s} -> {v or 'NOT FOUND'}")


# ---------------------------------------------------------------- helpers
def chat(model: str, messages: list, **kw):
    t0 = time.perf_counter()
    r = client.chat.completions.create(model=model, messages=messages, max_tokens=400, **kw)
    ms = (time.perf_counter() - t0) * 1000
    u = r.usage
    usage = f"in={u.prompt_tokens} out={u.completion_tokens}" if u else "usage n/a"
    return r, ms, usage


SYSTEM = (
    "You are the receptionist of Sunrise Clinic, Bengaluru. You only handle appointments, "
    "fees and clinic information. Never give medical advice. Keep replies to 2 sentences."
)
results: dict[str, str] = {}

# ---------------------------------------------------------------- 2. tiers
header("2. Each tier answers (latency + tokens)")
for tier in ("nano", "super", "ultra"):
    model = tiers[tier]
    if not model:
        print(f"  {tier}: skipped (model not found)")
        results[tier] = "not found"
        continue
    try:
        r, ms, usage = chat(model, [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "What are your clinic timings?"},
        ])
        text = (r.choices[0].message.content or "").strip().replace("\n", " ")
        print(f"  {tier:5s} {ms:7.0f} ms  {usage}\n        {text[:200]}")
        results[tier] = f"ok, {ms:.0f} ms"
    except Exception as e:  # noqa: BLE001
        print(f"  {tier}: FAILED {type(e).__name__}: {e}")
        results[tier] = f"failed: {type(e).__name__}"

# ---------------------------------------------------------------- 3. tools
header("3. Tool calling with Super")
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_slots",
            "description": "Find available appointment slots for a doctor on a date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor": {"type": "string", "description": "Doctor's name, e.g. 'Dr. Rao'"},
                    "date": {"type": "string", "description": "'today', 'tomorrow' or YYYY-MM-DD"},
                    "appointment_type": {"type": "string", "enum": ["new", "follow_up"]},
                },
                "required": ["doctor", "date", "appointment_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fee",
            "description": "Get the consultation fee for a doctor and appointment type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor": {"type": "string"},
                    "appointment_type": {"type": "string", "enum": ["new", "follow_up"]},
                },
                "required": ["doctor", "appointment_type"],
            },
        },
    },
]
FAKE_RESULTS = {
    "find_slots": {"slots": ["10:00", "10:40", "11:20"], "duration_min": 20},
    "get_fee": {"fee_inr": 500},
}

tool_model = tiers["super"] or tiers["nano"]
if not tool_model:
    print("  skipped (no Super or Nano model)")
    results["tools"] = "skipped"
else:
    try:
        msgs = [
            {"role": "system", "content": SYSTEM + " Use tools for any time or fee; never guess."},
            {"role": "user", "content": "I'm a new patient. Can I see Dr. Rao tomorrow morning, and what's the fee?"},
        ]
        r, ms, usage = chat(tool_model, msgs, tools=TOOLS, tool_choice="auto")
        calls = r.choices[0].message.tool_calls or []
        print(f"  model: {tool_model}\n  {ms:.0f} ms  {usage}")
        if not calls:
            print("  ⚠ No tool call made. Reply was:", (r.choices[0].message.content or "")[:200])
            results["tools"] = "no tool call"
        else:
            msgs.append(r.choices[0].message.model_dump(exclude_none=True))
            for c in calls:
                print(f"  tool call -> {c.function.name}({c.function.arguments})")
                msgs.append({
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": json.dumps(FAKE_RESULTS.get(c.function.name, {})),
                })
            r2, ms2, usage2 = chat(tool_model, msgs, tools=TOOLS)
            final = (r2.choices[0].message.content or "").strip().replace("\n", " ")
            print(f"  final reply ({ms2:.0f} ms): {final[:300]}")
            ok = "10:00" in final or "500" in final
            print("  ✅ used tool results" if ok else "  ⚠ reply didn't quote the tool results")
            results["tools"] = f"{len(calls)} call(s), " + ("used results" if ok else "check reply")
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED {type(e).__name__}: {e}")
        results["tools"] = f"failed: {type(e).__name__}"

# ---------------------------------------------------------------- 4. languages
header("4. Hindi and Kannada")
lang_model = tiers["super"] or tiers["nano"]
for lang, question in [
    ("Hindi", "Kal subah Dr. Rao ke saath appointment mil sakta hai?"),
    ("Kannada", "ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಡಾ. ರಾವ್ ಅವರ ಅಪಾಯಿಂಟ್ಮೆಂಟ್ ಸಿಗುತ್ತದೆಯೇ?"),
]:
    if not lang_model:
        break
    try:
        r, ms, usage = chat(lang_model, [
            {"role": "system", "content": SYSTEM + f" Reply in {lang}, in the same script the patient used."},
            {"role": "user", "content": question},
        ])
        text = (r.choices[0].message.content or "").strip().replace("\n", " ")
        print(f"  {lang:8s} {ms:6.0f} ms\n    Q: {question}\n    A: {text[:300]}")
        results[lang.lower()] = "replied (judge quality yourself)"
    except Exception as e:  # noqa: BLE001
        print(f"  {lang}: FAILED {type(e).__name__}: {e}")
        results[lang.lower()] = f"failed: {type(e).__name__}"

# ---------------------------------------------------------------- save
models_path = ROOT / "config" / "models.yaml"
try:
    cfg = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
    for tier in ("nano", "super", "ultra", "omni"):
        if tiers.get(tier) and tier in cfg.get("tiers", {}):
            cfg["tiers"][tier]["model"] = tiers[tier]
    cfg["safety_model"] = tiers["safety"]
    models_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    saved = f"saved model IDs to {models_path.relative_to(ROOT)}"
except Exception as e:  # noqa: BLE001
    saved = f"could not update models.yaml: {e}"

header("Summary")
for k, v in {**{f"model:{t}": tiers[t] or "NOT FOUND" for t in tiers}, **results}.items():
    print(f"  {k:14s} {v}")
print(f"\n  {saved}")
print("  Next: check the Hindi/Kannada replies read naturally, then commit config/models.yaml.")
