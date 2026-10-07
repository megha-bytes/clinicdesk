"""Convert between database rows and engine types, and load what the engine needs.

The engine never touches the database; tools load rows here, call the engine, then write back.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import engine as eng
from app import models as m

UTC = timezone.utc


def clinic_tz(clinic: m.Clinic) -> ZoneInfo:
    return ZoneInfo(clinic.timezone or "Asia/Kolkata")


def booking_style(doctor: m.Doctor, clinic: m.Clinic) -> str:
    return doctor.booking_style or clinic.booking_style


def to_engine_doctor(row: m.Doctor) -> eng.Doctor:
    return eng.Doctor(
        id=row.id, name=row.name, specialty=row.specialty,
        hours={day: [tuple(b) for b in blocks] for day, blocks in (row.hours or {}).items()},
        breaks=[tuple(b) for b in (row.breaks or [])],
        leave={date.fromisoformat(d) for d in (row.leave or [])},
        daily_cap=row.daily_cap, avg_consult_min=row.avg_consult_min or 10.0,
        languages=list(row.languages or ["en"]),
    )


def to_engine_type(row: m.AppointmentType) -> eng.AppointmentType:
    return eng.AppointmentType(name=row.name, minutes=row.minutes, fee_inr=row.fee_inr)


def _aware(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC (that's how we store them).
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(tz)


def to_engine_booking(row: m.Appointment, tz: ZoneInfo, type_name: str = "new") -> eng.Booking:
    return eng.Booking(
        id=row.id, doctor_id=row.doctor_id, start=_aware(row.start, tz), end=_aware(row.end, tz),
        status=eng.Status(row.status), type_name=type_name, patient_id=row.patient_id,
        hold_expires_at=_aware(row.hold_expires_at, tz), token_no=row.token_no,
        urgent_by_staff=row.urgent_by_staff, created_at=_aware(row.created_at, tz),
        started_at=_aware(row.started_at, tz), completed_at=_aware(row.completed_at, tz),
    )


def utc(dt: datetime | None) -> datetime | None:
    """Store every timestamp in UTC. SQLite drops the offset, so local times would come back wrong."""
    return dt.astimezone(UTC) if dt is not None else None


def write_back(b: eng.Booking, row: m.Appointment) -> m.Appointment:
    row.doctor_id, row.start, row.end = b.doctor_id, utc(b.start), utc(b.end)
    row.status, row.hold_expires_at, row.token_no = b.status.value, utc(b.hold_expires_at), b.token_no
    row.urgent_by_staff, row.started_at, row.completed_at = b.urgent_by_staff, utc(b.started_at), utc(b.completed_at)
    return row


def new_row_from(b: eng.Booking, clinic_id: str, type_id: str, source: str) -> m.Appointment:
    row = m.Appointment(id=b.id, clinic_id=clinic_id, type_id=type_id, source=source,
                        patient_id=b.patient_id, created_at=utc(b.created_at))
    return write_back(b, row)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Local midnight to midnight, expressed in UTC (to match how rows are stored)."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    return utc(start), utc(start + timedelta(days=1))


def load_bookings(session: Session, doctor_ids: list[str], first_day: date, last_day: date,
                  tz: ZoneInfo) -> tuple[list[eng.Booking], dict[str, m.Appointment]]:
    """Engine bookings for these doctors between two local dates (inclusive), plus the rows by id."""
    lo, _ = day_bounds(first_day, tz)
    _, hi = day_bounds(last_day, tz)
    rows = session.scalars(
        select(m.Appointment).where(m.Appointment.doctor_id.in_(doctor_ids),
                                    m.Appointment.start >= lo, m.Appointment.start < hi)
    ).all()
    return [to_engine_booking(r, tz) for r in rows], {r.id: r for r in rows}
