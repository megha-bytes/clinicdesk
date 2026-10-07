"""Which way of switching reasoning OFF works on Token Factory, and how fast is each tier?

Run from the repo root (uses TOKEN_FACTORY_API_KEY from .env.local):
    py scripts/check_reasoning.py

For each Nemotron model it tries three styles:
  default   no switch
  kwargs    extra_body={"chat_template_kwargs": {"enable_thinking": False}}   (vLLM style)
  system    a "/no_think" system message
and reports whether the reply is clean (no "<think>" / "Here's a thinking process"), whether reasoning came
back in a separate field, output tokens and latency. Then it times Hindi and Kannada replies per model
using the best style. Paste the output to Claude; it goes into docs/DEVLOG.md.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

try:
    from dotenv import load_dotenv
    from openai import OpenAI
except ImportError:
    sys.exit("Run: py -m pip install openai python-dotenv")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")
KEY = os.getenv("TOKEN_FACTORY_API_KEY")
if not KEY:
    sys.exit("TOKEN_FACTORY_API_KEY missing from .env.local")
client = OpenAI(base_url=os.getenv("TOKEN_FACTORY_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"),
                api_key=KEY, timeout=90, max_retries=0)

MODELS = {
    "lightning": "nvidia/Nemotron-3_5-Lightning",
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
}
SYSTEM = ("You are the receptionist of Sunrise Clinic. Clinic hours: Mon–Sat 9:00–13:00 and 17:00–20:00. "
          "Reply in 1–2 short sentences. Never give medical advice.")
LEAK = re.compile(r"(<think>|</think>|here'?s a thinking process|thinking process:|^okay,? (so )?let|"
                  r"^let me think|^we need to|^the user (is asking|asks|wants))", re.I | re.M)


def run(model: str, style: str, question: str, lang_hint: str = ""):
    msgs = [{"role": "system", "content": SYSTEM + lang_hint}, {"role": "user", "content": question}]
    extra = {}
    if style == "kwargs":
        extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    elif style == "system":
        msgs = [{"role": "system", "content": "/no_think"}, *msgs]
    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(model=model, messages=msgs, max_tokens=400, temperature=0.2, **extra)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}
    ms = (time.perf_counter() - t0) * 1000
    msg = r.choices[0].message
    text = msg.content or ""
    sep = bool(getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None))
    return {"ms": ms, "out": r.usage.completion_tokens if r.usage else 0, "clean": bool(text) and not LEAK.search(text),
            "separate_reasoning": sep, "text": text.strip().replace("\n", " ")[:110]}


print("\n=== 1. Reasoning switch per model (question: 'What are your timings?') ===")
best: dict[str, str] = {}
for tier, model in MODELS.items():
    print(f"\n{tier:9s} {model}")
    results = {}
    for style in ("default", "kwargs", "system"):
        r = run(model, style, "What are your timings?")
        results[style] = r
        if "error" in r:
            print(f"   {style:8s} ERROR {r['error']}")
        else:
            flag = "clean " if r["clean"] else "LEAKED"
            print(f"   {style:8s} {flag} {r['ms']:6.0f} ms  out={r['out']:<4} sep_reasoning={r['separate_reasoning']}  {r['text']}")
    ok = [(s, r) for s, r in results.items() if "error" not in r and r["clean"]]
    best[tier] = min(ok, key=lambda x: x[1]["ms"])[0] if ok else "none"
    print(f"   → best style: {best[tier]}")

print("\n=== 2. Hindi and Kannada latency (best style per model) ===")
questions = [("Hindi", "Kal subah Dr. Rao ke saath appointment mil sakta hai?"),
             ("Kannada", "ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಡಾ. ರಾವ್ ಅವರ ಅಪಾಯಿಂಟ್ಮೆಂಟ್ ಸಿಗುತ್ತದೆಯೇ?")]
for tier, model in MODELS.items():
    style = best[tier] if best[tier] != "none" else "kwargs"
    for lang, q in questions:
        r = run(model, style, q, f" Reply in {lang}, in the patient's script.")
        if "error" in r:
            print(f"   {tier:9s} {lang:8s} ERROR {r['error']}")
        else:
            flag = "clean " if r["clean"] else "LEAKED"
            print(f"   {tier:9s} {lang:8s} {flag} {r['ms']:6.0f} ms  out={r['out']:<4} {r['text']}")

print("\n=== Summary ===")
for tier in MODELS:
    print(f"   {tier:9s} best reasoning-off style: {best[tier]}")
print("\nPaste everything above into the chat.")
