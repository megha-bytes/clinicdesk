"""Token queue and live wait estimates (plan §4, booking style "token").

Order is first come, first served by token number. The only exception is an appointment that
clinic *staff* mark urgent; payment never changes the order (there is no payment field here).

Wait estimate = time until the doctor's session starts (if not yet open)
              + remaining time of the consultation in progress
              + patients ahead × today's pace
Pace blends the doctor's usual consult length with today's observed consults, trusting today's
numbers more as more consultations finish.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

from app.engine.availability import session_intervals
from app.engine.types import WAITING, Booking, Doctor, Status

PACE_FULL_TRUST_AFTER = 5     # completed consults today before we fully trust today's pace
OVERRUN_REMAINING_MIN = 2.0   # if a consult runs over the expected pace, assume this much is left


def _on_day(b: Booking, doctor_id: str, day: date, tz: tzinfo) -> bool:
    return b.doctor_id == doctor_id and b.start.astimezone(tz).date() == day


def next_token_no(bookings: Iterable[Booking], doctor_id: str, day: date, tz: tzinfo) -> int:
    """Next token for this doctor and day. Numbers are never reused, even after cancellations."""
    used = [b.token_no for b in bookings if _on_day(b, doctor_id, day, tz) and b.token_no is not None]
    return max(used, default=0) + 1


def queue_order(bookings: Iterable[Booking], doctor_id: str, day: date, tz: tzinfo) -> list[Booking]:
    """Patients still to be seen (not in consultation), staff-urgent first, then by token."""
    waiting = [b for b in bookings if _on_day(b, doctor_id, day, tz)
               and b.status in WAITING and b.started_at is None]
    return sorted(waiting, key=lambda b: (not b.urgent_by_staff,
                                          b.token_no if b.token_no is not None else math.inf,
                                          b.start))


def in_consultation(bookings: Iterable[Booking], doctor_id: str, day: date, tz: tzinfo) -> Booking | None:
    current = [b for b in bookings if _on_day(b, doctor_id, day, tz)
               and b.status == Status.CHECKED_IN and b.started_at is not None]
    return max(current, key=lambda b: b.started_at, default=None)


def current_pace(doctor: Doctor, bookings: Iterable[Booking], day: date, tz: tzinfo) -> float:
    """Minutes per patient right now."""
    done = [b for b in bookings if _on_day(b, doctor.id, day, tz) and b.status == Status.COMPLETED
            and b.started_at and b.completed_at]
    if not done:
        return doctor.avg_consult_min
    observed = sum((b.completed_at - b.started_at).total_seconds() / 60 for b in done) / len(done)
    trust = min(len(done), PACE_FULL_TRUST_AFTER) / PACE_FULL_TRUST_AFTER
    return trust * observed + (1 - trust) * doctor.avg_consult_min


@dataclass
class WaitEstimate:
    token_no: int | None
    position: int              # 1 = next to be called
    patients_ahead: int
    now_serving: int | None    # token currently with the doctor
    minutes: int               # rounded up to the nearest 5 for patients
    pace_min: float


def estimate_wait(target: Booking, doctor: Doctor, bookings: Iterable[Booking], now: datetime,
                  tz: tzinfo, round_to: int = 5) -> WaitEstimate:
    bookings = list(bookings)
    day = target.start.astimezone(tz).date()
    pace = current_pace(doctor, bookings, day, tz)
    order = queue_order(bookings, doctor.id, day, tz)
    if target not in order:
        raise ValueError("booking is not waiting in today's queue")
    ahead = order.index(target)

    wait = 0.0
    sessions = [s for s in session_intervals(doctor, day, tz) if s.end > now]
    if sessions and sessions[0].start > now:
        wait += (sessions[0].start - now).total_seconds() / 60

    current = in_consultation(bookings, doctor.id, day, tz)
    if current is not None:
        elapsed = (now - current.started_at).total_seconds() / 60
        wait += max(pace - elapsed, OVERRUN_REMAINING_MIN)

    wait += ahead * pace
    rounded = int(math.ceil(wait / round_to) * round_to) if wait > 0 else 0
    return WaitEstimate(
        token_no=target.token_no, position=ahead + 1, patients_ahead=ahead,
        now_serving=current.token_no if current else None, minutes=rounded, pace_min=round(pace, 1),
    )
