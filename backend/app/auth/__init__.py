"""Staff authentication and authorization (Day 21).

- Passwords: scrypt (stdlib, memory-hard), per-user salt, constant-time compare.
- Sessions: random bearer tokens sent in the `Authorization` header; only their SHA-256 is stored, so a
  database leak doesn't leak sessions and any session can be revoked. 12 h absolute, 2 h idle by default.
  Header tokens (not cookies) because the app and the API live on different sites (`onrender.com` is a
  public suffix), where third-party cookies are blocked; no cookies also means no CSRF.
- Lockout: 5 failed logins → 15 minutes; the same error for an unknown email and a wrong password.
- Roles (always within the user's own clinic):
    owner         everything, incl. costs/model stats/X-ray, onboarding, staff accounts
    receptionist  whole schedule, check-in / no-show, alerts
    doctor        own column only, start / complete own visits, alerts
- Demo: with DEMO_LOGIN_ENABLED the public demo clinic offers one-click sessions per role (the rules ask for
  a demo without credentials). Demo sessions show shortened names and can't manage staff accounts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select

from app import models as m
from app.db import SessionLocal
from app.settings import get_settings

ROLES = ("owner", "receptionist", "doctor")
PERMS: dict[str, set[str]] = {
    "owner": {"schedule.all", "queue.checkin", "visit.run", "alerts", "stats", "xray", "onboarding", "users.manage"},
    "receptionist": {"schedule.all", "queue.checkin", "alerts"},
    "doctor": {"schedule.own", "visit.run", "alerts"},
}
DEMO_DENIED = {"users.manage"}
MAX_FAILED = 5
LOCK_MINUTES = 15
N, R, P = 2 ** 14, 8, 1


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Use at least 10 characters.")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


_DUMMY = None


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(16))
    return _DUMMY


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ the logged-in staff member
@dataclass(frozen=True)
class Principal:
    user_id: str
    clinic_id: str
    role: str
    name: str
    doctor_id: str | None
    demo: bool
    session_id: str

    def can(self, perm: str) -> bool:
        return perm in PERMS.get(self.role, set()) and not (self.demo and perm in DEMO_DENIED)

    def public(self) -> dict:
        return {"name": self.name, "role": self.role, "demo": self.demo, "doctor_id": self.doctor_id,
                "permissions": sorted(p for p in PERMS[self.role] if self.can(p))}


class AuthError(Exception):
    pass


def create_user(session, *, clinic_id: str, email: str, name: str, role: str, password: str | None,
                doctor_id: str | None = None, demo: bool = False) -> m.StaffUser:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if role == "doctor" and not doctor_id:
        raise ValueError("A doctor account needs the doctor it belongs to.")
    user = m.StaffUser(clinic_id=clinic_id, email=email.strip().lower(), name=name, role=role, doctor_id=doctor_id,
                       password_hash=hash_password(password) if password else "!" + secrets.token_hex(16),
                       active=True, demo=demo, failed_logins=0)
    session.add(user)
    session.flush()
    return user


def start_session(session, user: m.StaffUser, hours: float | None = None) -> str:
    st = get_settings()
    token = secrets.token_urlsafe(32)
    session.add(m.StaffSession(user_id=user.id, token_hash=token_hash(token), created_at=_now(), last_seen_at=_now(),
                               expires_at=_now() + timedelta(hours=hours or st.staff_session_hours), revoked=False))
    user.last_login_at = _now()
    return token


def login(session, email: str, password: str) -> tuple[str, m.StaffUser]:
    user = session.scalar(select(m.StaffUser).where(m.StaffUser.email == email.strip().lower()))
    if user is None or not user.active or user.demo or user.password_hash.startswith("!"):
        verify_password(password, _dummy_hash())             # same work either way: no account probing by timing
        raise AuthError("Wrong email or password.")
    if user.locked_until and _aware(user.locked_until) > _now():
        raise AuthError("Too many attempts. Try again in a few minutes.")
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = _now() + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        session.add(m.AuditLog(actor="staff", action="login_failed", entity="staff_user", entity_id=user.id))
        session.commit()
        raise AuthError("Wrong email or password.")
    user.failed_logins = 0
    user.locked_until = None
    token = start_session(session, user)
    session.add(m.AuditLog(actor="staff", action="login", entity="staff_user", entity_id=user.id))
    session.commit()
    return token, user


def principal_for(token: str) -> Principal:
    st = get_settings()
    with SessionLocal() as s:
        row = s.scalar(select(m.StaffSession).where(m.StaffSession.token_hash == token_hash(token)))
        now = _now()
        if (row is None or row.revoked or _aware(row.expires_at) <= now
                or _aware(row.last_seen_at) < now - timedelta(minutes=st.staff_idle_minutes)):
            raise AuthError("Please log in again.")
        user = s.get(m.StaffUser, row.user_id)
        if user is None or not user.active:
            raise AuthError("Please log in again.")
        if _aware(row.last_seen_at) < now - timedelta(seconds=60):     # don't write on every request
            row.last_seen_at = now
            s.commit()
        return Principal(user.id, user.clinic_id, user.role, user.name, user.doctor_id, user.demo, row.id)


def revoke(token: str) -> None:
    with SessionLocal() as s:
        row = s.scalar(select(m.StaffSession).where(m.StaffSession.token_hash == token_hash(token)))
        if row:
            row.revoked = True
            s.commit()


# ------------------------------------------------------------------ FastAPI dependencies
def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please log in.", headers={"WWW-Authenticate": "Bearer"})
    return authorization.split(" ", 1)[1].strip()


def current_staff(authorization: str | None = Header(None)) -> Principal:
    try:
        return principal_for(_bearer(authorization))
    except AuthError as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from e


def require(*perms: str):
    """Dependency: a logged-in staff member with at least one of `perms`."""
    def dep(user: Principal = Depends(current_staff)) -> Principal:
        if not any(user.can(p) for p in perms):
            raise HTTPException(403, "Your role can't do that.")
        return user
    return dep


# ------------------------------------------------------------------ one-time WebSocket tickets
_tickets: dict[str, tuple[Principal, float]] = {}
_tlock = Lock()


def issue_ticket(user: Principal, ttl_s: float = 60) -> str:
    t = secrets.token_urlsafe(24)
    with _tlock:
        now = time.monotonic()
        for k in [k for k, (_, exp) in _tickets.items() if exp < now]:
            _tickets.pop(k, None)
        _tickets[t] = (user, now + ttl_s)
    return t


def redeem_ticket(ticket: str | None) -> Principal | None:
    if not ticket:
        return None
    with _tlock:
        got = _tickets.pop(ticket, None)
    if not got or got[1] < time.monotonic():
        return None
    return got[0]


# ------------------------------------------------------------------ bootstrap accounts
DEMO_USERS = {
    "owner": ("demo-owner@clinicdesk.demo", "Demo owner"),
    "receptionist": ("demo-reception@clinicdesk.demo", "Demo receptionist"),
    "doctor": ("demo-doctor@clinicdesk.demo", "Dr. Ananya Rao (demo)"),
}


def ensure_demo_user(session, clinic_id: str, role: str) -> m.StaffUser:
    email, name = DEMO_USERS[role]
    user = session.scalar(select(m.StaffUser).where(m.StaffUser.email == email))
    if user and user.clinic_id != clinic_id:                    # the demo clinic was rebuilt
        session.delete(user)
        session.flush()
        user = None
    if user is None:
        doctor_id = None
        if role == "doctor":
            doctor_id = session.scalar(select(m.Doctor.id).where(m.Doctor.clinic_id == clinic_id)
                                       .order_by(m.Doctor.name))   # the first doctor (Dr. Ananya Rao in the seed)
            rao = session.scalar(select(m.Doctor.id).where(m.Doctor.clinic_id == clinic_id, m.Doctor.name.like("%Rao%")))
            doctor_id = rao or doctor_id
        user = create_user(session, clinic_id=clinic_id, email=email, name=name, role=role, password=None,
                           doctor_id=doctor_id, demo=True)
    return user


def ensure_owner_from_env(session, clinic_id: str) -> None:
    """OWNER_EMAIL + OWNER_PASSWORD (Render env) create the clinic owner account once."""
    st = get_settings()
    email, pw = st.secret("OWNER_EMAIL"), st.secret("OWNER_PASSWORD")
    if not email or not pw:
        return
    e = email.get_secret_value().strip().lower()
    if session.scalar(select(m.StaffUser).where(m.StaffUser.email == e)) is None:
        create_user(session, clinic_id=clinic_id, email=e, name="Clinic owner", role="owner", password=pw.get_secret_value())
        session.commit()
