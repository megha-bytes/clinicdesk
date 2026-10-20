"""Make what a caller *said* look like what a patient would have *typed*.

Speech recognisers write numbers in many ways: "nine eight four five…", "डबल सात", Devanagari or
Kannada digits (९८४५ / ೯೮೪೫), and a short spoken reply to a list ("option two", "दो नंबर") instead
of "2". The booking flow reads digits, so these are turned into digits here, before the graph.

Conservative on purpose: a lone number word becomes a digit only when it is (almost) the whole
utterance, and spelled-out digits only inside a run of three or more (a phone number). "Kar do"
stays "kar do".

`short_answer` reads a spoken yes/no to a card: "yes please", "haan ji theek hai", "ಹೌದು ಮಾಡಿ".
Anything with a "but", a number or more than a few words is not a plain yes/no (None), so it is
handled as a new message instead (DEVLOG #37).
"""
from __future__ import annotations

import re

_NATIVE = str.maketrans("०१२३४५६७८९೦೧೨೩೪೫೬೭೮೯", "01234567890123456789")

# digit words; romanised Hindi/Kannada ones are ambiguous alone, so only count inside a run of digits
_DIGIT = {
    **dict.fromkeys(["zero", "oh", "o", "शून्य", "ಸೊನ್ನೆ", "sonne", "shunya"], "0"),
    **dict.fromkeys(["one", "एक", "ಒಂದು", "ek", "ondu"], "1"),
    **dict.fromkeys(["two", "दो", "ಎರಡು", "do", "eradu"], "2"),
    **dict.fromkeys(["three", "तीन", "ಮೂರು", "teen", "mooru", "muru"], "3"),
    **dict.fromkeys(["four", "चार", "ನಾಲ್ಕು", "char", "chaar", "naalku", "nalku"], "4"),
    **dict.fromkeys(["five", "पांच", "पाँच", "ಐದು", "paanch", "panch", "aidu", "aydu"], "5"),
    **dict.fromkeys(["six", "छह", "छः", "छे", "ಆರು", "chhe", "chhah", "che", "aaru"], "6"),
    **dict.fromkeys(["seven", "सात", "ಏಳು", "saat", "elu", "yelu"], "7"),
    **dict.fromkeys(["eight", "आठ", "ಎಂಟು", "aath", "entu", "yentu"], "8"),
    **dict.fromkeys(["nine", "नौ", "ಒಂಬತ್ತು", "nau", "ombattu", "ombatthu"], "9"),
}
_LONE_OK = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "शून्य", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "सात", "आठ", "नौ",
            "ಸೊನ್ನೆ", "ಒಂದು", "ಎರಡು", "ಮೂರು", "ನಾಲ್ಕು", "ಐದು", "ಆರು", "ಏಳು", "ಎಂಟು", "ಒಂಬತ್ತು",
            "ek", "do", "teen", "ondu", "eradu", "mooru"}
_REPEAT = {"double": 2, "triple": 3, "डबल": 2, "ट्रिपल": 3, "ಡಬಲ್": 2, "ಟ್ರಿಪಲ್": 3}
# words that may surround a spoken option number: "option two please", "दो नंबर वाला", "ಎರಡನೇ ಆಯ್ಕೆ"
_OPTION_FILLER = {"option", "number", "no", "the", "please", "pls", "one", "slot", "wala", "vala", "wali", "vali",
                  "नंबर", "विकल्प", "वाला", "वाली", "ऑप्शन", "जी", "ji", "ಆಯ್ಕೆ", "ಸಂಖ್ಯೆ", "ನಂಬರ್", "ಆಪ್ಷನ್", "ದಯವಿಟ್ಟು"}
_STRIP = ".,!?।;:\"'()"


def _tokens(text: str) -> list[str]:
    return [t.strip(_STRIP) for t in text.split() if t.strip(_STRIP)]


def _spoken_option(words: list[str]) -> str | None:
    """"option two", "two please", "दो नंबर" → "2" (whole utterance only)."""
    nums = [w for w in words if w.lower() in _LONE_OK]
    rest = [w for w in words if w.lower() not in _LONE_OK and w.lower() not in _OPTION_FILLER]
    if rest or len(nums) != 1 or len(words) > 4:
        return None
    return _DIGIT[nums[0].lower()]


def _join_digit_runs(words: list[str]) -> list[str]:
    """Runs of spoken digits (≥3 digits, at least one spelled out) → one number: "nine eight double four…"."""
    out: list[str] = []
    i = 0
    while i < len(words):
        j, digits, spelled, repeat = i, "", 0, 1
        while j < len(words):
            w = words[j].strip(_STRIP).lower()
            if w in _REPEAT and j + 1 < len(words) and words[j + 1].strip(_STRIP).lower() in _DIGIT:
                repeat = _REPEAT[w]
            elif w in _DIGIT:
                digits += _DIGIT[w] * repeat
                spelled += 1
                repeat = 1
            elif re.fullmatch(r"\d{1,5}", w):
                digits += w
                if words[j].rstrip()[-1:] in ",;:" and j > i:   # "…four five, 30" — a new number starts
                    j += 1
                    break
                if words[j].rstrip()[-1:] in ",;:":            # "30, nine one two…": the age, not the phone
                    break
            else:
                break
            j += 1
        if len(digits) >= 3 and spelled >= 1 and j - i >= 2:
            out.append(digits)
            i = j
        else:
            out.append(words[i])
            i += 1
    return out


