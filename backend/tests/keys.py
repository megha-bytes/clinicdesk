"""Test client that behaves like the patient app: it keeps each conversation's secret thread key and sends
it back on later calls (DEVLOG #48). Staff tests log in with `login_as`."""
from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient


class KeyedClient(TestClient):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.thread_keys: dict[str, str] = {}
        self.send_keys = True

    def send(self, request, **kw):
        tid = None
        m = re.search(r"/chat/([^/]+)/(pending|history|events)", request.url.path)
        if m:
            tid = m.group(1)
        elif request.content:
            try:
                tid = json.loads(request.content).get("thread_id")
            except (ValueError, AttributeError):
                tid = None
        if tid and self.send_keys and tid in self.thread_keys and "x-thread-key" not in request.headers:
            request.headers["X-Thread-Key"] = self.thread_keys[tid]
        resp = super().send(request, **kw)
        if resp.headers.get("x-thread-key") and resp.headers.get("x-thread-id"):
            self.thread_keys[resp.headers["x-thread-id"]] = resp.headers["x-thread-key"]
        elif not kw.get("stream") and resp.headers.get("content-type", "").startswith("application/json"):
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get("thread_key"):
                    self.thread_keys[body["thread_id"]] = body["thread_key"]
            except ValueError:
                pass
        return resp


def login_as(client: TestClient, session, clinic_id: str, role: str = "owner", *, demo: bool = False) -> dict:
    """Create a staff account for `role` and put its bearer token on the client. Returns the auth headers."""
    from sqlalchemy import select

    from app import auth
    from app import models as m
    doctor_id = None
    if role == "doctor":
        doctor_id = session.scalar(select(m.Doctor.id).where(m.Doctor.clinic_id == clinic_id, m.Doctor.name.like("%Rao%")))
    user = auth.create_user(session, clinic_id=clinic_id, email=f"{role}{'-demo' if demo else ''}@test.clinic",
                            name=f"Test {role}", role=role, password=None if demo else "correct horse battery",
                            doctor_id=doctor_id, demo=demo)
    token = auth.start_session(session, user)
    session.commit()
    headers = {"Authorization": f"Bearer {token}"}
    client.headers.update(headers)
    return headers
