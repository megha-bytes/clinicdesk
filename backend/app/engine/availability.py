"""Hard rules: when a doctor can be booked, and validation of a proposed slot."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, tzinfo

from app.engine.types import (
    WEEKDAYS, Booking, BookingError, Doctor, ErrorCode, Interval, Status, at, minutes,
)


def session_intervals(doctor: Doctor, day: date, tz: tzinfo) -> list[Interval]:
    """Doctor's sessions on a day (before removing breaks). Empty on leave or days off."""
    if day in doctor.leave:
        return []
    blocks = doctor.hours.get(WEEKDAYS[day.weekday()], [])
    return [Interval(at(day, a, tz), at(day, b, tz)) for a, b in blocks]


def break_intervals(doctor: Doctor, day: date, tz: tzinfo) -> list[Interval]:
    return [Interval(at(day, a, tz), at(day, b, tz)) for a, b in doctor.breaks]


def working_intervals(doctor: Doctor, day: date, tz: tzinfo) -> list[Interval]:
    """Sessions minus breaks."""
    result: list[Interval] = []
    for session in session_intervals(doctor, day, tz):
        pieces = [session]
        for brk in break_intervals(doctor, day, tz):
            next_pieces = []
            for p in pieces:
                if not p.overlaps(brk):
                    next_pieces.append(p)
                    continue
                if p.start < brk.start:
                    next_pieces.append(Interval(p.start, brk.start))
                if brk.end < p.end:
                    next_pieces.append(Interval(brk.end, p.end))
            pieces = next_pieces
        result.extend(pieces)
    return sorted(result, key=lambda i: i.start)


def _same_local_day(b: Booking, day: date, tz: tzinfo) -> bool:
    return b.start.astimezone(tz).date() == day


def occupying(bookings: Iterable[Booking], doctor_id: str, now: datetime,
              exclude_id: str | None = None) -> list[Booking]:
    return [b for b in bookings
            if b.doctor_id == doctor_id and b.id != exclude_id and b.occupies(now)]


def booked_count(bookings: Iterable[Booking], doctor_id: str, day: date, tz: tzinfo, now: datetime,
                 exclude_id: str | None = None) -> int:
    """Bookings counting towards the daily cap: active ones plus completed visits that day."""
    return sum(
        1 for b in bookings
        if b.doctor_id == doctor_id and b.id != exclude_id and _same_local_day(b, day, tz)
        and (b.occupies(now) or b.status == Status.COMPLETED)
    )


def validate_slot(doctor: Doctor, start: datetime, duration_min: int, bookings: Iterable[Booking],
                  now: datetime, tz: tzinfo, exclude_id: str | None = None) -> Interval:
    """Raise BookingError if the slot breaks any hard rule; return the interval otherwise."""
    bookings = list(bookings)
    slot = Interval(start, start + minutes(duration_min))
    day = start.astimezone(tz).date()

    if start < now:
        raise BookingError(ErrorCode.IN_PAST, "That time has already passed.")
    if day in doctor.leave:
        raise BookingError(ErrorCode.ON_LEAVE, f"{doctor.name or 'The doctor'} is on leave that day.")
    sessions = session_intervals(doctor, day, tz)
    if not any(s.contains(slot) for s in sessions):
        raise BookingError(ErrorCode.OUTSIDE_HOURS, "That time is outside the doctor's hours.")
    if any(slot.overlaps(b) for b in break_intervals(doctor, day, tz)):
        raise BookingError(ErrorCode.ON_BREAK, "That time overlaps the doctor's break.")
    if any(slot.overlaps(b.interval) for b in occupying(bookings, doctor.id, now, exclude_id)):
        raise BookingError(ErrorCode.OVERLAP, "That time is already taken.")
    if doctor.daily_cap is not None and \
            booked_count(bookings, doctor.id, day, tz, now, exclude_id) >= doctor.daily_cap:
        raise BookingError(ErrorCode.DAILY_CAP_REACHED, "The doctor is fully booked that day.")
    return slot


def free_slots(doctor: Doctor, day: date, duration_min: int, bookings: Iterable[Booking],
               now: datetime, tz: tzinfo, step_min: int = 10, lead_min: int = 0) -> list[Interval]:
    """All valid start times on a grid (`step_min`), at least `lead_min` from now."""
    bookings = list(bookings)
    if doctor.daily_cap is not None and booked_count(bookings, doctor.id, day, tz, now) >= doctor.daily_cap:
        return []
    earliest = now + minutes(lead_min)
    taken = [b.interval for b in occupying(bookings, doctor.id, now)]
    slots: list[Interval] = []
    for window in working_intervals(doctor, day, tz):
        cursor = window.start
        while cursor + minutes(duration_min) <= window.end:
            cand = Interval(cursor, cursor + minutes(duration_min))
            if cursor >= earliest and not any(cand.overlaps(t) for t in taken):
                slots.append(cand)
            cursor += minutes(step_min)
    return slots
