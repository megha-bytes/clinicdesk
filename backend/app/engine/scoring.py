"""Soft rules: rank valid slots so the agent offers the best 2–3 (plan §4).

score = + w_preference × match to requested doctor / time window
        − w_wait       × hours away from the requested time (or from now)
        − w_fragment   × unusable gaps the slot would leave in the doctor's day
        + w_continuity × same doctor as previous visits
        + w_language   × doctor speaks the patient's language
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, tzinfo

from app.engine.availability import occupying, working_intervals
from app.engine.types import Booking, Doctor, Interval


@dataclass
class Preferences:
    preferred_start: datetime | None = None          # "tomorrow 10:00"
    window: tuple[time, time] | None = None          # "morning" → (09:00, 12:00)
    preferred_doctor_id: str | None = None
    previous_doctor_id: str | None = None            # continuity for follow-ups
    language: str | None = None


@dataclass
class Weights:
    preference: float = 3.0
    wait: float = 1.0
    fragment: float = 0.5
    continuity: float = 1.0
    language: float = 1.0


@dataclass(order=True)
class RankedSlot:
    score: float
    doctor_id: str = field(compare=False)
    slot: Interval = field(compare=False)
    reasons: list[str] = field(default_factory=list, compare=False)


def _gaps_left(slot: Interval, doctor: Doctor, bookings: list[Booking], now: datetime, tz: tzinfo,
               min_useful_min: int) -> int:
    """Count gaps of 1..min_useful_min-1 minutes the slot would create next to it."""
    day = slot.start.astimezone(tz).date()
    window = next((w for w in working_intervals(doctor, day, tz) if w.contains(slot)), None)
    if window is None:
        return 0
    taken = sorted((b.interval for b in occupying(bookings, doctor.id, now)), key=lambda i: i.start)
    before_end = max([t.end for t in taken if t.end <= slot.start] + [window.start])
    after_start = min([t.start for t in taken if t.start >= slot.end] + [window.end])
    gaps = [(slot.start - before_end).total_seconds() / 60, (after_start - slot.end).total_seconds() / 60]
    return sum(1 for g in gaps if 0 < g < min_useful_min)


def _anchor(prefs: Preferences, now: datetime, tz: tzinfo) -> datetime:
    """The time we measure closeness from. An explicit window ("evening") beats a stray
    preferred time outside it: the anchor is moved to the nearest edge of the window."""
    anchor = prefs.preferred_start or now
    if prefs.window:
        local = anchor.astimezone(tz)
        lo, hi = prefs.window
        if local.time() < lo:
            anchor = local.replace(hour=lo.hour, minute=lo.minute, second=0, microsecond=0)
        elif local.time() > hi:
            anchor = local.replace(hour=hi.hour, minute=hi.minute, second=0, microsecond=0)
    return anchor


def score_slot(slot: Interval, doctor: Doctor, bookings: list[Booking], prefs: Preferences, now: datetime,
               tz: tzinfo, weights: Weights = Weights(), min_useful_min: int = 10) -> RankedSlot:
    score, reasons = 0.0, []
    local = slot.start.astimezone(tz)

    if prefs.window and prefs.window[0] <= local.time() and \
            slot.end.astimezone(tz).time() <= prefs.window[1]:
        score += weights.preference
        reasons.append("in requested time window")
    if prefs.preferred_doctor_id and prefs.preferred_doctor_id == doctor.id:
        score += weights.preference
        reasons.append("requested doctor")

    anchor = _anchor(prefs, now, tz)
    hours_away = abs((slot.start - anchor).total_seconds()) / 3600
    score -= weights.wait * hours_away

    gaps = _gaps_left(slot, doctor, bookings, now, tz, min_useful_min)
    if gaps:
        score -= weights.fragment * gaps
        reasons.append(f"leaves {gaps} short gap(s)")

    if prefs.previous_doctor_id and prefs.previous_doctor_id == doctor.id:
        score += weights.continuity
        reasons.append("same doctor as last visit")
    if prefs.language and prefs.language in doctor.languages:
        score += weights.language
        reasons.append(f"speaks {prefs.language}")

    return RankedSlot(score=round(score, 4), doctor_id=doctor.id, slot=slot, reasons=reasons)


def rank_slots(candidates: list[tuple[Doctor, Interval]], bookings: list[Booking], prefs: Preferences,
               now: datetime, tz: tzinfo, k: int = 3, weights: Weights = Weights(),
               spread_min: int = 30) -> list[RankedSlot]:
    """Top-k slots, best first. Picks are at least `spread_min` apart per doctor so the patient
    gets real choices (not 10:00, 10:10, 10:20). Ties break on the earlier time."""
    scored = [score_slot(s, d, bookings, prefs, now, tz, weights) for d, s in candidates]
    scored.sort(key=lambda r: (-r.score, r.slot.start, r.doctor_id))
    picked: list[RankedSlot] = []
    for r in scored:
        too_close = any(p.doctor_id == r.doctor_id and
                        abs((p.slot.start - r.slot.start).total_seconds()) < spread_min * 60
                        for p in picked)
        if not too_close:
            picked.append(r)
        if len(picked) == k:
            break
    return picked
