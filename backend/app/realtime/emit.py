"""Emit a live event from inside a workflow step (no-op outside a streamed run).

Payloads must be PII-free: ids, tool and node names, statuses, doctor names, times, costs.
Never patient names, phone numbers or message text.
"""
from __future__ import annotations

from typing import Any


def emit(event: str, /, **payload: Any) -> None:
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()({"event": event, "data": payload})
    except Exception:  # noqa: BLE001 - not inside a streamed graph run
        pass
