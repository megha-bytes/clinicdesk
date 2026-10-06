from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.telemetry import RedactingSpanExporter, mask_text, redact_attributes


def test_mask_text_phones_and_emails():
    s = "Call 9876543210 or +91 98765 43210 or mail priya@example.com"
    out = mask_text(s)
    assert "9876543210" not in out and "98765 43210" not in out
    assert "priya@example.com" not in out
    assert out.count("[phone]") == 2 and "[email]" in out


def test_redact_drops_credentials_and_bodies_keeps_usage():
    attrs = {
        "http.request.header.authorization": "Bearer tf-123",
        "api_key": "tf-123",
        "gen_ai.prompt": "My name is Priya, phone 9876543210",
        "message.content": "hello",
        "gen_ai.usage.input_tokens": 42,
        "gen_ai.request.model": "nvidia/nemotron-nano",
        "patient.phone": "9876543210",
        "clinic.name": "Sunrise Clinic",
    }
    out = redact_attributes(attrs)
    assert "http.request.header.authorization" not in out
    assert "api_key" not in out
    assert out["gen_ai.prompt"].startswith("[redacted len=")
    assert out["message.content"] == "[redacted len=5]"
    assert out["gen_ai.usage.input_tokens"] == 42
    assert out["gen_ai.request.model"] == "nvidia/nemotron-nano"
    assert out["patient.phone"] == "[phone]"
    assert out["clinic.name"] == "Sunrise Clinic"


def test_exporter_redacts_spans_and_events():
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(memory)))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("chat.turn") as span:
        span.set_attribute("patient.phone", "9876543210")
        span.set_attribute("gen_ai.usage.output_tokens", 7)
        span.add_event("user_message", {"content": "chest pain, call me on 9876543210"})

    [exported] = memory.get_finished_spans()
    assert exported.attributes["patient.phone"] == "[phone]"
    assert exported.attributes["gen_ai.usage.output_tokens"] == 7
    assert exported.events[0].attributes["content"].startswith("[redacted len=")
    assert "9876543210" not in str(exported.to_json())
