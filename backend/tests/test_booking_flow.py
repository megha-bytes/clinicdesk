"""Deterministic booking flow (DEVLOG #31, #32): the model only extracts details; code decides; templates reply.

The fake "understand" step returns whatever extraction a test gives it, including the kinds of
mistakes the real model made in live runs (wrong year, intent "info" for "3", 11-digit phone).
"""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from app import models as m
from app.agent.models import MemoryLedger, ModelRouter, load_tiers
from app.agent.tools import ToolContext
from app.graphs.booking_flow import clean_date, merge_details, pick_option, render, unverified_times
from app.graphs.clinicdesk import answer, build_graph, pending_interrupt, send
from app.seed import seed
from tests.fakes import FakeClient, msg

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 12, 9, 47, tzinfo=IST)          # Monday


@pytest.fixture
def clinic(session):
    return seed(session, now=NOW)["clinic_id"]


class Fake:
    """Plays the models. `extract[text]` is what 'understand' returns for that patient message."""

    def __init__(self, extract: dict, reply="Sure."):
        self.extract, self.reply, self.steps, self.agent_calls = extract, reply, [], 0

    def __call__(self, kw):
        system = kw["messages"][0]["content"]
        if "You classify messages" in system:
            text = kw["messages"][1]["content"].split("Patient: ")[-1]
            return msg(json.dumps({"language": "en", **self.extract.get(text, {"intent": "smalltalk"})}))
        if "action step" in system:
            self.agent_calls += 1
            return msg("DONE")
        self.steps.append(json.loads(system.split("Say only what THIS STEP needs:\n", 1)[1].split("\nRules:")[0])
                          if "THIS STEP" in system else None)
        return msg(self.reply(len(self.steps)) if callable(self.reply) else self.reply)


def graph_for(session, clinic_id, fake, checkpointer=None, now=NOW):
    ledger = MemoryLedger()
    router = ModelRouter(FakeClient(fake), tiers=load_tiers(), ledger=ledger)
    return build_graph(router, lambda: ToolContext(session=session, clinic_id=clinic_id, now=now), checkpointer), ledger


NEHA = "Book Dr. Rao tomorrow. New patient Neha Rao, 30, 9123456789"
NEHA_EXT = {"intent": "book", "doctor": "Dr. Rao", "date": "tomorrow", "name": "Neha Rao", "age": 30,
            "phone": "9123456789", "new_patient": True}


def patients(session):
    return session.scalar(select(func.count()).select_from(m.Patient))


def test_new_patient_books_end_to_end(session, clinic):
    fake = Fake({NEHA: NEHA_EXT, "2": {"intent": "info", "option": 2}})   # live model called "3" info
    g, ledger = graph_for(session, clinic, fake)

    out = send(g, "t", NEHA)
    assert out["interrupt"]["kind"] == "consent" and patients(session) == 60

    out = answer(g, "t", "yes")
    assert out["interrupt"] is None
    assert "Free with Dr. Ananya Rao (₹500):\n1. Tue 13 Oct" in out["reply"] and "Reply with a number." in out["reply"]
    when = out["reply"].split("2. ", 1)[1].split("\n")[0]

    out = send(g, "t", "2")
    q = out["interrupt"]
    assert q["kind"] == "confirm" and when in q["summary"] and "₹500" in q["summary"]

    out = answer(g, "t", "yes")
    assert f"✅ Booked: Dr. Ananya Rao, {when}, ₹500." in out["reply"] and "Pay: " in out["reply"]
    assert len(out["reply"].split()) < 20                                  # phone-sized
    p = session.scalar(select(m.Patient).where(m.Patient.phone == "9123456789"))
    appt = session.scalar(select(m.Appointment).where(m.Appointment.patient_id == p.id))
    assert appt.status == "confirmed" and p.consent_at is not None
    assert session.scalar(select(m.Payment.amount_inr).where(m.Payment.appointment_id == appt.id)) == 500
    assert fake.agent_calls == 0 and fake.steps == []                     # no model decides or writes
    assert {r.purpose for r in ledger.records} == {"intent"}


