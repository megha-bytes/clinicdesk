"""Waitlist: when a slot frees up, offer it to the earliest matching request (first come, first served)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.engine.types import Interval, minutes


@dataclass
class WaitlistRequest:
    id: str
    patient_id: str
    doctor_id: str
    duration_min: int
    window_start: datetime
    window_end: datetime
    created_at: datetime
    status: str = "waiting"   # waiting | offered | booked | expired


def match_freed_slot(requests: Iterable[WaitlistRequest], doctor_id: str, freed: Interval,
                     now: datetime) -> tuple[WaitlistRequest, Interval] | None:
    """Earliest waiting request that fits the freed time. Returns the request and the exact slot
    to offer (starting when the freed time starts, or when the request's window opens)."""
    for req in sorted(requests, key=lambda r: (r.created_at, r.id)):
        if req.status != "waiting" or req.doctor_id != doctor_id or req.window_end <= now:
            continue
        start = max(freed.start, req.window_start, now)
        end = start + minutes(req.duration_min)
        if end <= freed.end and end <= req.window_end:
            return req, Interval(start, end)
    return None
