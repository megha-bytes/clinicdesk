from datetime import timedelta

import pytest

from app.engine import (
    BookingError, ErrorCode, Status, cancel, check_duration, check_in, complete, confirm, hold,
    mark_no_show, reschedule, start_consult,
)
from tests.engine.conftest import FOLLOW_UP, MON, NEW, SUN, TZ, T, booking


def test_hold_uses_appointment_type_length(rao, now):
    h = hold(rao, T(MON, "10:00"), NEW, [], now, TZ, patient_id="p1")
    assert h.status == Status.HELD and (h.end - h.start) == timedelta(minutes=20)
    assert h.hold_expires_at == now + timedelta(minutes=5)
    f = hold(rao, T(MON, "10:30"), FOLLOW_UP, [h], now, TZ)
    assert (f.end - f.start) == timedelta(minutes=10)


def test_first_come_first_served_holds(rao, now):
    first = hold(rao, T(MON, "10:00"), NEW, [], now, TZ, patient_id="p1")
    with pytest.raises(BookingError) as e:
        hold(rao, T(MON, "10:00"), NEW, [first], now, TZ, patient_id="p2")
    assert e.value.code == ErrorCode.OVERLAP


def test_confirm_live_hold(rao, now):
    h = hold(rao, T(MON, "10:00"), NEW, [], now, TZ)
    confirm(h, rao, [h], now + timedelta(minutes=2), TZ)
    assert h.status == Status.CONFIRMED and h.hold_expires_at is None and h.token_no is None


def test_confirm_expired_hold_fails(rao, now):
    h = hold(rao, T(MON, "10:00"), NEW, [], now, TZ)
    with pytest.raises(BookingError) as e:
        confirm(h, rao, [h], now + timedelta(minutes=6), TZ)
    assert e.value.code == ErrorCode.HOLD_EXPIRED


def test_cannot_confirm_twice(rao, now):
    h = hold(rao, T(MON, "10:00"), NEW, [], now, TZ)
    confirm(h, rao, [h], now, TZ)
    with pytest.raises(BookingError) as e:
        confirm(h, rao, [h], now, TZ)
    assert e.value.code == ErrorCode.INVALID_STATUS


def test_confirm_assigns_token_numbers_in_order(rao, now):
    a = hold(rao, T(MON, "09:00"), FOLLOW_UP, [], now, TZ)
    b = hold(rao, T(MON, "09:10"), FOLLOW_UP, [a], now, TZ)
    confirm(a, rao, [a, b], now, TZ, token_style=True)
    confirm(b, rao, [a, b], now, TZ, token_style=True)
    assert (a.token_no, b.token_no) == (1, 2)


def test_check_duration_blocks_invented_lengths():
    check_duration(NEW, T(MON, "10:00"), T(MON, "10:20"))
    with pytest.raises(BookingError) as e:
        check_duration(NEW, T(MON, "10:00"), T(MON, "10:10"))
    assert e.value.code == ErrorCode.WRONG_DURATION


def test_cancel_frees_slot(rao, now):
    b = booking("a", "rao", MON, "10:00", 20)
    freed = cancel(b)
    assert b.status == Status.CANCELLED and freed.start == T(MON, "10:00")
    hold(rao, T(MON, "10:00"), NEW, [b], now, TZ)   # slot is free again


def test_cannot_cancel_completed_or_twice():
    done = booking("a", "rao", MON, "10:00", 20, Status.COMPLETED)
    with pytest.raises(BookingError):
        cancel(done)
    c = booking("b", "rao", MON, "11:00", 20, Status.CANCELLED)
    with pytest.raises(BookingError):
        cancel(c)


def test_reschedule_moves_and_keeps_length(rao, now):
    b = booking("a", "rao", MON, "10:00", 20)
    old = reschedule(b, rao, T(MON, "17:00"), [b], now, TZ)
    assert old.start == T(MON, "10:00")
    assert (b.start, b.end) == (T(MON, "17:00"), T(MON, "17:20"))


def test_reschedule_can_shift_within_own_slot(rao, now):
    b = booking("a", "rao", MON, "10:00", 20)
    reschedule(b, rao, T(MON, "10:10"), [b], now, TZ)
    assert b.start == T(MON, "10:10")


def test_reschedule_rejects_taken_slot_and_leaves_booking_unchanged(rao, now):
    b = booking("a", "rao", MON, "10:00", 20)
    other = booking("b", "rao", MON, "17:00", 20)
    with pytest.raises(BookingError) as e:
        reschedule(b, rao, T(MON, "17:10"), [b, other], now, TZ)
    assert e.value.code == ErrorCode.OVERLAP
    assert b.start == T(MON, "10:00")


def test_reschedule_to_other_doctor(rao, iyer, now):
    b = booking("a", "rao", MON, "10:00", 20)
    reschedule(b, iyer, T(MON, "10:30"), [b], now, TZ)
    assert b.doctor_id == "iyer"


def test_visit_lifecycle(now):
    b = booking("a", "rao", MON, "10:00", 20)
    check_in(b, T(MON, "09:55"))
    start_consult(b, T(MON, "10:02"))
    complete(b, T(MON, "10:15"))
    assert b.status == Status.COMPLETED and b.completed_at == T(MON, "10:15")


def test_cannot_start_without_check_in_or_cancel_mid_consult():
    b = booking("a", "rao", MON, "10:00", 20)
    with pytest.raises(BookingError):
        start_consult(b, T(MON, "10:00"))
    check_in(b, T(MON, "09:55"))
    start_consult(b, T(MON, "10:00"))
    with pytest.raises(BookingError):
        cancel(b)
    with pytest.raises(BookingError):
        mark_no_show(b)


def test_no_show_frees_slot(rao):
    b = booking("a", "rao", MON, "10:00", 20)
    mark_no_show(b)
    assert b.status == Status.NO_SHOW
    hold(rao, T(MON, "10:00"), NEW, [b], T(SUN, "20:00"), TZ)
