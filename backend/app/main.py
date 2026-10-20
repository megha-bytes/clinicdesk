"""ClinicDesk API entry point.

Run locally (from backend/):
    uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.admin import router as admin_router
from app.auth.routes import router as auth_router
from app.onboarding.routes import router as onboarding_router
from app.chat_api import router as chat_router
from app.realtime.routes import router as ops_router
from app.staff_api import router as staff_router
from app.payments.routes import router as payments_router
from app.settings import get_settings
from app.telemetry import setup_telemetry, tracer

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.otel_enabled and s.app_env != "test":
        setup_telemetry(s.otel_service_name, s.otel_exporter_otlp_endpoint)
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ping")
        except Exception:  # noqa: BLE001
            pass
    if s.app_env != "test":
        from app.guardrails import engine_name
        engine_name()                      # load NeMo Guardrails now, not on the first patient message
        try:                               # OWNER_EMAIL + OWNER_PASSWORD → the clinic owner's account
            from app.auth import ensure_owner_from_env
            from app.chat_api import _demo_clinic_id
            from app.db import SessionLocal
            with SessionLocal() as session:
                ensure_owner_from_env(session, _demo_clinic_id(session))
        except Exception:  # noqa: BLE001 - e.g. the demo clinic isn't seeded yet
            pass
    yield


app = FastAPI(title="ClinicDesk", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Thread-Key", "X-Thread-Id"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("X-Frame-Options", "DENY")
    path = request.url.path
    if path.startswith("/pay/"):                     # the mock payment page: its own inline styles and form
        h.setdefault("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
    elif path not in ("/docs", "/redoc", "/openapi.json"):
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(payments_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(ops_router)
app.include_router(staff_router)
app.include_router(onboarding_router)

try:                                     # A2A: /.well-known/agent-card.json and /a2a
    from app.a2a.server import mount_a2a
    mount_a2a(app)
except ImportError:                      # a2a-sdk not installed
    pass


@app.head("/", include_in_schema=False)
@app.get("/")
def root() -> dict:
    return {"name": "ClinicDesk API", "version": VERSION, "docs": "/docs", "health": "/health"}


@app.head("/ping", include_in_schema=False)
@app.get("/ping")
def ping() -> dict:
    """Liveness only, no database. Point Render's health check and UptimeRobot here, so the
    web service stays awake while Neon can still scale to zero (free plan: 100 CU-hours/month)."""
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    """Readiness: checks the database and config. Touches the DB, so don't poll it every few seconds."""
    s = get_settings()
    return {
        "status": "ok",
        "version": VERSION,
        "env": s.app_env,
        "db": "ok" if db.ping() else "down",
        "token_factory_key": "set" if s.token_factory_api_key else "missing",
        "guardrails": _guardrails_engine(),
    }


def _guardrails_engine() -> str:
    from app.guardrails import engine_name
    return "nemo-guardrails" if engine_name() == "nemo" else "direct"


@app.get("/debug/trace")
def debug_trace() -> dict:
    """Emits a test span so you can see it in Jaeger (Day 2 check)."""
    with tracer.start_as_current_span("debug.test_span") as span:
        span.set_attribute("clinicdesk.check", "day2")
        span.set_attribute("patient.phone", "9876543210")   # shows redaction working in Jaeger
        trace_id = format(span.get_span_context().trace_id, "032x")
    return {"trace_id": trace_id, "hint": "Open Jaeger at http://localhost:16686, service 'clinicdesk'"}
