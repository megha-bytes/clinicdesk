"""Which language to reply in (DEVLOG #35).

The model's per-message guess flipped mid-conversation: romanised Kannada ("Naale Dr.Rao jothe
appointment beku") came back as Hindi, and a bare phone number or "23" switched the reply language.
Rules, in order:
1. The patient asks for a language ("kannada please", "Hindi mein", "in English", "ಕನ್ನಡ").
2. The script they write in (Kannada or Devanagari letters).
3. Common words of romanised Kannada / Hindi.
4. Otherwise keep the conversation's language; only the first message uses the model's guess.
"""
from __future__ import annotations

import re

_ASK = [
    ("kn", re.compile(r"\bkannada\b|ಕನ್ನಡ", re.I)),
    ("hi", re.compile(r"\bhindi\b|हिंदी|हिन्दी", re.I)),
    ("en", re.compile(r"\benglish\b|\bangrezi\b|अंग्रेज़ी|ಇಂಗ್ಲಿಷ್", re.I)),
]
_KN_SCRIPT = re.compile(r"[ಀ-೿]")
_HI_SCRIPT = re.compile(r"[ऀ-ॿ]")
_KN_WORDS = re.compile(r"\b(beku|bekagide|naale|nale|ivattu|jothe|nanna|nange|hesaru|illa|ide|idhe|madi|maadi|"
                       r"howdu|houdu|sari|yenu|enu|elli|yavaga|banni|baruttini|appointment\s+beku|vayassu|"
                       r"mane|doctoru|beda|gottilla|swalpa|tumba|hege|yaake|novu|aagtide|aagthide)\b", re.I)
_HI_WORDS = re.compile(r"\b(chahiye|chahie|mujhe|mera|meri|naam|kal|aaj|hai|hain|kya|kab|kaise|ke\s+saath|"
                       r"karna|karni|nahi|nahin|haan|ji|bataiye|batao|saal|umar|subah|shaam|dopahar|accha|theek|mein|dard|ho\s+raha)\b",
                       re.I)
_EN_WORDS = re.compile(r"\b(i|i'm|my|the|want|would|like|please|is|am|can|book|need|tomorrow|today|with|"
                       r"appointment|time|slot|name|phone|mobile|thanks|thank|you|yes|no)\b", re.I)


def asked_for(text: str) -> str | None:
    """A language the patient explicitly asked for, if the message is (mostly) that request."""
    words = re.findall(r"\w+", text)
    if len(words) > 6:
        return None
    for lang, rx in _ASK:
        if rx.search(text):
            return lang
    return None


def detect(text: str, model_guess: str | None, previous: str | None) -> str:
    asked = asked_for(text)
    if asked:
        return asked
    if _KN_SCRIPT.search(text):
        return "kn"
    if _HI_SCRIPT.search(text):
        return "hi"
    kn, hi = len(_KN_WORDS.findall(text)), len(_HI_WORDS.findall(text))
    if kn > hi:
        return "kn"
    if hi > kn:
        return "hi"
    en = len(_EN_WORDS.findall(text))
    if previous and previous != "en" and en >= 3:               # clearly switched to English sentences
        return "en"
    if previous:
        return previous
    return model_guess if model_guess in ("en", "hi", "kn") else "en"
