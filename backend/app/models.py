"""SQLAlchemy models (plan §6). Generic column types so they run on SQLite (tests) and Postgres.

Conventions
- Weekly hours are JSON: {"mon": [["09:00", "13:00"], ["17:00", "20:00"]], ...}
- Money is stored in whole rupees (int); the demo has no paise.
- Patient data is minimal by design (privacy guardrail §3.5).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------------ clinic setup
class Clinic(TimestampMixin, Base):
    __tablename__ = "clinic"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    address: Mapped[str | None] = mapped_column(Text)
    hours: Mapped[dict] = mapped_column(JSON, default=dict)
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    booking_style: Mapped[str] = mapped_column(String(10), default="timed")  # timed | token
    emergency_numbers: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"ambulance": "108", "emergency": "112", "mental_health": "14416"}
    )
    retention_days: Mapped[int] = mapped_column(Integer, default=30)

    doctors: Mapped[list[Doctor]] = relationship(back_populates="clinic", cascade="all, delete-orphan")
    appointment_types: Mapped[list[AppointmentType]] = relationship(
        back_populates="clinic", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("booking_style IN ('timed','token')", name="ck_clinic_booking_style"),)


class Doctor(TimestampMixin, Base):
    __tablename__ = "doctor"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    specialty: Mapped[str] = mapped_column(String(100))
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    hours: Mapped[dict] = mapped_column(JSON, default=dict)
    breaks: Mapped[list] = mapped_column(JSON, default=list)     # [["13:00","14:00"]]
    leave: Mapped[list] = mapped_column(JSON, default=list)      # ["2026-10-20", ...]
    daily_cap: Mapped[int | None] = mapped_column(Integer)
    avg_consult_min: Mapped[float] = mapped_column(Float, default=10.0)
    # None = use the clinic's style. Lets one doctor run timed slots and another a token queue.
    booking_style: Mapped[str | None] = mapped_column(String(10))

    clinic: Mapped[Clinic] = relationship(back_populates="doctors")


class AppointmentType(Base):
    __tablename__ = "appointment_type"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))   # new | follow_up | procedure:<name>
    minutes: Mapped[int] = mapped_column(Integer)
    fee_inr: Mapped[int] = mapped_column(Integer)

    clinic: Mapped[Clinic] = relationship(back_populates="appointment_types")
    __table_args__ = (
        UniqueConstraint("clinic_id", "name", name="uq_appt_type_name"),
        CheckConstraint("minutes > 0", name="ck_appt_type_minutes"),
        CheckConstraint("fee_inr >= 0", name="ck_appt_type_fee"),
    )


class RoutingRule(Base):
    """Clinic-defined need → doctor/specialty mapping. Never decided by the model (§3.1)."""
    __tablename__ = "routing_rule"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"), index=True)
    keyword: Mapped[str] = mapped_column(String(100))
    doctor_id: Mapped[str | None] = mapped_column(ForeignKey("doctor.id", ondelete="SET NULL"))
    specialty: Mapped[str | None] = mapped_column(String(100))
    # Higher wins when several rules match (e.g. "child" + "cough" → pediatrics). Set by the clinic.
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


# ------------------------------------------------------------------ patients & bookings
class Patient(TimestampMixin, Base):
    __tablename__ = "patient"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    age: Mapped[int | None] = mapped_column(Integer)
    phone: Mapped[str] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(10), default="en")
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("clinic_id", "phone", "name", name="uq_patient_identity"),)


APPOINTMENT_STATUSES = ("held", "confirmed", "checked_in", "completed", "cancelled", "no_show")
APPOINTMENT_SOURCES = ("web", "voice", "a2a", "staff")


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointment"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"))
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patient.id", ondelete="SET NULL"))
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctor.id", ondelete="CASCADE"))
    type_id: Mapped[str] = mapped_column(ForeignKey("appointment_type.id"))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    token_no: Mapped[int | None] = mapped_column(Integer)
    reason_short: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="held")
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    urgent_by_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(8), default="web")
    a2a_caller_id: Mapped[str | None] = mapped_column(ForeignKey("a2a_caller.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # consultation began
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # consultation ended

    __table_args__ = (
        Index("ix_appt_doctor_start", "doctor_id", "start"),
        CheckConstraint("\"end\" > start", name="ck_appt_time_order"),
        CheckConstraint(
            "status IN ('held','confirmed','checked_in','completed','cancelled','no_show')",
            name="ck_appt_status",
        ),
        CheckConstraint("source IN ('web','voice','a2a','staff')", name="ck_appt_source"),
    )


class Payment(TimestampMixin, Base):
    __tablename__ = "payment"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointment.id", ondelete="CASCADE"), index=True)
    amount_inr: Mapped[int] = mapped_column(Integer)
    link_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|paid|expired|refunded
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    provider_ref: Mapped[str | None] = mapped_column(String(100))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WaitlistEntry(TimestampMixin, Base):
    __tablename__ = "waitlist_entry"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id", ondelete="CASCADE"))
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctor.id", ondelete="CASCADE"), index=True)
    type_id: Mapped[str] = mapped_column(ForeignKey("appointment_type.id"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # waiting|offered|booked|expired


# ------------------------------------------------------------------ agent, safety, A2A, cost
class A2ACaller(TimestampMixin, Base):
    __tablename__ = "a2a_caller"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(100))
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    allowed_skills: Mapped[list] = mapped_column(JSON, default=list)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=20)
    daily_spend_cap_usd: Mapped[float] = mapped_column(Float, default=0.5)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversation"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patient.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(8), default="web")   # web | voice | a2a
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, default=_id)
    a2a_context_id: Mapped[str | None] = mapped_column(String(64), index=True)
    transcript_masked: Mapped[list] = mapped_column(JSON, default=list)
    handoff: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))


class GuardrailEvent(Base):
    __tablename__ = "guardrail_event"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), index=True)
    rail: Mapped[str] = mapped_column(String(50))
    type: Mapped[str] = mapped_column(String(20))  # emergency|medical_advice|injection|pii|output_block|handoff
    outcome: Mapped[str] = mapped_column(String(10), default="block")  # pass | block | escalate
    detail: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class LLMCall(Base):
    """Cost ledger: one row per model call, written by CostSpanProcessor."""
    __tablename__ = "llm_call"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversation.id", ondelete="SET NULL"), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    node: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(120))
    tier: Mapped[str] = mapped_column(String(8))     # nano | super | ultra | omni
    purpose: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class OnboardingDraft(TimestampMixin, Base):
    __tablename__ = "onboarding_draft"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    clinic_id: Mapped[str | None] = mapped_column(ForeignKey("clinic.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(10))   # url | name | photo
    source_value: Mapped[str] = mapped_column(Text)
    tavily_sources: Mapped[list] = mapped_column(JSON, default=list)
    extracted_json: Mapped[dict] = mapped_column(JSON, default=dict)
    review_notes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | approved | rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(100))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    actor: Mapped[str] = mapped_column(String(10))   # agent | staff | job | a2a
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(32))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
