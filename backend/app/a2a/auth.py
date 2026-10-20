"""API keys for calling agents: stored as SHA-256 hashes in a2a_caller, with per-caller rate limits."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from sqlalchemy import select

from app import models as m
from app.db import SessionLocal
from app.settings import get_settings

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "cdk_" + secrets.token_urlsafe(32)


def create_caller(name: str, key: str | None = None, rate_limit_per_min: int = 20) -> tuple[str, str]:
    """Register a calling agent. Returns (caller_id, key); the key is shown once and stored only as a hash."""
    key = key or new_key()
    with SessionLocal() as s:
        row = m.A2ACaller(name=name, api_key_hash=key_hash(key), rate_limit_per_min=rate_limit_per_min,
                          allowed_skills=["book_appointment", "check_availability", "change_appointment",
                                          "queue_status", "clinic_info"])
        s.add(row)
        s.commit()
        return row.id, key


def ensure_demo_caller() -> None:
    """If A2A_DEMO_KEY is set (Render env), make sure a 'Demo companion' caller exists with that key."""
    key = get_settings().secret("A2A_DEMO_KEY")
    if key is None:
        return
    h = key_hash(key.get_secret_value())
    with SessionLocal() as s:
        if s.scalar(select(m.A2ACaller.id).where(m.A2ACaller.api_key_hash == h)) is None:
            s.add(m.A2ACaller(name="Demo companion", api_key_hash=h, rate_limit_per_min=30))
            s.commit()


def authenticate(key: str | None) -> dict | None:
    """The active caller for this key, or None."""
    if not key:
        return None
    ensure_demo_caller()
    with SessionLocal() as s:
        row = s.scalar(select(m.A2ACaller).where(m.A2ACaller.api_key_hash == key_hash(key), m.A2ACaller.active.is_(True)))
        return None if row is None else {"id": row.id, "name": row.name, "rate_limit_per_min": row.rate_limit_per_min}


def rate_ok(caller_id: str, per_min: int) -> bool:
    now = time.monotonic()
    with _lock:
        q = _hits[caller_id]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_min:
            return False
        q.append(now)
        return True


def main() -> None:
    """    py -m app.a2a.auth create "Companion demo"    → prints a new key (store it in the calling agent)."""
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "create":
        from app.db import Base, engine
        Base.metadata.create_all(engine)
        cid, key = create_caller(" ".join(sys.argv[2:]))
        print(f"caller {cid}\nkey    {key}\n(shown once; stored only as a hash)")
    else:
        print(main.__doc__)


if __name__ == "__main__":
    main()
