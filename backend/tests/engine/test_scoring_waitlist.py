from datetime import time, timedelta

from app.engine import Interval, Preferences, WaitlistRequest, free_slots, match_freed_slot, rank_slots
from tests.engine.conftest import MON, SUN, TZ, T, booking

MORNING = (time(9, 0), time(12, 0))


def _candidates(doctors, day, mins, bookings, now):
    return [(d, s) for d in doctors for s in free_slots(d, day, mins, bookings, now, TZ)]


def test_top3_are_spread_out_and_near_requested_time(rao, now):
    prefs = Preferences(preferred_start=T(MON, "10:00"), window=MORNING)
    top = rank_slots(_candidates([rao], MON, 20, [], now), [], prefs, now, TZ)
    starts = [r.slot.start for r in top]
    assert len(top) == 3 and starts[0] == T(MON, "10:00")
    assert all(abs((a - b).total_seconds()) >= 30 * 60 for i, a in enumerate(starts) for b in starts[i + 1:])
    assert all(T(MON, "09:00") <= s < T(MON, "12:00") for s in starts)


def test_requested_window_beats_closeness(rao, now):
    # Asked for "evening" but anchored at 13:00: evening slots should still win.
    prefs = Preferences(preferred_start=T(MON, "13:00"), window=(time(17, 0), time(20, 0)))
    top = rank_slots(_candidates([rao], MON, 20, [], now), [], prefs, now, TZ)
    assert all(r.slot.start >= T(MON, "17:00") for r in top)


def test_preferred_doctor_wins(rao, iyer, now):
    prefs = Preferences(preferred_start=T(MON, "10:00"), preferred_doctor_id="iyer")
    top = rank_slots(_candidates([rao, iyer], MON, 20, [], now), [], prefs, now, TZ)
    assert top[0].doctor_id == "iyer" and "requested doctor" in top[0].reasons


def test_continuity_and_language_break_ties(rao, iyer, now):
    prefs = Preferences(preferred_start=T(MON, "10:00"), previous_doctor_id="rao", language="kn")
    top = rank_slots(_candidates([rao, iyer], MON, 20, [], now), [], prefs, now, TZ)
    assert top[0].doctor_id == "rao"
    assert "same doctor as last visit" in top[0].reasons and "speaks kn" in top[0].reasons


def test_fragmentation_penalised(rao, now):
    # 10:00–10:20 booked. A 20-min slot at 10:25 would leave a 5-min hole; 10:20 leaves none.
    existing = [booking("a", "rao", MON, "10:00", 20)]
    prefs = Preferences(preferred_start=T(MON, "10:22"))
    cands = [(rao, Interval(T(MON, "10:20"), T(MON, "10:40"))), (rao, Interval(T(MON, "10:25"), T(MON, "10:45")))]
    top = rank_slots(cands, existing, prefs, now, TZ, spread_min=0)
    assert top[0].slot.start == T(MON, "10:20")
    assert any("short gap" in r for r in top[1].reasons)


def test_ranking_is_deterministic(rao, iyer, now):
    prefs = Preferences(preferred_start=T(MON, "10:00"))
    c = _candidates([rao, iyer], MON, 20, [], now)
    assert [(r.doctor_id, r.slot.start) for r in rank_slots(c, [], prefs, now, TZ)] == \
           [(r.doctor_id, r.slot.start) for r in rank_slots(list(reversed(c)), [], prefs, now, TZ)]


def _req(id, created_min, start="09:00", end="13:00", mins=20, doctor="rao"):
    return WaitlistRequest(id=id, patient_id=f"p-{id}", doctor_id=doctor, duration_min=mins,
                           window_start=T(MON, start), window_end=T(MON, end),
                           created_at=T(SUN, "10:00") + timedelta(minutes=created_min))


def test_waitlist_first_come_first_served(now):
    freed = Interval(T(MON, "10:00"), T(MON, "10:20"))
    req, slot = match_freed_slot([_req("late", 30), _req("early", 5)], "rao", freed, now)
    assert req.id == "early" and slot == freed


def test_waitlist_skips_requests_that_dont_fit(now):
    freed = Interval(T(MON, "10:00"), T(MON, "10:10"))       # 10-min gap
    reqs = [_req("needs20", 1, mins=20), _req("evening", 2, start="17:00", end="20:00", mins=10),
            _req("other-doc", 3, doctor="iyer", mins=10), _req("fits", 4, mins=10)]
    req, slot = match_freed_slot(reqs, "rao", freed, now)
    assert req.id == "fits" and slot.minutes == 10


def test_waitlist_ignores_non_waiting_and_returns_none(now):
    r = _req("x", 1)
    r.status = "offered"
    assert match_freed_slot([r], "rao", Interval(T(MON, "10:00"), T(MON, "10:20")), now) is None


def test_waitlist_slot_starts_when_window_opens(now):
    freed = Interval(T(MON, "09:00"), T(MON, "10:00"))
    req, slot = match_freed_slot([_req("a", 1, start="09:30", end="11:00")], "rao", freed, now)
    assert slot.start == T(MON, "09:30")
