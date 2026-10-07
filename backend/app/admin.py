"""Protected admin endpoints.

POST /admin/reset-demo   rebuild the synthetic demo clinic (used once after deploy, then nightly
                         by a scheduled GitHub Action so judges always see a clean, full clinic).

Disabled unless DEMO_RESET_TOKEN is set. Callers must send it in the X-Demo-Reset-Token header.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.seed import seed
from app.settings import get_settings

router = APIRouter(prefix="/admin", include_in_schema=False)


def _require_token(x_demo_reset_token: str = Header("")) -> None:
    expected = get_settings().secret("DEMO_RESET_TOKEN")
    if expected is None:
        raise HTTPException(404, "Not found")              # endpoint doesn't exist unless configured
    if not hmac.compare_digest(x_demo_reset_token.encode(), expected.get_secret_value().encode()):
        raise HTTPException(401, "Invalid token")


@router.post("/reset-demo", dependencies=[Depends(_require_token)])
def reset_demo(session: Session = Depends(get_session)) -> dict:
    return seed(session, reset=True)
