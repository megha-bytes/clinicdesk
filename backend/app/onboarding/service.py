"""Clinic onboarding with Tavily (owner side only; never used in the patient chat).

The owner gives the clinic's website (or its name and area). Tavily reads the pages, Nemotron Super turns the
text into a structured draft (doctors, specialties, hours, fees, address), and a deterministic check marks
every value as `found` in the source text or `check` (not found word for word). Nothing goes live from a
draft: the owner reviews it. Same rule as the patient side: the AI drafts, a person decides.

Why owner-side only: web results in the patient chat could turn into medical advice or unverified facts.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.settings import get_settings

TAVILY = "https://api.tavily.com"
MAX_CHARS = 14000

PROMPT = """You turn a clinic's web pages into a draft listing. Use ONLY facts written in the pages below.
If something isn't written there, use null. Never guess fees, hours or doctors.
Return JSON only, in this shape:
{"clinic_name": str|null, "address": str|null, "phone": str|null, "languages": [str],
 "booking_style": "timed"|"token"|null,
 "doctors": [{"name": str, "specialty": str|null, "days": str|null, "hours": str|null, "fee_inr": int|null}],
 "services": [{"name": str, "fee_inr": int|null}],
 "clinic_hours": str|null}

Pages:
"""


class OnboardingError(RuntimeError):
    pass


def _key() -> str:
    k = get_settings().secret("TAVILY_API_KEY")
    if not k:
        raise OnboardingError("Tavily isn't configured (TAVILY_API_KEY).")
    return k.get_secret_value()


def tavily_extract(urls: list[str], client: httpx.Client | None = None) -> list[dict]:
    c = client or httpx.Client(timeout=40)
    r = c.post(f"{TAVILY}/extract", headers={"Authorization": f"Bearer {_key()}"},
               json={"urls": urls[:5], "extract_depth": "advanced", "format": "markdown",
                     "query": "doctors, specialties, consultation fees, timings, address, phone"})
    if r.status_code != 200:
        raise OnboardingError(f"Tavily extract failed (HTTP {r.status_code}).")
    return [x for x in r.json().get("results", []) if x.get("raw_content")]


def tavily_search(query: str, client: httpx.Client | None = None) -> list[dict]:
    c = client or httpx.Client(timeout=40)
    r = c.post(f"{TAVILY}/search", headers={"Authorization": f"Bearer {_key()}"},
               json={"query": query, "max_results": 5, "search_depth": "advanced", "include_raw_content": "markdown"})
    if r.status_code != 200:
        raise OnboardingError(f"Tavily search failed (HTTP {r.status_code}).")
    return r.json().get("results", [])


def gather(url: str | None, name: str | None, client: httpx.Client | None = None) -> list[dict]:
    """[{url, title, text}] from the clinic's site, or from a search for its name."""
    if url:
        pages = tavily_extract([url], client)
        return [{"url": p["url"], "title": p.get("title") or p["url"], "text": p["raw_content"]} for p in pages]
    hits = tavily_search(f"{name} clinic doctors timings consultation fee", client)
    out = [{"url": h["url"], "title": h.get("title") or h["url"], "text": h.get("raw_content") or h.get("content") or ""}
           for h in hits]
    return [p for p in out if p["text"]][:4]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def review(draft: dict, text: str) -> list[dict]:
    """Mark each extracted value `found` (appears in the source text) or `check` (the owner must confirm)."""
    src = _norm(text)
    digits = re.sub(r"[^\d]", " ", text)
    notes: list[dict] = []

    def note(field: str, value: Any, ok: bool) -> None:
        notes.append({"field": field, "value": value, "status": "found" if ok else "check"})

    for i, d in enumerate(draft.get("doctors") or []):
        name = str(d.get("name") or "")
        core = [w for w in re.findall(r"[A-Za-z]{3,}", name) if w.lower() not in ("doctor", "dr")]
        note(f"doctors[{i}].name", name, bool(core) and all(w.lower() in src for w in core))
        if d.get("fee_inr") is not None:
            note(f"doctors[{i}].fee_inr", d["fee_inr"], re.search(rf"(?<!\d){int(d['fee_inr'])}(?!\d)", digits) is not None)
        if d.get("specialty"):
            note(f"doctors[{i}].specialty", d["specialty"], _norm(str(d["specialty"]))[:6] in src)
    for i, sv in enumerate(draft.get("services") or []):
        if sv.get("fee_inr") is not None:
            note(f"services[{i}].fee_inr", sv["fee_inr"], re.search(rf"(?<!\d){int(sv['fee_inr'])}(?!\d)", digits) is not None)
    if draft.get("phone"):
        p = re.sub(r"\D", "", str(draft["phone"]))[-10:]
        note("phone", draft["phone"], bool(p) and p in re.sub(r"\D", "", text))
    if draft.get("address"):
        words = [w for w in re.findall(r"[A-Za-z]{4,}", str(draft["address"]))][:4]
        note("address", draft["address"], bool(words) and all(w.lower() in src for w in words))
    return notes


def build_draft(router, pages: list[dict]) -> dict:
    from app.graphs.clinicdesk import _parse_json
    if not pages:
        raise OnboardingError("Tavily couldn't read any page for that clinic.")
    text = "\n\n".join(f"### {p['title']} ({p['url']})\n{p['text']}" for p in pages)[:MAX_CHARS]
    res = router.chat("super", [{"role": "system", "content": PROMPT + text},
                                {"role": "user", "content": "Return the JSON draft."}],
                      purpose="onboarding", node="onboarding", max_tokens=1200, temperature=0)
    draft = _parse_json(res.content)
    if not isinstance(draft, dict) or not draft:
        raise OnboardingError("The model didn't return a usable draft. Try again.")
    draft.setdefault("doctors", [])
    draft.setdefault("services", [])
    return {"draft": draft, "review": review(draft, text), "text_chars": len(text)}
