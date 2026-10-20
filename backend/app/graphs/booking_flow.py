"""Deterministic booking flow: the model extracts what the patient said, the code decides what happens
and the reply is built from templates.

Why (DEVLOG #27, #31, #32): in live runs a small model driving the booking tool-by-tool skipped the
confirmation and looped; a reply writer told patients an invalid phone number "looks good", promised
"one moment while I check", wrote "[date] at [time]" placeholders and invented slots. Here:

    understand (Lightning) → extraction only: doctor, date, time, name, age, phone, option number
    run_flow (code)        → typed details only → validate phone → find / register (consent card once)
                             → search slots (requested day, then the next days) → numbered options
                             → pick by number or time → hold → confirmation card → book → payment link
    render (templates)     → the reply, in English / Hindi / Kannada; no model writes booking replies

Every value the patient sees in a booking reply (times, fees, doctor, token, link) comes from a tool result.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from app import models as m
from app.agent.tools import ToolContext, ToolFailure, normalise_phone, parse_day, run_tool

_PHONE_RUN = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{7,15}\d)(?!\d)")
_AGE = re.compile(r"\b(?:age[d]?\s*(?:is\s*)?(\d{1,3})|(\d{1,3})\s*(?:years?|yrs?|y/?o|saal|साल|ವರ್ಷ)\b)", re.I)
_LEAD = re.compile(r"^\s*(?:option\s*)?#?([1-9])\s*[.),:-](?!\d)")      # "3. my mobile is…", "3, not my…"
_OPTION = re.compile(r"^\s*(?:option|no\.?|number|#)?\s*(\d)\s*[.)]?\s*(?:please|pls)?\s*$", re.I)
_CLOCK = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_TODAY = re.compile(r"\b(today|tonight|aaj|ivattu|indu)\b|आज|ಇಂದು|ಇವತ್ತು", re.I)
_TOMORROW = re.compile(r"\b(tomorrow|tmrw|kal|naale|nale)\b|कल|ನಾಳೆ", re.I)
_DATEISH = re.compile(r"\b\d{1,2}(st|nd|rd|th)\b|\b\d{1,2}[/-]\d{1,2}\b|\b\d{4}-\d{2}-\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|mon|tue|wed|thu|fri|sat|sun|next|after|"
                      r"parso|week)", re.I)
_WINDOW_WORDS = {
    "morning": re.compile(r"\b(morning|subah|subha|bellige)\b|सुबह|ಬೆಳಿಗ್ಗೆ|ಬೆಳಗ್ಗೆ", re.I),
    "afternoon": re.compile(r"\b(afternoon|noon|dopahar|madhyahna)\b|दोपहर|ಮಧ್ಯಾಹ್ನ", re.I),
    "evening": re.compile(r"\b(evening|shaam|sham|sanje)\b|शाम|ಸಂಜೆ", re.I),
}

# --------------------------------------------------------------------------- reply templates
T: dict[str, dict[str, str]] = {                 # short: replies may be read aloud on a phone call
    "welcome_back": {"en": "Welcome back, {name}!", "hi": "फिर से स्वागत है, {name}!", "kn": "ಮತ್ತೆ ಸ್ವಾಗತ, {name}!"},
    "phone_len": {"en": "That number has {n} digits; please send 10.", "hi": "इस नंबर में {n} अंक हैं; कृपया 10 अंक भेजें।",
                  "kn": "ಈ ಸಂಖ್ಯೆಯಲ್ಲಿ {n} ಅಂಕೆಗಳಿವೆ; ದಯವಿಟ್ಟು 10 ಕಳುಹಿಸಿ."},
    "phone_bad": {"en": "{num} isn't a mobile number; please check.", "hi": "{num} मोबाइल नंबर नहीं है; कृपया जाँचें।",
                  "kn": "{num} ಮೊಬೈಲ್ ಸಂಖ್ಯೆ ಅಲ್ಲ; ದಯವಿಟ್ಟು ಪರಿಶೀಲಿಸಿ."},
    "declined_slot": {"en": "OK, not booked.", "hi": "ठीक है, बुक नहीं किया।", "kn": "ಸರಿ, ಬುಕ್ ಮಾಡಿಲ್ಲ."},
    "declined_consent": {"en": "OK. Our staff can call you instead.", "hi": "ठीक है। हमारा स्टाफ़ आपको कॉल कर सकता है।",
                         "kn": "ಸರಿ. ನಮ್ಮ ಸಿಬ್ಬಂದಿ ನಿಮಗೆ ಕರೆ ಮಾಡಬಹುದು."},
    "nothing_day": {"en": "{who} is full on {day}.", "hi": "{day} को {who} खाली नहीं हैं।", "kn": "{day} ರಂದು {who} ಖಾಲಿ ಇಲ್ಲ."},
    "nothing_at_all": {"en": "{who} is full this week. Our staff will call you.",
                       "hi": "इस हफ़्ते {who} खाली नहीं हैं। स्टाफ़ आपको कॉल करेगा।",
                       "kn": "ಈ ವಾರ {who} ಖಾಲಿ ಇಲ್ಲ. ಸಿಬ್ಬಂದಿ ನಿಮಗೆ ಕರೆ ಮಾಡುತ್ತಾರೆ."},
    "taken": {"en": "Sorry, that slot just went.", "hi": "माफ़ कीजिए, वह समय अभी भर गया।", "kn": "ಕ್ಷಮಿಸಿ, ಆ ಸಮಯ ಈಗಷ್ಟೇ ಹೋಯಿತು."},
    "past": {"en": "That date has passed.", "hi": "वह तारीख़ निकल चुकी है।", "kn": "ಆ ದಿನಾಂಕ ಕಳೆದಿದೆ."},
    "error": {"en": "Sorry, that didn't work. Our staff can help.", "hi": "माफ़ कीजिए, यह नहीं हुआ। स्टाफ़ मदद करेगा।",
              "kn": "ಕ್ಷಮಿಸಿ, ಆಗಲಿಲ್ಲ. ಸಿಬ್ಬಂದಿ ಸಹಾಯ ಮಾಡುತ್ತಾರೆ."},
    "options": {"en": "Free with {who}{fee}:", "hi": "{who}{fee} के खाली समय:", "kn": "{who}{fee} ಖಾಲಿ ಸಮಯ:"},
    "pick": {"en": "Reply with a number.", "hi": "नंबर भेजें।", "kn": "ಸಂಖ್ಯೆ ಕಳುಹಿಸಿ."},
    "chosen": {"en": "Got it: {label}.", "hi": "ठीक है: {label}।", "kn": "ಸರಿ: {label}."},
    "ask_name_phone": {"en": "Your name and mobile number?", "hi": "आपका नाम और मोबाइल नंबर?", "kn": "ನಿಮ್ಮ ಹೆಸರು ಮತ್ತು ಮೊಬೈಲ್ ಸಂಖ್ಯೆ?"},
    "ask_name": {"en": "Your name?", "hi": "आपका नाम?", "kn": "ನಿಮ್ಮ ಹೆಸರು?"},
    "ask_phone": {"en": "Your 10-digit mobile number?", "hi": "आपका 10 अंकों का मोबाइल नंबर?", "kn": "ನಿಮ್ಮ 10 ಅಂಕೆಯ ಮೊಬೈಲ್ ಸಂಖ್ಯೆ?"},
    "not_found": {"en": "I couldn't find {name} with that number. Check it, or tell me your age to register.",
                  "hi": "उस नंबर पर {name} नहीं मिले। नंबर जाँचें, या रजिस्टर करने के लिए अपनी उम्र बताइए।",
                  "kn": "ಆ ಸಂಖ್ಯೆಯಲ್ಲಿ {name} ಸಿಗಲಿಲ್ಲ. ಸಂಖ್ಯೆ ಪರಿಶೀಲಿಸಿ, ಅಥವಾ ನೋಂದಣಿಗೆ ನಿಮ್ಮ ವಯಸ್ಸು ತಿಳಿಸಿ."},
    "ask_age": {"en": "First visit? Your age, please.", "hi": "पहली बार? अपनी उम्र बताइए।", "kn": "ಮೊದಲ ಭೇಟಿಯೇ? ನಿಮ್ಮ ವಯಸ್ಸು ತಿಳಿಸಿ."},
    "ask_doctor": {"en": "Which doctor, or what's it for?", "hi": "कौन से डॉक्टर, या किस लिए?", "kn": "ಯಾವ ವೈದ್ಯರು, ಅಥವಾ ಯಾವ ಕಾರಣಕ್ಕೆ?"},
    "booked": {"en": "✅ Booked: {doctor}, {when}, ₹{fee}.", "hi": "✅ बुक: {doctor}, {when}, ₹{fee}।",
               "kn": "✅ ಬುಕ್ ಆಗಿದೆ: {doctor}, {when}, ₹{fee}."},
    "pay": {"en": "Pay: {url}", "hi": "भुगतान: {url}", "kn": "ಪಾವತಿ: {url}"},
}


def t(key: str, lang: str, **kw: Any) -> str:
    row = T[key]
    return row.get(lang, row["en"]).format(**kw)


def render(say: dict, lang: str) -> str:
    """The patient-facing reply for one booking step."""
    lines: list[str] = []
    if say.get("welcome"):
        lines.append(t("welcome_back", lang, name=say["welcome"]))
    for key, kw in say.get("problems", []):
        lines.append(t(key, lang, **kw))
    if say.get("booked"):
        b = say["booked"]
        lines.append(t("booked", lang, doctor=b["doctor"], when=b["when"], fee=b["fee_inr"]))
        if b.get("pay_url"):
            lines.append(t("pay", lang, url=b["pay_url"]))
    if say.get("chosen"):
        lines.append(t("chosen", lang, label=say["chosen"]))
    if say.get("options"):
        opts = say["options"]
        same_doc = len({o["doctor"] for o in opts if "doctor" in o}) == 1 and all("doctor" in o for o in opts)
        same_fee = len({o.get("fee_inr") for o in opts}) == 1 and opts[0].get("fee_inr") is not None
        who = opts[0]["doctor"] if same_doc else say.get("who", "")
        lines.append(t("options", lang, who=who, fee=f" (₹{opts[0]['fee_inr']})" if same_fee else ""))
        for i, o in enumerate(opts, 1):
            parts = [] if same_doc else [o.get("doctor", "")]
            parts.append(o.get("short", o["label"]))
            if not same_fee and o.get("fee_inr") is not None:
                parts.append(f"₹{o['fee_inr']}")
            lines.append(f"{i}. " + " · ".join(p for p in parts if p))
        lines.append(t("pick", lang))
    ask = say.get("ask", [])
    if "name" in ask and "phone" in ask:
        lines.append(t("ask_name_phone", lang))
    elif "name" in ask:
        lines.append(t("ask_name", lang))
    elif "phone" in ask and not any(k.startswith("phone_") for k, _ in say.get("problems", [])):
        lines.append(t("ask_phone", lang))
    if "age" in ask and not any(k == "not_found" for k, _ in say.get("problems", [])):
        lines.append(t("ask_age", lang))
    if "doctor" in ask:
        lines.append(t("ask_doctor", lang))
    return "\n".join(lines) or t("ask_doctor", lang)


def _digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


# --------------------------------------------------------------------------- extraction checks
def merge_details(details: dict, ext: dict, text: str, *, asked_age: bool = False,
                  used: frozenset[str] = frozenset()) -> tuple[dict, list[tuple[str, dict]]]:
    """Add name/phone/age the patient actually typed in `text`. Returns (details, problems).

    An age counts only next to an age word ("23 years", "age 23") or as the answer right after we
    asked for it, and never when the same number picked an option ("3, not my first visit", DEVLOG #37)."""
    details, problems = dict(details), []
    typed = _digits(text)

    phone = _digits(str(ext.get("phone") or ""))
    if not phone or phone not in typed:
        runs = [_digits(x) for x in _PHONE_RUN.findall(text)]
        phone = max(runs, key=len) if runs else ""
    if phone and len(phone) >= 7:
        try:
            details["phone"] = normalise_phone(phone)
        except ToolFailure:
            details.pop("phone", None)
            core = phone[2:] if len(phone) == 12 and phone.startswith("91") else phone
            if len(core) == 10:
                problems.append(("phone_bad", {"num": core}))
            else:
                problems.append(("phone_len", {"n": len(phone)}))

    name = str(ext.get("name") or "").strip()
    if name and len(name) >= 2 and name.split()[0].casefold() in text.casefold():
        details["name"] = " ".join(w.capitalize() if w.islower() else w for w in name.split())

    age = None
    mm = _AGE.search(text)
    if mm:
        age = int(mm.group(1) or mm.group(2))
    else:
        cand = ext.get("age")
        bare = re.fullmatch(r"\s*(\d{1,3})\s*\.?\s*", text) if asked_age else None
        try:
            cand = int(cand) if cand is not None else (int(bare.group(1)) if bare else None)
        except (TypeError, ValueError):
            cand = None
        if cand is not None and str(cand) not in used and re.search(rf"(?<!\d){cand}(?!\d)", text):
            age = cand
    if age is not None and 0 <= age <= 120 and str(age) != details.get("phone"):
        details["age"] = age
    if ext.get("new_patient") is not None:
        details["new_patient"] = bool(ext["new_patient"])
    return details, problems


def match_doctor(ctx: ToolContext, said: str | None) -> m.Doctor | None:
    if not said:
        return None
    words = {w for w in re.findall(r"[a-z]+", said.casefold()) if len(w) > 2 and w not in ("doctor", "dr")}
    if not words:
        return None
    doctors = ctx.session.scalars(select(m.Doctor).where(m.Doctor.clinic_id == ctx.clinic_id)).all()
    hits = [d for d in doctors if words & set(re.findall(r"[a-z]+", d.name.casefold()))]
    return hits[0] if len(hits) == 1 else None


def clean_date(value: Any, ctx: ToolContext, text: str | None = None) -> str | None:
    """'today' / 'tomorrow' / ISO date the patient actually asked for, never in the past.

    With `text`, a date is only accepted if the message mentions one (the model tends to fill in
    "today" when no date was given; at 8:38 pm that hid every slot, DEVLOG #32)."""
    v = str(value or "").strip().lower()
    if text is not None:
        if v == "today" and not _TODAY.search(text):
            return None
        if v == "tomorrow" and not _TOMORROW.search(text):
            return None
        if v not in ("today", "tomorrow") and not (_DATEISH.search(text) or _TOMORROW.search(text)):
            return None
    if v in ("today", "tomorrow"):
        return parse_day(v, ctx).isoformat()                   # "tomorrow" and "2026-10-21" are the same day
    try:
        d = date.fromisoformat(v)
    except ValueError:
        return None
    today = ctx.local_now.date()
    if d < today:
        try:
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
        except ValueError:
            return None
    return d.isoformat()


_ORDINALS = {
    1: r"first|1st|pehl[ae]|pahl[ae]|modal(a|ane|aneya)|ಮೊದಲ|पहल",
    2: r"second|2nd|d[ou]{1,2}sr[ae]|eradu|eradane|ಎರಡನೇ|दूसर",
    3: r"third|3rd|t[ei]{1,2}sr[ae]|mooru|mooran[ae]|ಮೂರನೇ|तीसर",
}


_NOT_A_PICK = re.compile(r"first (visit|time)|pehli (baar|bar|dafa)|modala (bari|sala|sari)", re.I)


def _says_number(text: str, n: int) -> bool:
    """True if the message itself picks option n: a standalone number, or an ordinal used as a pick
    ("the first one", "dusra wala"), never "first visit" (DEVLOG #37)."""
    if re.search(rf"(?<![\d:.]){n}(?!\d|[:.]\d)", text):
        return True
    return _says_ordinal(text, n)


def _says_ordinal(text: str, n: int) -> bool:
    ordp = _ORDINALS.get(n)
    if not ordp or _NOT_A_PICK.search(text):
        return False
    if re.search(rf"(?:{ordp})\w*\s+(one|option|slot|wala|vala|wali|vali|ondu|time slot)\b", text, re.I):
        return True
    return len(re.findall(r"\w+", text)) <= 3 and re.search(ordp, text, re.I) is not None


def pick_option(offered: list[dict], ext: dict, text: str) -> dict | None:
    """The slot the patient chose: an option number, or a time that matches exactly one option."""
    if not offered:
        return None
    n = ext.get("option")
    try:
        n = int(n) if n is not None else None
    except (TypeError, ValueError):
        n = None
    if n is not None and not _says_number(text, n):      # the model invented a pick (DEVLOG #34)
        n = None
    mm = _OPTION.match(text) or _LEAD.match(text)
    if mm:
        n = int(mm.group(1))
    if n is None:
        n = next((k for k in range(1, len(offered) + 1) if _says_ordinal(text, k)), None)
    if n is not None and 1 <= n <= len(offered):
        return offered[n - 1]
    said = _times(text)
    if said:
        hits = [o for o in offered if _times(o.get("short") or o["label"]) & said]
        if len(hits) == 1:
            return hits[0]
        loose = {(h % 12, mi) for h, mi in said}                 # "12:05 am" for a 12:05 PM slot
        hits = [o for o in offered if {(h % 12, mi) for h, mi in _times(o.get("short") or o["label"])} & loose]
        if len(hits) == 1:
            return hits[0]
    return None


def _times(text: str) -> set[tuple[int, int]]:
    """Clock times mentioned in text as (hour24, minute); bare numbers only count with am/pm or ':'."""
    out = set()
    for h, mnt, ap in _CLOCK.findall(text or ""):
        if not mnt and not ap:
            continue
        h, mi = int(h), int(mnt or 0)
        ap = ap.lower().replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            out.add((h, mi))
            if not ap and h < 12:
                out.add((h + 12, mi))
    return out


def unverified_times(reply: str, allowed_text: str) -> set[tuple[int, int]]:
    """Times in the reply that appear nowhere in the facts or the patient's words."""
    return _times(reply) - _times(allowed_text)


# --------------------------------------------------------------------------- the flow
def _log(results: list, tool: str, args: dict, out: dict) -> dict:
    results.append({"tool": tool, "args": {k: v for k, v in args.items() if k != "phone"}, "result": out})
    return out


def _slot_label(s: dict) -> str:
    if s.get("booking_style") == "token":
        return f"{s['doctor']} · token {s['next_token']}, about {s['estimated_time']} · ₹{s['fee_inr']}"
    return f"{s['doctor']} · {s['when']} · ₹{s['fee_inr']}"


def _slot_short(s: dict) -> str:
    if s.get("booking_style") == "token":
        return f"token {s['next_token']}, about {s['estimated_time']}"
    return s["when"]


def _search(booking: dict, state: dict, ctx: ToolContext, results: list, say: dict) -> None:
    """Find options: the requested day first, then the following days."""
    base = {"appointment_type": booking.get("appointment_type", "new"), "window": booking.get("window") or "any",
            "language": state.get("language", "en")}
    if booking.get("doctor_id"):
        base["doctor_id"] = booking["doctor_id"]
    else:
        base["specialty"] = booking["specialty"]
    if booking.get("time"):
        base["preferred_time"] = booking["time"]
    who = booking.get("doctor") or booking.get("specialty") or ""
    say["who"] = who

    attempts = []
    if booking.get("date"):
        attempts.append({**base, "date": booking["date"], "days": 1})
        try:
            nxt = parse_day(booking["date"], ctx) + timedelta(days=1)
            attempts.append({**base, "date": nxt.isoformat(), "days": 3, "preferred_time": None})
        except ToolFailure:
            pass
    else:
        attempts.append({**base, "date": "today", "days": 3})
    if base["window"] != "any":
        attempts.append({**attempts[-1], "window": "any"})

    for i, args in enumerate(attempts):
        args = {k: v for k, v in args.items() if v is not None}
        fs = _log(results, "find_slots", args, run_tool("find_slots", args, ctx))
        if not fs.get("ok"):
            say["problems"].append(("past", {}) if fs.get("error") == "IN_PAST" else ("error", {}))
            booking["date"] = None
            return
        if fs.get("slots"):
            if i > 0 and booking.get("date"):
                day = parse_day(booking["date"], ctx).strftime("%a %d %b").replace(" 0", " ")
                say["problems"].append(("nothing_day", {"who": who, "day": day}))
            booking["offered"] = [{"doctor_id": s["doctor_id"], "start": s["start"], "label": _slot_label(s),
                                   "short": _slot_short(s), "doctor": s["doctor"], "fee_inr": s["fee_inr"],
                                   "appointment_type": fs.get("appointment_type", args["appointment_type"])}
                                  for s in fs["slots"][:3]]
            say["options"] = booking["offered"]
            return
    say["problems"].append(("nothing_at_all", {"who": who}))
    booking["date"] = None


def run_flow(state: dict, ctx: ToolContext) -> dict:
    """One step of the booking flow. Returns state updates (incl. `say`, maybe `pending`)."""
    ext = state.get("ext") or {}
    text = next((x["content"] for x in reversed(state.get("messages", [])) if x["role"] == "user"), "")
    booking = dict(state.get("booking") or {})
    results = list(state.get("tool_results", []))
    patient_id = state.get("patient_id")
    say: dict = {"ask": [], "problems": [], "options": None, "booked": None, "who": booking.get("doctor", "")}
    upd: dict = {}

    last = results[-1] if results else None
    # ---- just came back from a card
    if last and last["tool"] == "book":
        if last["result"].get("ok"):
            appt = last["result"]
            pay = _log(results, "send_payment_link", {"appointment_id": appt["appointment_id"]},
                       run_tool("send_payment_link", {"appointment_id": appt["appointment_id"]}, ctx))
            say["booked"] = {"doctor": appt.get("doctor"), "when": appt.get("when"), "fee_inr": appt.get("fee_inr"),
                             "pay_url": pay.get("url") if pay.get("ok") else None}
            return {"booking": {}, "tool_results": results, "say": say, "hold_id": None}
        booking["chosen"] = None
        if last["result"].get("error") == "PATIENT_DECLINED":
            _release_hold(ctx, (last.get("args") or {}).get("hold_id"))
            say["problems"].append(("declined_slot", {}))
            say["options"] = booking.get("offered")
        else:
            say["problems"].append(("taken", {}))
            booking["offered"] = None
            _search(booking, state, ctx, results, say)
        return {"booking": booking, "tool_results": results, "say": say, "hold_id": None}
    if last and last["tool"] == "register_patient" and last["result"].get("error") == "PATIENT_DECLINED":
        say["problems"].append(("declined_consent", {}))
        return {"booking": {}, "tool_results": results, "say": say}

    # ---- 1. which option they picked (first, so "the 12:05 one" isn't read as a new time)
    offered = booking.get("offered") or []
    choice = pick_option(offered, ext, text)
    used = frozenset({str(offered.index(choice) + 1)}) if choice else frozenset()

    # ---- 2. details the patient typed
    details, problems = merge_details(booking.get("details") or {}, ext, text,
                                      asked_age="age" in (booking.get("last_ask") or []), used=used)
    booking["details"] = details
    say["problems"] += problems

    if choice:
        booking["chosen"] = choice
    else:
        # ---- 3. what they want
        changed = False
        doc = match_doctor(ctx, ext.get("doctor"))
        if doc and doc.id != booking.get("doctor_id"):
            booking.update(doctor_id=doc.id, doctor=doc.name, specialty=None)
            changed = True
        if ext.get("need") and not booking.get("doctor_id") and not booking.get("specialty"):
            r = _log(results, "route_to_doctor", {"need": ext["need"]},
                     run_tool("route_to_doctor", {"need": str(ext["need"])[:200]}, ctx))
            if r.get("ok"):
                booking.update(specialty=r["specialty"], need=ext["need"])
                changed = True
        window = next((w for w, rx in _WINDOW_WORDS.items() if rx.search(text)), None)
        time_ = ext.get("time") if re.fullmatch(r"\d{2}:\d{2}", str(ext.get("time") or "")) and _times(text) else None
        for key, value in (("date", clean_date(ext.get("date"), ctx, text)), ("time", time_), ("window", window)):
            if value and value != booking.get(key):
                booking[key] = value
                changed = True
        if ext.get("follow_up"):
            booking["appointment_type"] = "follow_up"
        if changed:
            booking["offered"], booking["chosen"] = None, None

    # ---- 4. who they are
    if not patient_id:
        missing = [f for f in ("name", "phone") if not details.get(f)]
        if not missing:
            fp = _log(results, "find_patient", {"name": details["name"]},
                      run_tool("find_patient", {"name": details["name"], "phone": details["phone"]}, ctx))
            if fp.get("ok"):
                patient_id = fp["patient_id"]
                upd["patient_id"] = patient_id
                say["welcome"] = fp.get("name")
            elif fp.get("error") == "NOT_FOUND":
                if details.get("age") is None:
                    missing.append("age")
                    if details.get("new_patient") is False:          # "not my first visit", but no record
                        say["problems"].append(("not_found", {"name": details["name"]}))
                else:
                    args = {"name": details["name"], "age": details["age"], "phone": details["phone"],
                            "language": state.get("language", "en")}
                    if state.get("consented"):
                        reg = _log(results, "register_patient", args,
                                   run_tool("register_patient", {**args, "consent": True}, ctx))
                        if reg.get("ok"):
                            patient_id = reg["patient_id"]
                            upd["patient_id"] = patient_id
                    else:
                        upd["pending"] = {"call": {"id": "flow_register", "name": "register_patient", "args": args}}
            else:
                say["problems"].append(("error", {}))
        say["ask"] += missing

    # ---- 5. options
    if not booking.get("doctor_id") and not booking.get("specialty"):
        say["ask"].append("doctor")
    elif not booking.get("offered") and not booking.get("chosen"):
        _search(booking, state, ctx, results, say)

    # ---- 6. hold the chosen slot → confirmation card
    chosen = booking.get("chosen")
    if chosen and patient_id and "pending" not in upd:
        args = {"doctor_id": chosen["doctor_id"], "start": chosen["start"],
                "appointment_type": chosen["appointment_type"], "patient_id": patient_id}
        h = _log(results, "hold_slot", args, run_tool("hold_slot", args, ctx))
        if h.get("ok"):
            upd["hold_id"] = h["hold_id"]
            upd["pending"] = {"call": {"id": "flow_book", "name": "book",
                                       "args": {"hold_id": h["hold_id"], "patient_id": patient_id}}}
        else:
            say["problems"].append(("taken", {}))
            booking["offered"], booking["chosen"] = None, None
            _search(booking, state, ctx, results, say)
    elif chosen and not patient_id:
        say["chosen"] = chosen.get("short", chosen["label"])
    elif booking.get("offered") and not say["options"] and not say["ask"] and not say["problems"]:
        say["options"] = booking["offered"]                     # remind only when there's nothing else to say

    booking["last_ask"] = list(say["ask"])
    return {**upd, "booking": booking, "tool_results": results, "say": say}


def _release_hold(ctx: ToolContext, hold_id: str | None) -> None:
    """The patient said no to the card: free the slot now instead of waiting for the hold to expire."""
    if not hold_id:
        return
    row = ctx.session.get(m.Appointment, hold_id)
    if row is not None and row.status == "held":
        row.status, row.hold_expires_at = "cancelled", None
        ctx.session.commit()


def flow_active(state: dict) -> bool:
    intent = state.get("intent")
    if intent == "book":
        return True
    in_progress = bool(state.get("booking"))
    return in_progress and intent not in ("cancel", "reschedule", "queue", "pay", "human")
