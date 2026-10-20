"""Deterministic guardrail checks (no model needed, so they work when the model is down).

Patterns cover English, romanised Hindi/Kannada and Devanagari/Kannada script. They are deliberately
broad for emergencies (a false alarm costs one extra line; a missed emergency costs much more) and
narrow for everything else.
"""
from __future__ import annotations

import re

_I = re.I


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), _I)


EMERGENCY: dict[str, re.Pattern] = {
    "self_harm": _rx(
        r"\b(kill(ing)?|hurt(ing)?|harm(ing)?) (my ?self|myself)\b", r"\bsuicid", r"\bend (my|it all|my life)\b",
        r"\bwant to die\b", r"\bdon'?t want to live\b", r"\bself[- ]?harm", r"\bcut(ting)? myself\b",
        r"\b(marna|mar jana|mar jaun|khudkushi|aatmahatya|atmahatya)\b", r"आत्महत्या", r"मर जा", r"जीना नहीं",
        r"\b(saayabeku|sayabeku|atmahatye|aatmahatye)\b", r"ಆತ್ಮಹತ್ಯೆ", r"ಸಾಯಬೇಕು"),
    "cardiac": _rx(
        r"\bchest (pain|tightness|pressure)\b", r"\bpain in (my )?chest\b", r"\bheart attack\b",
        r"\b(seene|seene mein|chhati|chhaati) (mein )?(dard|dukh)", r"सीने में दर्द", r"छाती में दर्द", r"दिल का दौरा",
        r"\bede ?novu\b", r"\bedeyalli novu\b", r"ಎದೆ ?ನೋವು", r"ಹೃದಯಾಘಾತ"),
    "breathing": _rx(
        r"\b(can'?t|cannot|unable to|difficulty|trouble|hard to) breath", r"\bnot breathing\b", r"\bchoking\b",
        r"\bshort(ness)? of breath\b", r"\bbreathless", r"\bsaa?ns? (nahi|nahin|lene mein)", r"साँस नहीं",
        r"सांस नहीं", r"साँस लेने में", r"\busiru (aadtilla|adtilla|kashta|baralla)", r"ಉಸಿರು (ಆಡುತ್ತಿಲ್ಲ|ಕಷ್ಟ|ಬರುತ್ತಿಲ್ಲ)"),
    "stroke": _rx(
        r"\bstroke\b", r"\bface (is )?droop", r"\bslurred speech\b", r"\b(can'?t|cannot) (move|feel) (my )?(arm|leg|side)",
        r"\blakwa\b", r"लकवा", r"\bpaarshvavayu\b", r"ಪಾರ್ಶ್ವವಾಯು"),
    "unconscious": _rx(
        r"\bunconscious\b", r"\bpassed out\b", r"\bfainted\b", r"\bnot (waking|responding)\b", r"\bcollapsed\b",
        r"\bbe ?hosh\b", r"बेहोश", r"\bprajne (illa|tappi)", r"ಪ್ರಜ್ಞೆ (ಇಲ್ಲ|ತಪ್ಪಿ)", r"ಮೂರ್ಛೆ"),
    "bleeding": _rx(
        r"\b(heavy|severe|lot of|won'?t stop|not stopping) bleed", r"\bbleeding (heavily|a lot|won'?t stop|badly)\b",
        r"\bvomit(ing)? blood\b", r"\bcough(ing)? (up )?blood\b",
        r"\bkhoon (ruk nahi|band nahi|bahut)", r"खून (रुक नहीं|बहुत)", r"\brakta (nilthilla|tumba)", r"ರಕ್ತ (ನಿಲ್ಲುತ್ತಿಲ್ಲ|ತುಂಬಾ)"),
    "poisoning": _rx(
        r"\b(swallowed|drank|ate|took) (poison|bleach|pesticide|acid|kerosene)", r"\boverdose\b", r"\bpoison(ed|ing)\b",
        r"\bzeh(e)?r\b", r"ज़हर", r"जहर", r"\bvisha\b", r"ವಿಷ"),
    "seizure": _rx(r"\bseizures?\b", r"\bfits? (aa|aaya|aa rah|aate|bandide|bantu|banthu)", r"\bhaving (a )?fits?\b",
                   r"\bconvulsi", r"\bmirgi\b", r"मिर्गी", r"दौरा पड़", r"ಫಿಟ್ಸ್", r"ಮೂರ್ಛೆ ರೋಗ"),
    "injury": _rx(r"\b(bad|serious|road) accident\b", r"\bsevere burn", r"\bhead injury\b", r"\bbroken bone\b",
                  r"\bdog bite\b", r"\bsnake ?bite\b", r"\bsaa?np ne kaata\b", r"साँप", r"\bhaavu kachchi", r"ಹಾವು ಕಚ್ಚಿ"),
}