def test_live_run_replay_eleven_digit_phone(session, clinic):
    """DEVLOG #31: phone had 11 digits; the assistant said 'noted' and could never book."""
    fake = Fake({
        "I want an appointment with Dr. Rao": {"intent": "book", "doctor": "Dr. Rao"},
        "I am John, my phone number is 87976545677, I am 23 years old":
            {"intent": "book", "name": "John", "phone": "87976545677", "age": 23},
        "sorry, 9823461744": {"intent": "book", "phone": "9823461744"},
    })
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", "I want an appointment with Dr. Rao")
    assert "Free with Dr. Ananya Rao" in out["reply"]                      # options straight away
    assert out["reply"].endswith("Your name and mobile number?")

    out = send(g, "t", "I am John, my phone number is 87976545677, I am 23 years old")
    assert "That number has 11 digits" in out["reply"] and out["interrupt"] is None

    out = send(g, "t", "sorry, 9823461744")
    assert out["interrupt"]["kind"] == "consent"                           # name + age remembered


def test_existing_patient_needs_no_consent_and_can_pick_by_time(session, clinic):
    p = session.scalars(select(m.Patient).where(m.Patient.consent_at.is_not(None))).first()
    first = f"Dr. Rao tomorrow please, {p.name}, {p.phone}"
    fake = Fake({first: {"intent": "book", "doctor": "Rao", "date": "tomorrow", "name": p.name, "phone": p.phone}})
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", first)
    assert out["interrupt"] is None
    when = out["reply"].split("1. ", 1)[1].split("\n")[0]                  # e.g. "Tue 13 Oct, 9:20 AM"
    clock = when.split(", ")[1]
    fake.extract[f"the {clock} one"] = {"intent": "book"}
    out = send(g, "t", f"the {clock} one")
    assert out["interrupt"]["kind"] == "confirm" and when in out["interrupt"]["summary"]


def test_choice_before_details_goes_straight_to_the_card_after_consent(session, clinic):
    fake = Fake({"Dr. Rao tomorrow": {"intent": "book", "doctor": "Dr. Rao", "date": "tomorrow"},
                 "1": {"intent": "book", "option": 1},
                 "Neha Rao, 30, 9123456789": {"intent": "book", "name": "Neha Rao", "age": 30, "phone": "9123456789"}})
    g, _ = graph_for(session, clinic, fake)
    send(g, "t", "Dr. Rao tomorrow")
    out = send(g, "t", "1")
    assert out["interrupt"] is None and out["reply"].startswith("Got it: Tue 13 Oct")
    assert send(g, "t", "Neha Rao, 30, 9123456789")["interrupt"]["kind"] == "consent"
    q = answer(g, "t", "yes")["interrupt"]
    assert q["kind"] == "confirm" and "Dr. Ananya Rao" in q["summary"]


def test_declined_consent_stores_nothing(session, clinic):
    fake = Fake({NEHA: NEHA_EXT})
    g, _ = graph_for(session, clinic, fake)
    send(g, "t", NEHA)
    out = answer(g, "t", "no")
    assert out["interrupt"] is None and "staff can call you" in out["reply"]
    assert patients(session) == 60


def test_declined_booking_changes_nothing_and_offers_options_again(session, clinic):
    fake = Fake({NEHA: NEHA_EXT, "1": {"intent": "book", "option": 1}})
    g, _ = graph_for(session, clinic, fake)
    send(g, "t", NEHA)
    answer(g, "t", "yes")
    send(g, "t", "1")
    out = answer(g, "t", "no")
    assert out["reply"].startswith("OK, not booked.\nFree with Dr. Ananya Rao")
    p = session.scalar(select(m.Patient).where(m.Patient.phone == "9123456789"))
    assert "confirmed" not in session.scalars(select(m.Appointment.status).where(m.Appointment.patient_id == p.id)).all()


def test_paused_card_survives_a_restart(session, clinic, tmp_path):
    path = str(tmp_path / "cp.db")
    fake = Fake({NEHA: NEHA_EXT, "1": {"intent": "book", "option": 1}})
    g1, _ = graph_for(session, clinic, fake, SqliteSaver(sqlite3.connect(path, check_same_thread=False)))
    send(g1, "t", NEHA)
    answer(g1, "t", "yes")
    send(g1, "t", "1")
    del g1
    g2, _ = graph_for(session, clinic, fake, SqliteSaver(sqlite3.connect(path, check_same_thread=False)))
    assert pending_interrupt(g2, "t")["kind"] == "confirm"
    assert "✅ Booked" in answer(g2, "t", "yes")["reply"]


