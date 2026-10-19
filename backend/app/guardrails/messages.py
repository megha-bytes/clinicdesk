"""Short guardrail replies (en / hi / kn). Emergency replies always carry the clinic's emergency numbers."""
from __future__ import annotations

M = {
    "emergency": {
        "en": "This could be an emergency. Please call {ambulance} (ambulance) or {emergency} right now. "
              "I've alerted the clinic staff. If it isn't urgent, reply \"not urgent\".",
        "hi": "यह इमरजेंसी हो सकती है। कृपया अभी {ambulance} (एम्बुलेंस) या {emergency} पर कॉल करें। "
              "मैंने क्लिनिक स्टाफ़ को बता दिया है। अर्जेंट नहीं है तो \"not urgent\" लिखें।",
        "kn": "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿ ಆಗಿರಬಹುದು. ದಯವಿಟ್ಟು ಈಗಲೇ {ambulance} (ಆಂಬ್ಯುಲೆನ್ಸ್) ಅಥವಾ {emergency} ಗೆ ಕರೆ ಮಾಡಿ. "
              "ನಾನು ಕ್ಲಿನಿಕ್ ಸಿಬ್ಬಂದಿಗೆ ತಿಳಿಸಿದ್ದೇನೆ. ತುರ್ತು ಅಲ್ಲದಿದ್ದರೆ \"not urgent\" ಎಂದು ಬರೆಯಿರಿ.",
    },
    "self_harm": {
        "en": "I'm really sorry you're feeling this way. Please call Tele-MANAS {mental_health} (free, 24×7) "
              "or {emergency} now. I've alerted the clinic staff.",
        "hi": "मुझे बहुत दुख है कि आप ऐसा महसूस कर रहे हैं। कृपया अभी Tele-MANAS {mental_health} (मुफ़्त, 24×7) "
              "या {emergency} पर कॉल करें। मैंने क्लिनिक स्टाफ़ को बता दिया है।",
        "kn": "ನೀವು ಹೀಗೆ ಅನುಭವಿಸುತ್ತಿರುವುದಕ್ಕೆ ತುಂಬಾ ವಿಷಾದವಿದೆ. ದಯವಿಟ್ಟು ಈಗಲೇ Tele-MANAS {mental_health} (ಉಚಿತ, 24×7) "
              "ಅಥವಾ {emergency} ಗೆ ಕರೆ ಮಾಡಿ. ನಾನು ಕ್ಲಿನಿಕ್ ಸಿಬ್ಬಂದಿಗೆ ತಿಳಿಸಿದ್ದೇನೆ.",
    },
    "injection": {"en": "I can only help with appointments at this clinic.",
                  "hi": "मैं सिर्फ़ इस क्लिनिक की अपॉइंटमेंट में मदद कर सकता हूँ।",
                  "kn": "ನಾನು ಈ ಕ್ಲಿನಿಕ್‌ನ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್‌ಗಳಿಗೆ ಮಾತ್ರ ಸಹಾಯ ಮಾಡಬಹುದು."},
    "privacy": {"en": "Sorry, I can't share other patients' details.",
                "hi": "माफ़ कीजिए, मैं दूसरे मरीज़ों की जानकारी नहीं दे सकता।",
                "kn": "ಕ್ಷಮಿಸಿ, ಬೇರೆ ರೋಗಿಗಳ ಮಾಹಿತಿ ನೀಡಲು ಆಗುವುದಿಲ್ಲ."},
    "medical": {"en": "I can't give medical advice, but I can book you with a doctor.",
                "hi": "मैं मेडिकल सलाह नहीं दे सकता, पर डॉक्टर से अपॉइंटमेंट बुक कर सकता हूँ।",
                "kn": "ನಾನು ವೈದ್ಯಕೀಯ ಸಲಹೆ ನೀಡಲು ಆಗುವುದಿಲ್ಲ, ಆದರೆ ವೈದ್ಯರ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಬಹುದು."},
    "offer_booking": {"en": "Shall I find a time?", "hi": "समय देखूँ?", "kn": "ಸಮಯ ನೋಡಲೇ?"},
    "handoff": {"en": "I've asked our staff to call you back shortly.",
                "hi": "मैंने स्टाफ़ से कहा है, वे जल्द आपको कॉल करेंगे।",
                "kn": "ಸಿಬ್ಬಂದಿಗೆ ತಿಳಿಸಿದ್ದೇನೆ, ಅವರು ಶೀಘ್ರದಲ್ಲೇ ನಿಮಗೆ ಕರೆ ಮಾಡುತ್ತಾರೆ."},
}
DEFAULT_NUMBERS = {"ambulance": "108", "emergency": "112", "mental_health": "14416"}


def msg(key: str, lang: str, numbers: dict | None = None) -> str:
    row = M[key]
    return row.get(lang, row["en"]).format(**{**DEFAULT_NUMBERS, **(numbers or {})})
