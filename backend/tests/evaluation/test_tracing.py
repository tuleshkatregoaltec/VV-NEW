import stat

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from scripts.evaluation.tracing import (
    LocalExporter,
    TraceMiddleware,
    observe_query,
    private_directory,
    private_write,
)


def test_private_evidence_permissions_and_secret_redaction_include_events(tmp_path):
    directory = private_directory(tmp_path / "private")
    private_write(directory / "case.json", {"answer": "local evidence"})
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "case.json").stat().st_mode) == 0o600
    provider = TracerProvider()
    exporter = LocalExporter(directory / "spans.jsonl", ("sensitive-api-key",))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("model") as span:
        span.set_attribute("tool_response", 'nested {"credential": "sensitive-api-key"}')
        span.record_exception(ValueError("sensitive-api-key Bearer abc.def.ghi"))
    content = (directory / "spans.jsonl").read_text()
    assert "sensitive-api-key" not in content
    assert "abc.def.ghi" not in content
    assert "[SECRET]" in content
    assert stat.S_IMODE((directory / "spans.jsonl").stat().st_mode) == 0o600


async def test_query_instrumentation_preserves_arguments_result_and_nested_parent():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    rows = [{"transaction_count": 0, "median_price": float("nan")}]
    calls = []

    async def query(sql, parameters):
        calls.append((sql, parameters))
        return rows

    with tracer.start_as_current_span("tool") as parent:
        result = await observe_query(query, tracer)(
            "SELECT * FROM ch_transactions", {"area": "JVC"}
        )
    assert result is rows
    assert calls == [("SELECT * FROM ch_transactions", {"area": "JVC"})]
    child = exporter.get_finished_spans()[0]
    assert child.parent.span_id == parent.get_span_context().span_id
    assert child.attributes["app.source.tables"] == ("ch_transactions",)
    assert "NaN" in child.attributes["app.query.result"]


async def test_stream_root_closes_after_final_chunk_and_does_not_capture_headers():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sent = []

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"data: first\n\n", "more_body": True})
        assert exporter.get_finished_spans() == ()
        await send({"type": "http.response.body", "body": b"data: done\n\n", "more_body": False})

    async def send(message):
        sent.append(message)

    await TraceMiddleware(application, provider.get_tracer("test"))(
        {"type": "http", "method": "POST", "headers": [(b"authorization", b"Bearer secret")]},
        None,
        send,
    )
    assert len(exporter.get_finished_spans()) == 1
    assert sent[1]["body"] == b"data: first\n\n"
    assert "secret" not in exporter.get_finished_spans()[0].to_json()
    assert len(dict(sent[0]["headers"])[b"x-evaluation-trace-id"]) == 32


def test_private_files_refuse_symlinks(tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        private_write(link, {"overwrite": True})
    assert target.read_text() == "preserve"


def test_redaction_handles_nested_json_escaped_credentials():
    import json

    from scripts.evaluation.tracing import scrub_secrets

    secret = 'key"with\\escape'
    nested = json.dumps({"message": json.dumps({"password": secret})})
    clean = json.loads(scrub_secrets(nested, (secret,)))
    assert json.loads(clean["message"])["password"] == "[SECRET]"
