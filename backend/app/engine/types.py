"""Plain data types for the scheduling engine.

The engine is pure Python with no database or model calls, so every booking decision is
deterministic and unit-tested. Tools (Day 5) convert DB rows to these types and back.
All datetimes are timezone-aware; doctor hours are local clock times in the clinic timezone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Status(str, Enum):
    HELD = "held"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


# Statuses that occupy a doctor's time (a hold only while it hasn't expired).
OCCUPYING = {Status.HELD, Status.CONFIRMED, Status.CHECKED_IN}
# Statuses that still need to be seen (count towards the queue and the daily cap).
WAITING = {Status.CONFIRMED, Status.CHECKED_IN}


class ErrorCode(str, Enum):
    IN_PAST = "IN_PAST"
    ON_LEAVE = "ON_LEAVE"
    OUTSIDE_HOURS = "OUTSIDE_HOURS"
    ON_BREAK = "ON_BREAK"
    OVERLAP = "OVERLAP"
    DAILY_CAP_REACHED = "DAILY_CAP_REACHED"
    WRONG_DURATION = "WRONG_DURATION"
    HOLD_EXPIRED = "HOLD_EXPIRED"
    INVALID_STATUS = "INVALID_STATUS"
    UNKNOWN_TYPE = "UNKNOWN_TYPE"


class BookingError(Exception):
    """Raised when a request breaks a hard rule. `code` is stable and safe to show the agent."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("interval end must be after start")

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: Interval) -> bool:
        return self.start <= other.start and other.end <= self.end


@dataclass
class AppointmentType:
    name: str            # new | follow_up | procedure:<name>
    minutes: int
    fee_inr: int = 0


@dataclass
class Doctor:
    id: str
    name: str = ""
    specialty: str = ""
    hours: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # {"mon": [("09:00","13:00")]}
    breaks: list[tuple[str, str]] = field(default_factory=list)            # daily, e.g. [("11:00","11:15")]
    leave: set[date] = field(default_factory=set)
    daily_cap: int | None = None
    avg_consult_min: float = 10.0
    languages: list[str] = field(default_factory=lambda: ["en"])


@dataclass
class Booking:
    id: str
    doctor_id: str
    start: datetime
    end: datetime
    status: Status = Status.CONFIRMED
    type_name: str = "new"
    patient_id: str | None = None
    hold_expires_at: datetime | None = None
    token_no: int | None = None
    urgent_by_staff: bool = False
    created_at: datetime | None = None
    started_at: datetime | None = None       # consultation began
    completed_at: datetime | None = None     # consultation ended

    @property
    def interval(self) -> Interval:
        return Interval(self.start, self.end)

    def occupies(self, now: datetime) -> bool:
        if self.status == Status.HELD:
            return self.hold_expires_at is not None and self.hold_expires_at > now
        return self.status in OCCUPYING


def parse_hhmm(value: str) -> time:
    return time.fromisoformat(value)


def at(day: date, hhmm: str | time, tz) -> datetime:
    t = parse_hhmm(hhmm) if isinstance(hhmm, str) else hhmm
    return datetime.combine(day, t, tzinfo=tz)


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)