def test_invented_times_in_a_model_reply_are_rejected(session, clinic):
    """Non-booking replies are still model-written; times not in the tool results are rejected."""
    fake = Fake({"what are your timings": {"intent": "info"}}, reply="We have 09:05 and 09:35 free. [Tool: find_slots]")
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", "what are your timings")
    assert len(fake.steps) == 2 and "09:05" not in out["reply"] and "Tool" not in out["reply"]


# ------------------------------------------------------------------ second live run (DEVLOG #32), 8:38 pm
EVENING = datetime(2026, 10, 12, 20, 38, tzinfo=IST)


def test_evening_without_a_date_finds_tomorrow(session, clinic):
    """The model filled in date="today" though the patient gave none; today was over, so nothing showed."""
    fake = Fake({"Hi, I want to book an appointment with Rao": {"intent": "book", "doctor": "Rao", "date": "today"}})
    g, _ = graph_for(session, clinic, fake, now=EVENING)
    out = send(g, "t", "Hi, I want to book an appointment with Rao")
    assert "is full" not in out["reply"] and "(₹500):\n1. Tue 13 Oct" in out["reply"]


def test_requested_day_full_falls_back_to_next_days(session, clinic):
    fake = Fake({"Dr. Rao today please": {"intent": "book", "doctor": "Dr. Rao", "date": "today"}})
    g, _ = graph_for(session, clinic, fake, now=EVENING)
    out = send(g, "t", "Dr. Rao today please")
    assert out["reply"].startswith("Dr. Ananya Rao is full on Mon 12 Oct.")
    assert "1. Tue 13 Oct" in out["reply"]


def test_picking_by_time_keeps_the_options_and_tolerates_am_pm(session, clinic):
    """'I would like the 12:05 am slot' re-searched and lost the list; the slot was 12:05 PM."""
    fake = Fake({"Dr. Rao tomorrow": {"intent": "book", "doctor": "Dr. Rao", "date": "tomorrow"}})
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", "Dr. Rao tomorrow")
    first = out["reply"].split("1. ", 1)[1].split("\n")[0]                 # e.g. "Tue 13 Oct, 9:20 AM"
    clock = first.split(", ")[1]
    wrong = clock.replace("AM", "pm") if "AM" in clock else clock.replace("PM", "am")
    msg_ = f"I would like the {wrong} slot thanks"
    fake.extract[msg_] = {"intent": "book", "time": "21:20"}                # model also extracts a "new" time
    out = send(g, "t", msg_)
    assert f"Got it: {first}" in out["reply"]


def test_phone_that_isnt_a_mobile_number(session, clinic):
    fake = Fake({"My name is megh and phone number is 2345637389":
                 {"intent": "book", "name": "megh", "phone": "2345637389"}})
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", "My name is megh and phone number is 2345637389")
    assert out["reply"].startswith("2345637389 isn't a mobile number; please check.\nWhich doctor")


def test_hindi_booking_reply():
    say = {"problems": [("phone_len", {"n": 11})], "ask": ["phone"], "options": None}
    assert render(say, "hi") == "इस नंबर में 11 अंक हैं; कृपया 10 अंक भेजें।"


def test_model_cannot_pick_a_date_in_the_past(session, clinic):
    fake = Fake({"Dr. Rao on 20 October": {"intent": "book", "doctor": "Dr. Rao", "date": "2024-10-20"}})
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", "Dr. Rao on 20 October")
    assert "1. Tue 20 Oct" in out["reply"]


def test_other_intents_still_use_the_agent(session, clinic):
    fake = Fake({"what are your timings": {"intent": "info"}})
    g, _ = graph_for(session, clinic, fake)
    send(g, "t", "what are your timings")
    assert fake.agent_calls == 1


# ------------------------------------------------------------------ helpers
class Ctx:
    class local_now:  # noqa: N801
        @staticmethod
        def date():
            return NOW.date()
    local_now = NOW


