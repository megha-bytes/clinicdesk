"""Booking state changes: hold → confirm, cancel, reschedule, check-in, consultation start/end.

Each function validates against the hard rules and mutates the Booking it is given; persistence
is the caller's job. First come, first served: a held slot blocks everyone else until it expires.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, tzinfo

from app.engine.availability import validate_slot
from app.engine.queue import next_token_no
from app.engine.types import (
    AppointmentType, Booking, BookingError, Doctor, ErrorCode, Interval, Status, minutes,
)

DEFAULT_HOLD_MIN = 5


def check_duration(appt_type: AppointmentType, start: datetime, end: datetime) -> None:
    """Guard for tool calls: the model must not invent appointment lengths."""
    if (end - start) != minutes(appt_type.minutes):
        raise BookingError(ErrorCode.WRONG_DURATION,
                           f"A {appt_type.name} appointment is {appt_type.minutes} minutes.")


def hold(doctor: Doctor, start: datetime, appt_type: AppointmentType, bookings: Iterable[Booking],
         now: datetime, tz: tzinfo, patient_id: str | None = None,
         hold_min: int = DEFAULT_HOLD_MIN) -> Booking:
    slot = validate_slot(doctor, start, appt_type.minutes, bookings, now, tz)
    return Booking(
        id=uuid.uuid4().hex, doctor_id=doctor.id, start=slot.start, end=slot.end,
        status=Status.HELD, type_name=appt_type.name, patient_id=patient_id,
        hold_expires_at=now + minutes(hold_min), created_at=now,
    )


def confirm(booking: Booking, doctor: Doctor, bookings: Iterable[Booking], now: datetime, tz: tzinfo,
            token_style: bool = False) -> Booking:
    """Turn a live hold into a confirmed booking (only after the patient says yes)."""
    bookings = list(bookings)
    if booking.status != Status.HELD:
        raise BookingError(ErrorCode.INVALID_STATUS, f"Only a held slot can be confirmed (is {booking.status.value}).")
    if booking.hold_expires_at is None or booking.hold_expires_at <= now:
        raise BookingError(ErrorCode.HOLD_EXPIRED, "The hold expired; please pick a slot again.")
    # Re-check the rules (excluding this hold) in case anything changed while holding.
    validate_slot(doctor, booking.start, int((booking.end - booking.start).total_seconds() // 60),
                  bookings, now, tz, exclude_id=booking.id)
    booking.status = Status.CONFIRMED
    booking.hold_expires_at = None
    if token_style and booking.token_no is None:
        booking.token_no = next_token_no(bookings, doctor.id, booking.start.astimezone(tz).date(), tz)
    return booking


def cancel(booking: Booking) -> Interval:
    """Cancel a held or confirmed booking; returns the freed interval (for the waitlist)."""
    if booking.status not in (Status.HELD, Status.CONFIRMED, Status.CHECKED_IN) or booking.started_at:
        raise BookingError(ErrorCode.INVALID_STATUS, f"A {booking.status.value} appointment can't be cancelled.")
    booking.status = Status.CANCELLED
    booking.hold_expires_at = None
    return booking.interval


def reschedule(booking: Booking, doctor: Doctor, new_start: datetime, bookings: Iterable[Booking],
               now: datetime, tz: tzinfo) -> Interval:
    """Move a confirmed booking, keeping its length. Returns the freed (old) interval."""
    if booking.status != Status.CONFIRMED:
        raise BookingError(ErrorCode.INVALID_STATUS, f"A {booking.status.value} appointment can't be moved.")
    duration = int((booking.end - booking.start).total_seconds() // 60)
    old = booking.interval
    slot = validate_slot(doctor, new_start, duration, bookings, now, tz, exclude_id=booking.id)
    booking.doctor_id, booking.start, booking.end = doctor.id, slot.start, slot.end
    return old


def check_in(booking: Booking, now: datetime) -> Booking:
    if booking.status != Status.CONFIRMED:
        raise BookingError(ErrorCode.INVALID_STATUS, "Only a confirmed appointment can be checked in.")
    booking.status = Status.CHECKED_IN
    return booking


def start_consult(booking: Booking, now: datetime) -> Booking:
    if booking.status != Status.CHECKED_IN:
        raise BookingError(ErrorCode.INVALID_STATUS, "Check the patient in first.")
    booking.started_at = now
    return booking


def complete(booking: Booking, now: datetime) -> Booking:
    if booking.status != Status.CHECKED_IN or booking.started_at is None:
        raise BookingError(ErrorCode.INVALID_STATUS, "The consultation hasn't started.")
    booking.status = Status.COMPLETED
    booking.completed_at = now
    return booking


def mark_no_show(booking: Booking) -> Interval:
    if booking.status not in (Status.CONFIRMED, Status.CHECKED_IN) or booking.started_at:
        raise BookingError(ErrorCode.INVALID_STATUS, "Only a waiting appointment can be a no-show.")
    booking.status = Status.NO_SHOW
    return booking.interval
