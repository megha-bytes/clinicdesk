"""Deterministic scheduling engine. The model talks; the engine decides (plan §4)."""
from app.engine.availability import free_slots, validate_slot, working_intervals
from app.engine.operations import (
    cancel, check_duration, check_in, complete, confirm, hold, mark_no_show, reschedule, start_consult,
)
from app.engine.queue import current_pace, estimate_wait, next_token_no, queue_order
from app.engine.scoring import Preferences, RankedSlot, Weights, rank_slots, score_slot
from app.engine.types import (
    AppointmentType, Booking, BookingError, Doctor, ErrorCode, Interval, Status,
)
from app.engine.waitlist import WaitlistRequest, match_freed_slot

__all__ = [
    "AppointmentType", "Booking", "BookingError", "Doctor", "ErrorCode", "Interval", "Preferences",
    "RankedSlot", "Status", "WaitlistRequest", "Weights", "cancel", "check_duration", "check_in",
    "complete", "confirm", "current_pace", "estimate_wait", "free_slots", "hold", "mark_no_show",
    "match_freed_slot", "next_token_no", "queue_order", "rank_slots", "reschedule", "score_slot",
    "start_consult", "validate_slot", "working_intervals",
]
