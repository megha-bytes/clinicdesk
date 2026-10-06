from datetime import timedelta

import pytest

from app.engine import Status, current_pace, estimate_wait, next_token_no, queue_order
from tests.engine.conftest import MON, TZ, T, booking


def _token_day():
    # Five patients on Dr. Rao's morning queue, tokens 1–5.
    return [booking(f"t{i}", "rao", MON, f"09:{(i - 1) * 10:02d}", 10, token_no=i) for i in range(1, 6)]


def test_next_token_never_reuses_numbers():
    bs = _token_day()
    bs[4].status = Status.CANCELLED
    assert next_token_no(bs, "rao", MON, TZ) == 6
    assert next_token_no([], "rao", MON, TZ) == 1


def test_queue_is_by_token():
    bs = _token_day()
    assert [b.token_no for b in queue_order(bs, "rao", MON, TZ)] == [1, 2, 3, 4, 5]


def test_staff_urgent_goes_first_but_nothing_else_reorders():
    bs = _token_day()
    bs[3].urgent_by_staff = True          # token 4
    assert [b.token_no for b in queue_order(bs, "rao", MON, TZ)] == [4, 1, 2, 3, 5]


def test_cancelled_completed_and_in_consult_leave_queue():
    bs = _token_day()
    bs[0].status = Status.COMPLETED
    bs[1].status = Status.CANCELLED
    bs[2].status, bs[2].started_at = Status.CHECKED_IN, T(MON, "09:20")
    assert [b.token_no for b in queue_order(bs, "rao", MON, TZ)] == [4, 5]


def test_pace_defaults_to_doctor_average(rao):
    assert current_pace(rao, _token_day(), MON, TZ) == 10


def test_pace_learns_from_today(rao):
    bs = _token_day()
    # Five 16-minute consults today → fully trust today's pace.
    for i in range(5):
        bs[i].status = Status.COMPLETED
        bs[i].started_at = T(MON, "09:00") + timedelta(minutes=16 * i)
        bs[i].completed_at = bs[i].started_at + timedelta(minutes=16)
    assert current_pace(rao, bs, MON, TZ) == pytest.approx(16)


def test_pace_blends_when_few_consults_done(rao):
    bs = _token_day()
    bs[0].status, bs[0].started_at, bs[0].completed_at = Status.COMPLETED, T(MON, "09:00"), T(MON, "09:20")
    # one 20-min consult, trust 1/5 → 0.2*20 + 0.8*10 = 12
    assert current_pace(rao, bs, MON, TZ) == pytest.approx(12)


def test_wait_before_clinic_opens(rao):
    bs = _token_day()
    est = estimate_wait(bs[2], rao, bs, T(MON, "08:30"), TZ)   # token 3, two ahead
    # 30 min until 09:00 + 2 × 10 = 50
    assert (est.position, est.patients_ahead, est.minutes, est.now_serving) == (3, 2, 50, None)


def test_wait_with_consult_in_progress(rao):
    bs = _token_day()
    bs[0].status, bs[0].started_at = Status.CHECKED_IN, T(MON, "09:00")
    est = estimate_wait(bs[3], rao, bs, T(MON, "09:04"), TZ)   # token 4; tokens 2, 3 ahead
    # 6 min left on token 1 + 2 × 10 = 26 → rounded to 30
    assert (est.now_serving, est.patients_ahead, est.minutes) == (1, 2, 30)


def test_overrunning_consult_assumes_a_little_left(rao):
    bs = _token_day()
    bs[0].status, bs[0].started_at = Status.CHECKED_IN, T(MON, "09:00")
    est = estimate_wait(bs[1], rao, bs, T(MON, "09:25"), TZ)   # token 2, next up, token 1 over by 15 min
    assert est.patients_ahead == 0 and est.minutes == 5          # 2 min remaining → rounds to 5


def test_wait_updates_as_doctor_slows_down(rao):
    bs = _token_day()
    fast = estimate_wait(bs[4], rao, bs, T(MON, "09:00"), TZ).minutes
    for i in range(2):
        bs[i].status = Status.COMPLETED
        bs[i].started_at = T(MON, "09:00") + timedelta(minutes=25 * i)
        bs[i].completed_at = bs[i].started_at + timedelta(minutes=25)
    slow = estimate_wait(bs[4], rao, bs, T(MON, "09:50"), TZ)
    assert slow.pace_min > 10
    assert slow.minutes > fast - 40   # fewer people ahead, but each takes longer
    assert slow.patients_ahead == 2


def test_next_patient_with_nobody_in_consult_waits_zero(rao):
    bs = _token_day()
    assert estimate_wait(bs[0], rao, bs, T(MON, "09:30"), TZ).minutes == 0


def test_estimate_rejects_booking_not_waiting(rao):
    bs = _token_day()
    bs[0].status = Status.CANCELLED
    with pytest.raises(ValueError):
        estimate_wait(bs[0], rao, bs, T(MON, "09:00"), TZ)
