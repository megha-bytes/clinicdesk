import pytest

from app.engine import BookingError, ErrorCode, Status, free_slots, validate_slot, working_intervals
from tests.engine.conftest import MON, SUN, TZ, T, booking


def test_working_intervals_remove_break(rao):
    w = working_intervals(rao, MON, TZ)
    assert [(i.start.strftime("%H:%M"), i.end.strftime("%H:%M")) for i in w] == [
        ("09:00", "11:00"), ("11:15", "13:00"), ("17:00", "20:00")]


def test_no_hours_on_day_off(rao):
    assert working_intervals(rao, SUN, TZ) == []


def test_no_hours_on_leave(rao):
    rao.leave.add(MON)
    assert working_intervals(rao, MON, TZ) == []


def test_valid_slot_passes(rao, now):
    slot = validate_slot(rao, T(MON, "10:00"), 20, [], now, TZ)
    assert slot.minutes == 20


@pytest.mark.parametrize("start,mins,code", [
    ("08:40", 20, ErrorCode.OUTSIDE_HOURS),      # before opening
    ("12:50", 20, ErrorCode.OUTSIDE_HOURS),      # runs past the session end
    ("14:00", 10, ErrorCode.OUTSIDE_HOURS),      # lunch gap between sessions
    ("10:50", 20, ErrorCode.ON_BREAK),           # overlaps the 11:00 break
    ("11:05", 10, ErrorCode.ON_BREAK),           # inside the break
])
def test_hour_and_break_rules(rao, now, start, mins, code):
    with pytest.raises(BookingError) as e:
        validate_slot(rao, T(MON, start), mins, [], now, TZ)
    assert e.value.code == code


def test_slot_ending_exactly_at_break_is_fine(rao, now):
    validate_slot(rao, T(MON, "10:40"), 20, [], now, TZ)


def test_rejects_past(rao):
    with pytest.raises(BookingError) as e:
        validate_slot(rao, T(MON, "09:00"), 10, [], T(MON, "09:30"), TZ)
    assert e.value.code == ErrorCode.IN_PAST


def test_rejects_leave(rao, now):
    rao.leave.add(MON)
    with pytest.raises(BookingError) as e:
        validate_slot(rao, T(MON, "10:00"), 10, [], now, TZ)
    assert e.value.code == ErrorCode.ON_LEAVE


def test_no_double_booking(rao, now):
    existing = [booking("a", "rao", MON, "10:00", 20)]
    for start in ("10:00", "09:50", "10:10"):
        with pytest.raises(BookingError) as e:
            validate_slot(rao, T(MON, start), 20, existing, now, TZ)
        assert e.value.code == ErrorCode.OVERLAP


def test_back_to_back_is_allowed(rao, now):
    existing = [booking("a", "rao", MON, "10:00", 20)]
    validate_slot(rao, T(MON, "10:20"), 20, existing, now, TZ)
    validate_slot(rao, T(MON, "09:40"), 20, existing, now, TZ)


def test_other_doctors_bookings_dont_block(rao, now):
    validate_slot(rao, T(MON, "10:00"), 20, [booking("a", "iyer", MON, "10:00", 20)], now, TZ)


def test_cancelled_and_no_show_free_the_slot(rao, now):
    existing = [booking("a", "rao", MON, "10:00", 20, Status.CANCELLED),
                booking("b", "rao", MON, "10:20", 20, Status.NO_SHOW)]
    validate_slot(rao, T(MON, "10:00"), 20, existing, now, TZ)
    validate_slot(rao, T(MON, "10:20"), 20, existing, now, TZ)


def test_live_hold_blocks_expired_hold_does_not(rao, now):
    live = booking("h1", "rao", MON, "10:00", 20, Status.HELD, hold_expires_at=T(SUN, "20:05"))
    with pytest.raises(BookingError):
        validate_slot(rao, T(MON, "10:00"), 20, [live], now, TZ)
    later = T(SUN, "20:06")
    validate_slot(rao, T(MON, "10:00"), 20, [live], later, TZ)


def test_daily_cap(rao, now):
    rao.daily_cap = 2
    existing = [booking("a", "rao", MON, "09:00", 10), booking("b", "rao", MON, "09:10", 10)]
    with pytest.raises(BookingError) as e:
        validate_slot(rao, T(MON, "17:00"), 10, existing, now, TZ)
    assert e.value.code == ErrorCode.DAILY_CAP_REACHED
    assert free_slots(rao, MON, 10, existing, now, TZ) == []


def test_daily_cap_ignores_cancelled(rao, now):
    rao.daily_cap = 1
    validate_slot(rao, T(MON, "10:00"), 10, [booking("a", "rao", MON, "09:00", 10, Status.CANCELLED)], now, TZ)


def test_exclude_self_when_rescheduling(rao, now):
    me = booking("me", "rao", MON, "10:00", 20)
    validate_slot(rao, T(MON, "10:10"), 20, [me], now, TZ, exclude_id="me")


def test_free_slots_grid_and_gaps(rao, now):
    existing = [booking("a", "rao", MON, "09:20", 20)]
    starts = [s.start.strftime("%H:%M") for s in free_slots(rao, MON, 20, existing, now, TZ)]
    assert starts[:3] == ["09:00", "09:40", "09:50"]   # 09:10/09:20/09:30 clash with 09:20–09:40
    assert "10:50" not in starts and "10:40" in starts  # 10:40–11:00 ends at the break
    assert "11:15" in starts                            # first start after the break
    assert starts[-1] == "19:40"                        # last 20-min slot of the evening


def test_free_slots_respect_lead_time(rao):
    starts = [s.start.strftime("%H:%M") for s in free_slots(rao, MON, 10, [], T(MON, "09:03"), TZ, lead_min=30)]
    assert starts[0] == "09:40"


def test_every_free_slot_validates(rao, now):
    existing = [booking("a", "rao", MON, "10:00", 20), booking("b", "rao", MON, "17:30", 10)]
    for s in free_slots(rao, MON, 20, existing, now, TZ):
        validate_slot(rao, s.start, 20, existing, now, TZ)