def test_clean_date():
    assert clean_date("2024-10-20", Ctx) == "2026-10-20"
    assert clean_date("2026-01-05", Ctx) == "2027-01-05"
    assert clean_date("tomorrow", Ctx) == "2026-10-13" and clean_date("next week", Ctx) is None
    assert clean_date("today", Ctx, "book with Rao") is None                 # not said → not used
    assert clean_date("today", Ctx, "aaj shaam") == "2026-10-12" and clean_date("tomorrow", Ctx, "कल सुबह") == "2026-10-13"
    assert clean_date("2026-10-20", Ctx, "20th October") == "2026-10-20"
    assert clean_date("2026-10-21", Ctx, "My mobile is 8376897656, name is asha") is None   # digits aren't a date


def test_merge_details_only_keeps_what_was_typed():
    d, problems = merge_details({}, {"name": "Ravi", "age": 40, "phone": "9123456789"}, "book me please")
    assert d == {} and problems == []
    d, problems = merge_details({}, {}, "I'm Asha, 9123456789, 34 years")
    assert d["phone"] == "9123456789" and d["age"] == 34
    d, problems = merge_details({}, {"phone": "87976545677"}, "87976545677")
    assert "phone" not in d and problems == [("phone_len", {"n": 11})]


def test_pick_option():
    offered = [{"label": "Dr. A · Tue 13 Oct, 9:20 AM · ₹500"}, {"label": "Dr. A · Tue 13 Oct, 10:00 AM · ₹500"}]
    assert pick_option(offered, {}, "2") is offered[1]
    assert pick_option(offered, {"option": 1}, "the first") is offered[0]
    assert pick_option(offered, {}, "10 am works") is offered[1]
    assert pick_option(offered, {}, "5") is None and pick_option(offered, {}, "maybe") is None


def test_unverified_times():
    assert unverified_times("Come at 9:20 AM", "Tue 13 Oct, 9:20 AM") == set()
    assert unverified_times("We have 09:00 free", "9:20 AM") == {(9, 0), (21, 0)}
    assert unverified_times("Thanks!", "") == set()


def test_model_replies_are_phone_sized():
    from app.graphs.clinicdesk import CONSENT_TEXT, shorten
    long = ("Our clinic is open Monday to Saturday. **Dr. Rao** sees patients 9 to 1. "
            "Please arrive early. Bring your old reports. Parking is available.")
    out = shorten(long)
    assert out == "Our clinic is open Monday to Saturday. Dr. Rao sees patients 9 to 1."
    assert all(len(v.split()) <= 30 for v in CONSENT_TEXT.values())


def test_invented_option_number_is_ignored():
    """DEVLOG #34: 'I'm ehnas, my mobile is 7896578678' came back as option 1."""
    offered = [{"label": "Dr. A · Tue 20 Oct, 9:50 AM · ₹500", "short": "Tue 20 Oct, 9:50 AM"},
               {"label": "Dr. A · Tue 20 Oct, 11:45 AM · ₹500", "short": "Tue 20 Oct, 11:45 AM"}]
    assert pick_option(offered, {"option": 1}, "I'm ehnas, my mobile is 7896578678") is None
    assert pick_option(offered, {"option": 2}, "the second one please") is offered[1]
    assert pick_option(offered, {"option": 2}, "dusra wala") is offered[1]
    assert pick_option(offered, {"option": 1}, "option 1, I'm Asha") is offered[0]
    assert pick_option(offered, {}, "Age : 23,  I want to come in at 11:45") is offered[1]


# ------------------------------------------------------------------ fourth live run (DEVLOG #37)
def test_first_visit_is_not_option_one_and_lead_number_picks():
    offered = [{"label": "a", "short": "Wed 21 Oct, 11:15 AM"}, {"label": "b", "short": "Wed 21 Oct, 12:25 PM"},
               {"label": "c", "short": "Wed 21 Oct, 9:10 AM"}]
    assert pick_option(offered, {"option": 1}, "3, not my first visit") is offered[2]
    assert pick_option(offered, {"option": 1}, "not my first visit") is None
    assert pick_option(offered, {"option": 3}, "3. My mobile is 8376897656, name is asha") is offered[2]
    assert pick_option(offered, {}, "the first one") is offered[0]
    assert pick_option(offered, {}, "3 years old") is None


