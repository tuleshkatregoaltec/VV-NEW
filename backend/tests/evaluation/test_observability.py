import json
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import observability


def test_exporter_drops_content_in_attributes_names_events_resources_and_errors():
    target = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"secret": "RESOURCE_SECRET"}))
    provider.add_span_processor(
        SimpleSpanProcessor(observability.AllowlistExporter(target, "google/model", "local"))
    )
    with provider.get_tracer("SECRET_SCOPE").start_as_current_span("NAME_SECRET") as span:
        span.set_attribute("gen_ai.request.model", "google/model")
        span.set_attribute("gen_ai.usage.input_tokens", 42)
        span.set_attribute("gen_ai.input.messages", '{"password": "CONTENT_SECRET"}')
        span.set_attribute("tool_response", "ROW_SECRET")
        span.set_attribute("db.statement", "SQL_SECRET")
        span.set_attribute("http.request.header.authorization", "Bearer TOKEN_SECRET")
        span.set_attribute("gen_ai.response.model", "UNEXPECTED_SECRET_MODEL")
        span.record_exception(ValueError("ERROR_SECRET"))
    clean = target.get_finished_spans()[0]
    serialized = clean.to_json()
    for secret in (
        "RESOURCE_SECRET",
        "SECRET_SCOPE",
        "NAME_SECRET",
        "CONTENT_SECRET",
        "ROW_SECRET",
        "SQL_SECRET",
        "TOKEN_SECRET",
        "UNEXPECTED_SECRET_MODEL",
        "ERROR_SECRET",
    ):
        assert secret not in serialized
    assert clean.name == "model.request"
    assert clean.attributes["gen_ai.usage.input_tokens"] == 42
    assert clean.attributes["gen_ai.request.model"] == "google/model"
    assert clean.events == ()
    assert clean.instrumentation_scope is None


def test_disabled_telemetry_does_not_construct_any_exporter(monkeypatch):
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as exporters

    def reject(*args, **kwargs):
        raise AssertionError("disabled telemetry must not initialize remote exporter")

    monkeypatch.setattr(exporters, "OTLPSpanExporter", reject)
    assert observability.configure(SimpleNamespace(AI_TELEMETRY_ENABLED=False)) is None
    with pytest.raises(ValueError, match="requires"):
        observability.configure(SimpleNamespace(AI_TELEMETRY_ENABLED=True, LANGSMITH_API_KEY=None))


async def test_provider_observation_preserves_payload_and_status_without_content(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(observability, "_provider", provider)
    request_body = {
        "model": "google/model",
        "messages": [{"role": "user", "content": "PRIVATE_PROMPT"}],
    }
    seen = []

    async def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "google/model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
                "choices": [{"message": {"content": "PRIVATE_ANSWER"}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await observability.provider_request(
            client, "https://openrouter.ai/api/v1/chat/completions", json=request_body
        )
    assert seen == [request_body]
    assert response.json()["choices"][0]["message"]["content"] == "PRIVATE_ANSWER"
    span = exporter.get_finished_spans()[0]
    assert span.attributes["gen_ai.usage.output_tokens"] == 4
    assert "PRIVATE" not in span.to_json()


async def test_no_provider_span_when_crm_skips_model_for_empty_input(monkeypatch):
    from app.crm.ai import _ai_extract

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(observability, "_provider", provider)
    assert await _ai_extract("", {}) == ({}, 0.0, [])
    assert all(
        "gen_ai.request.model" not in span.attributes for span in exporter.get_finished_spans()
    )


def test_sanitized_spans_encode_as_real_otlp_without_raw_content():
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

    target = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(observability.AllowlistExporter(target, "google/model", "local"))
    )
    with provider.get_tracer("test").start_as_current_span("PRIVATE_NAME") as span:
        span.set_attribute("gen_ai.request.model", "google/model")
        span.set_attribute("db.statement", "PRIVATE_SQL")
    encoded = encode_spans(target.get_finished_spans()).SerializeToString()
    assert b"PRIVATE" not in encoded
    assert b"google/model" in encoded


async def test_native_agent_spans_keep_stable_identity_and_template_versions(monkeypatch):
    from pydantic_ai.models.instrumented import InstrumentationSettings
    from pydantic_ai.models.test import TestModel

    from app.chat.agent import agent

    target = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(observability.AllowlistExporter(target, "test", "local"))
    )
    monkeypatch.setattr(
        agent,
        "instrument",
        InstrumentationSettings(tracer_provider=provider, include_content=False),
    )
    with agent.override(model=TestModel(call_tools=[], custom_output_text="PRIVATE_RESPONSE")):
        await agent.run("PRIVATE_QUESTION")
    spans = target.get_finished_spans()
    root = next(span for span in spans if span.name == "agent.market_chat")
    assert len(root.attributes["app.prompt.sha256"]) == 64
    assert len(root.attributes["app.tools.sha256"]) == 64
    assert all("PRIVATE" not in span.to_json() for span in spans)