NOT_URGENT = _rx(r"\bnot (urgent|an emergency|emergency)\b", r"\bno emergency\b", r"\bjaldi nahi\b", r"\burgent nahi\b",
                 r"अर्जेंट नहीं", r"\burgent alla\b", r"ತುರ್ತು ಅಲ್ಲ")

INJECTION = _rx(
    r"\bignore (all |the |any |your )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"\b(disregard|forget) (all |your |the )?(instructions|rules|prompt)",
    r"\b(system|developer) prompt\b", r"\byou are now\b", r"\bdeveloper mode\b", r"\bjail ?break",
    r"\bpretend (to be|you are)\b", r"\bact as (an? )?(admin|developer|doctor|system)",
    r"\breveal (your|the) (prompt|instructions|rules)", r"\bdo anything now\b", r"\bDAN\b",
    r"</?(system|assistant)>", r"\[\s*(system|inst)\s*\]",
    r"\b(admin|developer|system) (override|mode|access)\b",
    r"\b(pichhl|pichl|pehle)\w* (sabhi |saare |sab )?(nirdesh|instructions?) (bhool|ignore|chhod)", r"निर्देश (भूल|अनदेखा)",
    r"\b(hinde|hinda)\w* (ella )?suchane\w* (mare|ignore)", r"ಸೂಚನೆ\w* ಮರೆ")

PAYMENT_SECRET = _rx(
    r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)",                         # a card number (phones are 10 digits)
    r"\b(cvv|cvc)\b", r"\b(otp|one[- ]time password)\b\s*(is|:|-)?\s*\d{4,8}", r"\bupi ?pin\b", r"\batm ?pin\b",
    r"\b(card|debit|credit) (number|no\.?)\b\s*(is|:)?\s*\d", r"\bnet ?banking (password|login)\b")

PRIVACY = _rx(
    r"\b(other|another|all) (patients?|people)('s)?\b.*\b(phone|number|name|details|appointments?|list)",
    r"\b(phone|number|details|appointments?) of (other|another|all)\b",
    r"\b(list|show|give) (me )?(all )?(the )?(patients?|bookings|appointments)\b(?! (for|of) me)",
    r"\bwho (else )?(is|are) (booked|coming|seeing)\b", r"\blist of (all )?(the )?(patients|bookings|appointments)\b",
    r"\ball (the )?patients\b", r"\b(someone|somebody) else'?s? (appointment|booking|details|number)\b", r"\b(dusre|doosre) (patient|mareez)",
    r"दूसरे (मरीज़|मरीज)", r"\bbere (rogi|patient)", r"ಬೇರೆ ರೋಗಿ")

MEDICAL = _rx(
    r"\bwhat (medicine|medication|tablet|drug|pill|dose|dosage)\b", r"\bwhich (medicine|tablet|drug|pill|antibiotic)\b",
    r"\b(should|can|could) i (take|use|give)\b.*\b(medicine|tablet|pill|paracetamol|dolo|crocin|antibiotic|syrup|mg)\b",
    r"\bhow (much|many) (mg|tablets?|pills?)\b", r"\b(dose|dosage) (of|for)\b", r"\bis (it|this) (serious|cancer|dangerous)\b",
    r"\bdo i have\b", r"\bdiagnos", r"\bwhat (is wrong|disease)\b", r"\bhow (to|do i) (treat|cure)\b",
    r"\b(kaun ?si|konsi|kya) (dawa|dawai|dava|goli|tablet)\b", r"\b(dawa|dawai|goli) (kya|kaun)", r"कौन सी (दवा|गोली)",
    r"क्या दवा", r"\b(yaava|yav) (maatre|tablet|aushadha|oushadha)\b", r"ಯಾವ (ಮಾತ್ರೆ|ಔಷಧ)")

# Output: things a reply must never contain
ADVICE_OUT = _rx(r"\b\d+\s?(mg|ml|mcg)\b", r"\b(take|try|use) (a |an |some )?(paracetamol|ibuprofen|dolo|crocin|antibiotic|"
                 r"aspirin|cetirizine|antacid|syrup|tablet)", r"\byou (probably |may |might )?have (a |an )?[a-z]+ (infection|disease)",
                 r"\b(it'?s|it is) (probably|likely) (nothing|just)\b")
PHONE_OUT = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
FEE_OUT = re.compile(r"(?:₹|rs\.?|inr)\s?(\d[\d,]*)", _I)


def emergency_kind(text: str) -> str | None:
    for kind, rx in EMERGENCY.items():
        if rx.search(text or ""):
            return kind
    return None