def normalise(text: str, *, voice: bool = False) -> str:
    """Native digits → 0-9 always. With `voice`, also spoken numbers → digits."""
    text = (text or "").translate(_NATIVE)
    if not voice:
        return text
    words = _tokens(text)
    if (n := _spoken_option(words)) is not None:
        return n
    if not any(w.lower() in _DIGIT or w.lower() in _REPEAT for w in words):
        return text
    joined = _join_digit_runs(text.split())
    # keep the caller's punctuation on the words we didn't touch
    return re.sub(r"\s+", " ", " ".join(joined)).strip()


# --------------------------------------------------------------------------- spoken yes / no
_YES = {"yes", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm", "confirmed", "correct", "right", "fine",
        "haan", "haa", "ha", "han", "hanji", "haanji", "theek", "thik", "sahi", "chalega", "houdu", "howdu", "haudu",
        "sari", "aagli", "aagali", "hmm", "हाँ", "हां", "हा", "जी", "ठीक", "सही", "चलेगा", "ಹೌದು", "ಸರಿ", "ಆಗಲಿ", "ಓಕೆ",
        "ओके"}
_YES_PHRASES = re.compile(r"\b(go ahead|book it|do it|please do|sounds good|that'?s (fine|right|ok|okay)|"
                          r"kar do|kardo|kar dijiye|book karo|book kar do|madi|maadi|book madi)\b|"
                          r"कर दो|कर दीजिए|बुक करो|बुक कर दो|ಮಾಡಿ|ಬುಕ್ ಮಾಡಿ", re.I)
_NO = {"no", "nope", "nah", "not", "don't", "dont", "cancel", "wait", "stop", "nahi", "nahin", "nhi", "mat", "na",
       "illa", "beda", "bedi", "नहीं", "नही", "मत", "ना", "रुको", "ಇಲ್ಲ", "ಬೇಡ", "ನಿಲ್ಲಿಸಿ"}
_NO_PHRASES = re.compile(r"\b(rehne do|rahne do|ruk(o|iye)|hold on)\b|रहने दो|रुकिए", re.I)
_NO_FILLER = {"rehne", "rahne", "do", "abhi", "now", "later", "baad", "mein", "need", "want", "dont", "i", "ruko",
              "ruk", "jaroorat", "zaroorat", "hold", "on", "रहने", "अभी", "बाद", "में", "ಈಗ", "ಬೇಕಾಗಿಲ್ಲ"}
_BUT = {"but", "lekin", "par", "magar", "aadre", "instead", "change", "different", "another", "other", "badal",
        "badlo", "doosra", "dusra", "bere", "लेकिन", "मगर", "बदल", "दूसरा", "ಆದರೆ", "ಬೇರೆ", "ಬದಲಿಸಿ"}
_FILLER = {"please", "pls", "ji", "jee", "sir", "madam", "ma'am", "thanks", "thank", "you", "it", "is", "that", "that's",
           "thats", "hai", "hain", "ho", "go", "ahead", "book", "do", "kar", "karo", "kardo", "dijiye", "madi", "maadi",
           "very", "good", "great", "perfect", "alright", "all", "dhanyavaad", "dhanyavad", "shukriya", "oh", "uh", "um",
           "है", "जी", "कर", "दो", "दीजिए", "करो", "बुक", "धन्यवाद", "शुक्रिया", "ಮಾಡಿ", "ಬುಕ್", "ಧನ್ಯವಾದ", "ದಯವಿಟ್ಟು"}


def short_answer(text: str) -> bool | None:
    """True / False for a plain spoken (or typed) yes or no to a card; None if it says more than that."""
    raw = (text or "").strip()
    words = [w.lower() for w in _tokens(raw)]
    if not words or len(words) > 6 or re.search(r"\d", raw.translate(_NATIVE)):
        return None
    if any(w in _BUT for w in words):
        return None
    if any(w in _NO for w in words) or _NO_PHRASES.search(raw):
        # "no thanks" is a no; "no, the third one" says more, so it is handled as a new message
        rest = [w for w in words if w not in _NO and w not in _FILLER and w not in _NO_FILLER]
        return False if not rest or _NO_PHRASES.fullmatch(raw.strip(_STRIP)) else None
    yes = any(w in _YES for w in words) or bool(_YES_PHRASES.search(raw))
    if not yes:
        return None
    if all(w in _YES or w in _FILLER for w in words):
        return True
    return None
