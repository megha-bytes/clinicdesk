"""Reply language stays put unless the patient changes it (DEVLOG #35)."""
import pytest

from app.graphs.language import detect
from app.graphs.clinicdesk import send
from tests.test_booking_flow import Fake, graph_for


@pytest.mark.parametrize("text,guess,prev,expected", [
    ("Naale Dr.Rao jothe appointment beku", "hi", None, "kn"),     # live run: model said Hindi
    ("Kal Dr. Rao ke saath appointment chahiye", "en", None, "hi"),
    ("kannada", "en", "hi", "kn"),
    ("kannada please", "hi", "hi", "kn"),
    ("Hindi mein bolo", "kn", "kn", "hi"),
    ("8739478947", "hi", "kn", "kn"),                             # live run: a number flipped it to Hindi
    ("23", "hi", "kn", "kn"),
    ("nanna hesaru henry", "kn", "kn", "kn"),
    ("ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಬರಬಹುದೇ", "en", "en", "kn"),
    ("कल सुबह", "en", "kn", "hi"),
    ("I would like to book with the dermatologist please", "hi", "hi", "en"),
    ("Hi, I would like an appointment with Dr Rao", "en", None, "en"),
    ("ok", "hi", None, "hi"),
])
def test_detect(text, guess, prev, expected):
    assert detect(text, guess, prev) == expected


def test_live_kannada_conversation_stays_in_kannada(session):
    """Replays the live run: every reply after the first Kannada message is in Kannada."""
    from app.seed import seed
    from tests.test_booking_flow import NOW
    cid = seed(session, now=NOW)["clinic_id"]
    convo = [("Naale Dr.Rao jothe appointment beku", {"intent": "book", "language": "hi", "doctor": "Dr.Rao",
                                                       "date": "tomorrow"}),
             ("nanna hesaru henry", {"intent": "info", "language": "kn", "name": "henry"}),
             ("8739478947", {"intent": "info", "language": "hi", "phone": "8739478947"}),
             ("23", {"intent": "info", "language": "hi", "age": 23})]
    fake = Fake({t: e for t, e in convo})
    g, _ = graph_for(session, cid, fake)
    out = None
    for text, _ in convo:
        out = send(g, "t", text)
        assert out["language"] == "kn"
    assert out["interrupt"]["kind"] == "consent" and "ಸರಿಯೇ?" in out["interrupt"]["summary"]