def test_age_needs_an_age_word_or_our_question():
    d, _ = merge_details({}, {"age": 3}, "3, not my first visit", used=frozenset({"3"}))
    assert "age" not in d
    d, _ = merge_details({}, {"age": 30, "phone": "9123456789"}, "Neha Rao, 30, 9123456789")
    assert d["age"] == 30
    d, _ = merge_details({}, {"age": 3}, "3, not my first visit", asked_age=True, used=frozenset({"3"}))
    assert "age" not in d                                                    # 3 picked the option
    d, _ = merge_details({}, {}, "23", asked_age=True)
    assert d["age"] == 23
    d, _ = merge_details({}, {"age": 41}, "I'm Ravi, 41 years, 9123456780")
    assert d["age"] == 41


def test_live_run_replay_asha(session, clinic):
    first = "Hi, is ananya available for an appointment tomorrow at 12pm?"
    second = "3. My mobile is 8376897656, name is asha"
    third = "3, not my first visit"
    fake = Fake({first: {"intent": "book", "doctor": "Ananya", "date": "tomorrow", "time": "12:00"},
                 second: {"intent": "book", "option": 3, "phone": "8376897656", "name": "asha", "date": "2026-10-13"},
                 third: {"intent": "book", "option": 1, "age": 3, "new_patient": False}})   # what the model sent
    g, _ = graph_for(session, clinic, fake)
    out = send(g, "t", first)
    option3 = out["reply"].split("3. ", 1)[1].split("\n")[0]
    out = send(g, "t", second)
    assert out["reply"].startswith(f"Got it: {option3}.") and fake.steps == []
    out = send(g, "t", third)
    assert out["interrupt"] is None and out["reply"].startswith("I couldn't find Asha with that number.")
    out = send(g, "t", "I'm 34 years old")
    fake.extract["I'm 34 years old"] = {"intent": "book", "age": 34}
    assert out["interrupt"]["kind"] == "consent"
    q = answer(g, "t", "yes")["interrupt"]
    assert q["kind"] == "confirm" and option3 in q["summary"]


def test_typed_answer_to_a_card_changes_the_choice(session, clinic):
    from app.realtime import runner
    fake = Fake({NEHA: NEHA_EXT, "1": {"intent": "book", "option": 1}})
    g, _ = graph_for(session, clinic, fake)
    send(g, "t", NEHA)
    out = answer(g, "t", "yes")
    third = out["reply"].split("3. ", 1)[1].split("\n")[0]                 # e.g. "Tue 13 Oct, 11:45 AM"
    first_hold = send(g, "t", "1")["interrupt"]
    typed = f"No I asked for 3, {third.split(', ')[1]}"
    fake.extract[typed] = {"intent": "book", "option": 3}
    out = runner.run(g, "t", approved=typed)
    assert out["interrupt"]["kind"] == "confirm" and third in out["interrupt"]["summary"]
    assert first_hold["summary"] != out["interrupt"]["summary"]
    held = session.scalars(select(m.Appointment.status).where(m.Appointment.status == "held")).all()
    assert len(held) == 1                                                    # the declined hold was released


# ------------------------------------------------------------------ first browser run (DEVLOG #38)
def test_returning_patient_only_when_they_say_so():
    d, _ = merge_details({}, {"new_patient": False, "name": "Firdauz", "phone": "7890987898"},
                         "My name is Firdauz and mobile is 7890987898")
    assert "new_patient" not in d
    d, _ = merge_details({}, {}, "3, not my first visit")
    assert d["new_patient"] is False
    d, _ = merge_details({}, {}, "I'm a new patient")
    assert d["new_patient"] is True


def test_got_it_is_said_once(session, clinic):
    fake = Fake({"Dr. Rao tomorrow": {"intent": "book", "doctor": "Dr. Rao", "date": "tomorrow"},
                 "1": {"intent": "book", "option": 1},
                 "My name is Firdauz and mobile is 7890987898": {"intent": "book", "name": "Firdauz",
                                                                "phone": "7890987898", "new_patient": False}})
    g, _ = graph_for(session, clinic, fake)
    send(g, "t", "Dr. Rao tomorrow")
    assert send(g, "t", "1")["reply"].startswith("Got it:")
    out = send(g, "t", "My name is Firdauz and mobile is 7890987898")
    assert out["reply"] == "First visit? Your age, please."
