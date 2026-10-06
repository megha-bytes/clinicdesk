"""OpenTelemetry setup + PII redaction before export (plan §5.3).

Every exported span passes through `RedactingSpanExporter`, which:
- drops credential-like attributes (authorization headers, api keys, tokens, cookies)
- replaces message bodies / prompts / completions with "[redacted len=N]"
- masks phone numbers and email addresses anywhere in string attributes and event attributes

Spans in the app are never mutated; redaction happens on a copy at export time, so
nothing un-redacted ever leaves the process.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

log = logging.getLogger(__name__)

# Keys whose values must never be exported.
_DROP_KEY = re.compile(r"(authorization|api[_-]?key|secret|password|token(?!s)|cookie|set-cookie)", re.I)
# Keys holding free text from patients or models: keep only the length.
_BODY_KEY = re.compile(
    r"(message|content|prompt|completion|transcript|body|gen_ai\.(input|output)\.messages|\.text$)", re.I
)
# Token counts and similar numeric GenAI attributes must survive.
_KEEP_KEY = re.compile(r"(usage|tokens|count|length|len|model|tier|status|code|id$)", re.I)

_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def mask_text(value: str) -> str:
    value = _PHONE.sub("[phone]", value)
    return _EMAIL.sub("[email]", value)


def redact_attributes(attrs: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        if _DROP_KEY.search(key) and not _KEEP_KEY.search(key):
            continue
        if _BODY_KEY.search(key) and not _KEEP_KEY.search(key):
            size = len(value) if isinstance(value, (str, Sequence)) else 0
            out[key] = f"[redacted len={size}]"
            continue
        if isinstance(value, str):
            out[key] = mask_text(value)
        elif isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
            out[key] = tuple(mask_text(v) for v in value)
        else:
            out[key] = value
    return out


def redact_span(span: ReadableSpan) -> ReadableSpan:
    events = [
        Event(name=e.name, attributes=redact_attributes(e.attributes), timestamp=e.timestamp)
        for e in span.events
    ]
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=redact_attributes(span.attributes),
        events=events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingSpanExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.inner.export([redact_span(s) for s in spans])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


def setup_telemetry(
    service_name: str = "clinicdesk",
    otlp_endpoint: str | None = None,
    extra_exporters: Sequence[SpanExporter] = (),
) -> TracerProvider:
    """Configure the global tracer provider. Safe to call when no collector is running."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(RedactingSpanExporter(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)))
            )
        except Exception as e:  # noqa: BLE001
            log.warning("OTLP exporter disabled: %s", e)
    for exp in extra_exporters:
        provider.add_span_processor(BatchSpanProcessor(RedactingSpanExporter(exp)))
    trace.set_tracer_provider(provider)
    return provider


tracer = trace.get_tracer("clinicdesk")
