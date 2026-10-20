"""Replies written to be *heard*: the text a voice call reads out.

The chat reply is made for eyes ("✅ Booked: Dr. Rao, Wed 21 Oct, 9:30 AM, ₹500." and a numbered
list). Read aloud, a Hindi voice says "W-E-D" and "rupee sign". So, for speech only:
- dates and times in the caller's language ("कल सुबह 9:30", "ನಾಳೆ ಬೆಳಿಗ್ಗೆ 9:30"), "today"/"tomorrow"
  instead of the date when it is one of those;
- "₹500" → "500 rupees" / "500 रुपये" / "500 ರೂಪಾಯಿ";
- "1. 9:30 AM" → "Option 1: 9:30 AM."; "Reply with a number." → "Say the option number.";
- no emoji, no links (a link is sent, never read out);
- cards: what is being agreed to, then "Say yes or no."
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_DAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
DAY_NAMES = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "kn": ["ಸೋಮವಾರ", "ಮಂಗಳವಾರ", "ಬುಧವಾರ", "ಗುರುವಾರ", "ಶುಕ್ರವಾರ", "ಶನಿವಾರ", "ಭಾನುವಾರ"],
}
MONTH_NAMES = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "kn": ["ಜನವರಿ", "ಫೆಬ್ರವರಿ", "ಮಾರ್ಚ್", "ಏಪ್ರಿಲ್", "ಮೇ", "ಜೂನ್", "ಜುಲೈ", "ಆಗಸ್ಟ್", "ಸೆಪ್ಟೆಂಬರ್", "ಅಕ್ಟೋಬರ್", "ನವೆಂಬರ್",
           "ಡಿಸೆಂಬರ್"],
}
RELATIVE = {"en": ("today", "tomorrow"), "hi": ("आज", "कल"), "kn": ("ಇಂದು", "ನಾಳೆ")}
# part of day for Hindi/Kannada clock times (they say "सुबह 9:30", not "9:30 AM")
PART = {"hi": [(0, "रात"), (4, "सुबह"), (12, "दोपहर"), (16, "शाम"), (19, "रात")],
        "kn": [(0, "ರಾತ್ರಿ"), (4, "ಬೆಳಿಗ್ಗೆ"), (12, "ಮಧ್ಯಾಹ್ನ"), (16, "ಸಂಜೆ"), (19, "ರಾತ್ರಿ")]}
DR = {"en": "Dr. ", "hi": "डॉक्टर ", "kn": "ಡಾಕ್ಟರ್ "}
RUPEES = {"en": "{n} rupees", "hi": "{n} रुपये", "kn": "{n} ರೂಪಾಯಿ"}
OPTION = {"en": "Option {i}: {rest}.", "hi": "विकल्प {i}: {rest}।", "kn": "ಆಯ್ಕೆ {i}: {rest}."}
TOKEN = {"en": "token number {n}", "hi": "टोकन नंबर {n}", "kn": "ಟೋಕನ್ ಸಂಖ್ಯೆ {n}"}
ABOUT = {"en": "around", "hi": "लगभग", "kn": "ಸುಮಾರು"}
SAY_NUMBER = {"en": "Say the option number.", "hi": "विकल्प का नंबर बोलिए।", "kn": "ಆಯ್ಕೆಯ ಸಂಖ್ಯೆ ಹೇಳಿ."}
SAY_YES_NO = {"en": "Say yes or no.", "hi": "हाँ या नहीं बोलिए।", "kn": "ಹೌದು ಅಥವಾ ಬೇಡ ಎಂದು ಹೇಳಿ."}
LINK_SENT = {"en": "You can pay with the link on your screen, or at the clinic.",
             "hi": "स्क्रीन पर दिए लिंक से या क्लिनिक में भुगतान कर सकते हैं।",
             "kn": "ಪರದೆಯ ಮೇಲಿನ ಲಿಂಕ್ ಮೂಲಕ ಅಥವಾ ಕ್ಲಿನಿಕ್‌ನಲ್ಲಿ ಪಾವತಿಸಬಹುದು."}
PICK_LINES = {"Reply with a number.", "नंबर भेजें।", "ಸಂಖ್ಯೆ ಕಳುಹಿಸಿ."}
CARD = {
    "book": {"en": "Shall I book {doctor}, {when}, {fee}?", "hi": "{doctor}, {when}, {fee}। बुक कर दूँ?",
             "kn": "{doctor}, {when}, {fee}. ಬುಕ್ ಮಾಡಲೇ?"},
    "cancel": {"en": "Cancel your appointment with {doctor}, {when}?", "hi": "{doctor} के साथ {when} की अपॉइंटमेंट रद्द करूँ?",
               "kn": "{doctor} ಅವರ ಜೊತೆ {when} ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದು ಮಾಡಲೇ?"},
    "reschedule": {"en": "Move your appointment with {doctor} to {when}?", "hi": "{doctor} की अपॉइंटमेंट {when} पर कर दूँ?",
                   "kn": "{doctor} ಅವರ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಅನ್ನು {when} ಗೆ ಬದಲಿಸಲೇ?"},
}

_DATE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AP])\.?M\b", re.I)
_RUPEE = re.compile(r"(?:₹|\bRs\.?\s?|\bINR\s?)\s?(\d[\d,]*)", re.I)
_OPTION_LINE = re.compile(r"^\s*(\d)\.\s+(.+?)\s*$")
_TOKEN = re.compile(r"\btoken\s+(\d+)", re.I)
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")


def _lang(lang: str | None) -> str:
    return lang if lang in ("en", "hi", "kn") else "en"


def speak_date(m: re.Match, lang: str, today: date | None) -> str:
    dow, day, mon = _DAYS[m.group(1)], int(m.group(2)), _MONTHS[m.group(3)]
    if today:
        for offset, word in enumerate(RELATIVE[lang]):
            d = today + timedelta(days=offset)
            if (d.day, d.month) == (day, mon):
                return word
    if lang == "en":
        return f"{DAY_NAMES['en'][dow]} {day} {MONTH_NAMES['en'][mon - 1]}"
    return f"{DAY_NAMES[lang][dow]}, {day} {MONTH_NAMES[lang][mon - 1]}"


def speak_clock(m: re.Match, lang: str) -> str:
    h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
    if lang == "en":
        return f"{h} {ap}M" if mi == "00" else f"{h}:{mi} {ap}M"
    h24 = (h % 12) + (12 if ap == "P" else 0)
    part = [p for start, p in PART[lang] if h24 >= start][-1]
    return f"{part} {h}" + ("" if mi == "00" else f":{mi}")


def speak_when(text: str, lang: str, today: date | None = None) -> str:
    lang = _lang(lang)
    text = _DATE.sub(lambda m: speak_date(m, lang, today), text)
    text = _CLOCK.sub(lambda m: speak_clock(m, lang), text)
    text = _TOKEN.sub(lambda m: TOKEN[lang].format(n=m.group(1)), text)
    text = re.sub(r"\babout\b", ABOUT[lang], text)
    return text


def speak_money(text: str, lang: str) -> str:
    return _RUPEE.sub(lambda m: RUPEES[_lang(lang)].format(n=m.group(1).replace(",", "")), text)


def to_speech(text: str | None, lang: str | None, today: date | None = None) -> str:
    """A reply rewritten to be read aloud."""
    lang = _lang(lang)
    out: list[str] = []
    had_link = False
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if _URL.search(line):
            had_link = True
            line = _URL.sub("", line).strip(" :")
            if not line or line.endswith(("Pay", "भुगतान", "ಪಾವತಿ")):
                continue
        if line in PICK_LINES:
            out.append(SAY_NUMBER[lang])
            continue
        if (m := _OPTION_LINE.match(line)):
            rest = m.group(2).replace(" · ", ", ").rstrip(".")
            out.append(OPTION[lang].format(i=m.group(1), rest=rest))
            continue
        out.append(line.replace(" · ", ", "))
    if had_link:
        out.append(LINK_SENT[lang])
    spoken = " ".join(out)
    spoken = _EMOJI.sub("", spoken)
    spoken = speak_money(spoken, lang)
    spoken = re.sub(r"\s*\(([^()]*)\)", r", \1", spoken)          # "(₹500)" reads as ", 500 rupees"
    spoken = speak_when(spoken, lang, today)
    spoken = re.sub(r"\bDr\.?\s*", DR[lang], spoken)
    spoken = re.sub(r"\s+([,.।?!])", r"\1", spoken)
    return re.sub(r"\s{2,}", " ", spoken).strip()


def speak_card(card: dict | None, lang: str | None, today: date | None = None) -> str:
    """A consent or confirmation card as a spoken question, ending with "Say yes or no."."""
    if not card:
        return ""
    lang = _lang(lang)
    d = card.get("details") or {}
    action = d.get("action") or card.get("action")
    if card.get("kind") == "confirm" and d.get("doctor") and action in CARD:
        when = d.get("new_when") if action == "reschedule" else d.get("when")
        if d.get("token_no") and "token" not in (when or ""):
            when = f"{when}, token {d['token_no']}"
        fee = RUPEES[lang].format(n=d["fee_inr"]) if d.get("fee_inr") else ""
        q = CARD[action][lang].format(doctor=d["doctor"], when=when or "", fee=fee).replace(", ,", ",")
        q = re.sub(r",\s*([?।.])", r"\1", q)
    else:
        q = card.get("summary") or ""
    return f"{to_speech(q, lang, today)} {SAY_YES_NO[lang]}".strip()
