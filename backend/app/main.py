"""ClinicDesk API entry point.

Run locally (from backend/):
    uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
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

            FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
        except Exception:  # noqa: BLE001
            pass
    yield


app = FastAPI(title="ClinicDesk", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """Liveness + readiness. Used by the host and by UptimeRobot (keeps free tiers awake)."""
    s = get_settings()
    return {
        "status": "ok",
        "version": VERSION,
        "env": s.app_env,
        "db": "ok" if db.ping() else "down",
        "token_factory_key": "set" if s.token_factory_api_key else "missing",
    }


@app.get("/debug/trace")
def debug_trace() -> dict:
    """Emits a test span so you can see it in Jaeger (Day 2 check)."""
    with tracer.start_as_current_span("debug.test_span") as span:
        span.set_attribute("clinicdesk.check", "day2")
        span.set_attribute("patient.phone", "9876543210")   # shows redaction working in Jaeger
        trace_id = format(span.get_span_context().trace_id, "032x")
    return {"trace_id": trace_id, "hint": "Open Jaeger at http://localhost:16686, service 'clinicdesk'"}
