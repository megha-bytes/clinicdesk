"""Staff dashboard data (Days 15–16). Live updates come over WS /ops/ws (app/realtime/routes.py).

GET  /ops/schedule?day=YYYY-MM-DD    doctors and appointments for a day (names shortened, no phone numbers)
GET  /ops/stats                      today's numbers: bookings, model cost and latency, guardrails, red-team
GET  /ops/alerts                     emergencies and handoffs in the last 24 h (no patient text)
GET  /ops/graph                      the real ClinicDeskGraph topology for the Agent X-ray
POST /ops/appointments/{id}/{action} staff actions: check-in | start | complete | no-show

Every route needs a staff login (Authorization: Bearer …, see app/auth) and is scoped to that staff
member's clinic. What each role may do is in app/auth.PERMS.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app import engine as eng
from app.engine.queue import in_consultation
from app import models as m
from app import repo
from app.auth import Principal, require
from app.db import SessionLocal
from app.realtime.events import EventFactory, EventType
from app.realtime.hub import hub, ops_channel

router = APIRouter(prefix="/ops", tags=["staff"])
REDTEAM_DIR = Path(__file__).resolve().parents[1] / "redteam"


def short_name(name: str | None) -> str:
    """'Asha Reddy' → 'Asha R.' (used for one-click demo sessions, which anyone can open)."""
    if not name:
        return "Walk-in"
    parts = name.split()
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


def _name(name: str | None, user: Principal) -> str:
    """Full names for real staff sessions; shortened ones for one-click demo sessions."""
    return short_name(name) if user.demo or not name else name


def _hhmm(dt: datetime, tz) -> str:
    return repo._aware(dt, tz).astimezone(tz).strftime("%H:%M")


@router.get("/schedule")
def schedule(day: date | None = None, user: Principal = Depends(require("schedule.all", "schedule.own"))) -> dict:
    own_only = not user.can("schedule.all")                  # doctors see their own column only
    with SessionLocal() as s:
        clinic = s.get(m.Clinic, user.clinic_id)
        tz = repo.clinic_tz(clinic)
        day = day or datetime.now(tz).date()
        dq = select(m.Doctor).where(m.Doctor.clinic_id == clinic.id)
        if own_only:
            dq = dq.where(m.Doctor.id == user.doctor_id)
        doctors = s.scalars(dq.order_by(m.Doctor.name)).all()
        lo, hi = repo.day_bounds(day, tz)
        aq = select(m.Appointment).where(
            m.Appointment.clinic_id == clinic.id, m.Appointment.start >= lo, m.Appointment.start < hi,
            m.Appointment.status.in_(("held", "confirmed", "checked_in", "completed", "no_show")))
        if own_only:
            aq = aq.where(m.Appointment.doctor_id == user.doctor_id)
        rows = s.scalars(aq.order_by(m.Appointment.start)).all()
        patients = {p.id: p for p in s.scalars(select(m.Patient).where(
            m.Patient.id.in_([r.patient_id for r in rows if r.patient_id]))).all()}
        types = {t.id: t for t in s.scalars(select(m.AppointmentType).where(
            m.AppointmentType.clinic_id == clinic.id)).all()}
        by_agent = set(s.scalars(select(m.AuditLog.entity_id).where(
            m.AuditLog.action == "book", m.AuditLog.actor.in_(("agent", "a2a")))).all())
        weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][day.weekday()]
        out_doctors = []
        for d in doctors:
            bookings, _ = repo.load_bookings(s, [d.id], day, day, tz)
            serving = in_consultation(bookings, d.id, day, tz)
            waiting = eng.queue_order(bookings, d.id, day, tz)
            out_doctors.append({
                "id": d.id, "name": d.name, "specialty": d.specialty, "booking_style": repo.booking_style(d, clinic),
                "hours": (d.hours or {}).get(weekday, []), "on_leave": day.isoformat() in (d.leave or []),
                "now_serving": serving.token_no if serving else None, "waiting": len(waiting)})
        appts = [{
            "id": r.id, "doctor_id": r.doctor_id, "start": _hhmm(r.start, tz), "end": _hhmm(r.end, tz),
            "status": r.status, "source": r.source, "token_no": r.token_no, "urgent": r.urgent_by_staff,
            "patient": _name(patients[r.patient_id].name if r.patient_id in patients else None, user),
            "type": types[r.type_id].name if r.type_id in types else None,
            "booked_by_agent": r.id in by_agent, "in_consult": bool(r.started_at and r.status == "checked_in"),
        } for r in rows]
    return {"day": day.isoformat(), "clinic": clinic.name, "doctors": out_doctors, "appointments": appts}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return round(values[k], 1)


def redteam_results() -> dict:
    out = {}
    for mode in ("scripted", "live"):
        f = REDTEAM_DIR / f"results-{mode}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out[mode] = {k: d.get(k) for k in ("run_at", "passed", "total", "rate", "by_category")}
            except (OSError, ValueError):
                pass
    return out


@router.get("/stats")
def stats(user: Principal = Depends(require("stats"))) -> dict:
    with SessionLocal() as s:
        clinic = s.get(m.Clinic, user.clinic_id)
        tz = repo.clinic_tz(clinic)
        lo, _ = repo.day_bounds(datetime.now(tz).date(), tz)
        calls = s.scalars(select(m.LLMCall).where(m.LLMCall.at >= lo)).all()
        bookings = s.scalar(select(func.count()).select_from(m.AuditLog).where(
            m.AuditLog.at >= lo, m.AuditLog.action == "book", m.AuditLog.actor.in_(("agent", "a2a")))) or 0
        convs = s.scalar(select(func.count()).select_from(m.Conversation).where(m.Conversation.created_at >= lo)) or 0
        rails = Counter(s.scalars(select(m.GuardrailEvent.type).where(m.GuardrailEvent.at >= lo)).all())
    by_tier: dict = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0, "latency": []})
    by_node: dict = defaultdict(list)
    for c in calls:
        t = by_tier[c.tier]
        t["calls"] += 1
        t["cost_usd"] += c.cost_usd or 0
        t["latency"].append(c.latency_ms or 0)
        by_node[c.node or "other"].append(c.latency_ms or 0)
    total_cost = sum(c.cost_usd or 0 for c in calls)
    return {
        "conversations": convs, "bookings": bookings, "model_calls": len(calls),
        "cost_usd": round(total_cost, 6), "cost_per_booking_usd": round(total_cost / bookings, 6) if bookings else None,
        "cost_per_conversation_usd": round(total_cost / convs, 6) if convs else None,
        "by_tier": {k: {"calls": v["calls"], "cost_usd": round(v["cost_usd"], 6), "p50_ms": _pct(v["latency"], .5)}
                    for k, v in by_tier.items()},
        "latency_by_node": {k: {"calls": len(v), "p50_ms": _pct(v, .5), "p95_ms": _pct(v, .95)} for k, v in by_node.items()},
        "guardrails": dict(rails), "redteam": redteam_results(),
        "median_turn_ms": round(statistics.median([c.latency_ms for c in calls]), 1) if calls else None,
    }


@router.get("/alerts")
def alerts(user: Principal = Depends(require("alerts"))) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with SessionLocal() as s:
        convs = select(m.Conversation.id).where(m.Conversation.clinic_id == user.clinic_id)
        rows = s.scalars(select(m.GuardrailEvent).where(
            m.GuardrailEvent.at >= since, m.GuardrailEvent.type.in_(("emergency", "handoff")),
            m.GuardrailEvent.conversation_id.in_(convs))
            .order_by(m.GuardrailEvent.at.desc()).limit(30)).all()
        return {"alerts": [{"id": r.id, "type": r.type, "rail": r.rail, "detail": (r.detail or "")[:120],
                            "conversation_id": r.conversation_id, "at": repo._aware(r.at, timezone.utc).isoformat()}
                           for r in rows]}


STATIC_GRAPH = {
    "nodes": ["__start__", "intent", "guard_in", "flow", "agent", "tools", "confirm", "execute", "handoff", "respond", "__end__"],
    "edges": [["__start__", "intent"], ["intent", "guard_in"], ["guard_in", "flow"], ["guard_in", "agent"],
              ["guard_in", "handoff"], ["guard_in", "respond"], ["flow", "confirm"], ["flow", "respond"],
              ["agent", "tools"], ["agent", "respond"], ["tools", "agent"], ["tools", "confirm"], ["tools", "respond"],
              ["confirm", "execute"], ["execute", "flow"], ["execute", "agent"], ["handoff", "respond"],
              ["respond", "__end__"]],
}


@router.get("/graph", dependencies=[Depends(require("xray"))])
def graph_topology() -> dict:
    """Nodes and edges straight from the compiled LangGraph (falls back to a static copy)."""
    try:
        from app.chat_api import _Runtime
        g = _Runtime.get().get_graph()
        return {"nodes": [n for n in g.nodes], "edges": [[e.source, e.target] for e in g.edges], "source": "langgraph"}
    except Exception:  # noqa: BLE001 - e.g. no model key configured
        return {**STATIC_GRAPH, "source": "static"}


OPS = {"check-in": eng.check_in, "start": eng.start_consult, "complete": eng.complete}


NEEDS = {"check-in": "queue.checkin", "no-show": "queue.checkin", "start": "visit.run", "complete": "visit.run"}


@router.post("/appointments/{appt_id}/{action}")
def staff_action(appt_id: str, action: Literal["check-in", "start", "complete", "no-show"],
                 user: Principal = Depends(require("queue.checkin", "visit.run"))) -> dict:
    if not user.can(NEEDS[action]):
        raise HTTPException(403, "Your role can't do that.")
    with SessionLocal() as s:
        row = s.get(m.Appointment, appt_id)
        if row is None or row.clinic_id != user.clinic_id:          # another clinic's booking: doesn't exist for you
            raise HTTPException(404, "Unknown appointment.")
        if user.role == "doctor" and row.doctor_id != user.doctor_id:
            raise HTTPException(403, "That's another doctor's patient.")
        clinic = s.get(m.Clinic, row.clinic_id)
        tz = repo.clinic_tz(clinic)
        b = repo.to_engine_booking(row, tz)
        now = datetime.now(timezone.utc)
        try:
            if action == "no-show":
                eng.mark_no_show(b)
            else:
                OPS[action](b, now)
        except eng.BookingError as e:
            raise HTTPException(409, e.message) from e
        before = row.status
        repo.write_back(b, row)
        s.add(m.AuditLog(actor="staff", action=action, entity="appointment", entity_id=row.id,
                         before={"status": before}, after={"status": row.status, "by": user.user_id}))
        s.commit()
        doctor = s.get(m.Doctor, row.doctor_id)
        payload = {"action": action, "appointment_id": row.id, "doctor": doctor.name, "status": row.status,
                   "token_no": row.token_no, "when": _hhmm(row.start, tz), "channel": "staff",
                   "date": repo._aware(row.start, tz).astimezone(tz).date().isoformat()}
        clinic_id = clinic.id
    ev = EventFactory("staff").make(EventType.BOOKING_CHANGED, None, **payload).model_dump(mode="json")
    hub.publish(ops_channel(clinic_id), ev)
    return payload
