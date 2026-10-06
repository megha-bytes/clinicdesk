from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.engine import AppointmentType, Booking, Doctor, Status

TZ = ZoneInfo("Asia/Kolkata")
MON = date(2026, 10, 12)          # a Monday
SUN = date(2026, 10, 11)


def T(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


NEW = AppointmentType("new", 20, 500)
FOLLOW_UP = AppointmentType("follow_up", 10, 300)


@pytest.fixture
def rao() -> Doctor:
    week = {d: [("09:00", "13:00"), ("17:00", "20:00")] for d in ("mon", "tue", "wed", "thu", "fri", "sat")}
    return Doctor(id="rao", name="Dr. Rao", specialty="General Physician", hours=week,
                  breaks=[("11:00", "11:15")], avg_consult_min=10, languages=["en", "hi", "kn"])


@pytest.fixture
def iyer() -> Doctor:
    return Doctor(id="iyer", name="Dr. Iyer", specialty="Dermatology",
                  hours={"mon": [("10:00", "12:00")]}, avg_consult_min=15, languages=["en", "ta"])


@pytest.fixture
def now() -> datetime:
    return T(SUN, "20:00")


def booking(id: str, doctor: str, day: date, start: str, mins: int, status=Status.CONFIRMED, **kw) -> Booking:
    s = T(day, start)
    return Booking(id=id, doctor_id=doctor, start=s, end=s + timedelta(minutes=mins), status=status, **kw)
