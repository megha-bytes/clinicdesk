"""Staff login endpoints.

POST /auth/login      {email, password}     → {token, user}
POST /auth/demo       {role}                → {token, user}   (only with DEMO_LOGIN_ENABLED, demo clinic only)
POST /auth/logout                           → revokes the session
GET  /auth/me                               → the logged-in staff member and their permissions
POST /auth/ws-ticket                        → one-time ticket for WS /ops/ws (60 s)
GET  /auth/config                           → {demo_login: bool} for the login page
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import auth
from app import models as m
from app.db import SessionLocal
from app.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_LIMIT, LOGIN_WINDOW_S = 10, 60
_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def _rate(request: Request, bucket: str) -> None:
    key, now = f"{bucket}:{_ip(request)}", time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > LOGIN_WINDOW_S:
            q.popleft()
        if len(q) >= LOGIN_LIMIT:
            raise HTTPException(429, "Too many attempts. Wait a minute.")
        q.append(now)


class LoginIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class DemoIn(BaseModel):
    role: Literal["owner", "receptionist", "doctor"]


def _principal(user: m.StaffUser, token: str) -> dict:
    p = auth.principal_for(token)
    return {"token": token, "user": p.public()}


@router.get("/config")
def config() -> dict:
    return {"demo_login": get_settings().demo_login_enabled}


@router.post("/login")
def login(body: LoginIn, request: Request) -> dict:
    _rate(request, "login")
    with SessionLocal() as s:
        try:
            token, user = auth.login(s, body.email, body.password)
        except auth.AuthError as e:
            raise HTTPException(401, str(e)) from e
    return _principal(user, token)


@router.post("/demo")
def demo(body: DemoIn, request: Request) -> dict:
    if not get_settings().demo_login_enabled:
        raise HTTPException(404, "Not found")
    _rate(request, "demo")
    from app.chat_api import _demo_clinic_id
    with SessionLocal() as s:
        user = auth.ensure_demo_user(s, _demo_clinic_id(s), body.role)
        token = auth.start_session(s, user, hours=2)
        s.add(m.AuditLog(actor="staff", action="demo_login", entity="staff_user", entity_id=user.id))
        s.commit()
    return _principal(user, token)


@router.post("/logout")
def logout(authorization: str | None = Header(None)) -> dict:
    auth.revoke(auth._bearer(authorization))
    return {"ok": True}


@router.get("/me")
def me(user: auth.Principal = Depends(auth.current_staff)) -> dict:
    return user.public()


@router.post("/ws-ticket")
def ws_ticket(user: auth.Principal = Depends(auth.current_staff)) -> dict:
    return {"ticket": auth.issue_ticket(user)}
