"""Private local evidence capture. No network exporter is configured here."""

import hashlib
import inspect
import json
import os
import re
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Evidence directory must not be a symlink")
    path.chmod(0o700)
    return path


def private_write(path: Path, value) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, default=str, ensure_ascii=False)
        stream.write("\n")


def scrub_secrets(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            # Secrets can occur inside nested serialized tool messages. Redact
            # literal and JSON-escaped forms without corrupting the JSON envelope.
            forms = {secret}
            escaped = secret
            for _ in range(3):
                escaped = json.dumps(escaped)[1:-1]
                forms.add(escaped)
            for form in sorted(forms, key=len, reverse=True):
                text = text.replace(form, "[SECRET]")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~-]+", "Bearer [SECRET]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[TOKEN]", text)
    return text


class LocalExporter(SpanExporter):
    def __init__(self, path: Path, secrets: tuple[str, ...] = ()):
        self.path = path
        self.secrets = secrets
        self.lock = threading.Lock()

    def export(self, spans):
        with self.lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "a") as stream:
                for span in spans:
                    # Serialize then scrub, including exceptions/events/resources, not just inputs.
                    payload = json.loads(span.to_json())
                    line = json.dumps(payload, default=str)
                    stream.write(scrub_secrets(line, self.secrets) + "\n")
        return SpanExportResult.SUCCESS


def application_path() -> list[str]:
    """Capture executed callers, not guessed static reachability; never capture locals."""
    frames = []
    frame = inspect.currentframe()
    try:
        while frame:
            module = frame.f_globals.get("__name__", "")
            if module.startswith("app."):
                frames.append(f"{module}.{frame.f_code.co_name}")
            frame = frame.f_back
    finally:
        del frame
    return frames


@contextmanager
def safe_span(tracer, name, attributes=None):
    with tracer.start_as_current_span(name, attributes=attributes or {}) as span:
        yield span


def observe_operation(function, tracer):
    @wraps(function)
    async def observed(*args, **kwargs):
        with safe_span(
            tracer,
            f"{function.__module__}.{function.__name__}",
            {
                "app.operation": f"{function.__module__}.{function.__name__}",
            },
        ) as span:
            result = await function(*args, **kwargs)
            if isinstance(result, dict) and result.get("success") is False:
                span.set_attribute("app.result.success", False)
                span.set_status(Status(StatusCode.ERROR, "operation returned failure"))
            return result

    return observed


def observe_query(function, tracer):
    @wraps(function)
    async def observed(sql, parameters=None):
        with safe_span(
            tracer,
            "clickhouse.query",
            {
                "db.system": "clickhouse",
                "app.query.sha256": fingerprint(sql),
                "app.call_path": application_path(),
                "app.source.tables": sorted(
                    set(re.findall(r"\b(?:FROM|JOIN)\s+([a-zA-Z_]\w*)", sql, re.I))
                ),
                "db.statement": sql,
                "app.query.parameters": json.dumps(parameters or {}, default=str),
            },
        ) as span:
            result = await function(sql, parameters)
            records = result.to_dict(orient="records") if hasattr(result, "to_dict") else result
            span.set_attribute("app.query.row_count", len(records))
            span.set_attribute("app.query.result", json.dumps(records, default=str))
            return result

    return observed


def instrument_postgres(engine, tracer):
    from sqlalchemy import event

    def before(conn, cursor, statement, parameters, context, executemany):
        # Bind values can contain identities, tokens and saved messages. Omit entirely.
        context._evaluation_span = tracer.start_span(
            "postgresql.query",
            attributes={
                "db.system": "postgresql",
                "app.query.sha256": fingerprint(statement),
                "db.statement": statement,
                "app.call_path": application_path(),
            },
        )

    def after(conn, cursor, statement, parameters, context, executemany):
        span = getattr(context, "_evaluation_span", None)
        if span:
            span.set_attribute("app.query.row_count", cursor.rowcount)
            span.end()

    def error(context):
        span = getattr(context.execution_context, "_evaluation_span", None)
        if span:
            span.set_status(Status(StatusCode.ERROR, type(context.original_exception).__name__))
            span.end()

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(engine.sync_engine, "after_cursor_execute", after)
    event.listen(engine.sync_engine, "handle_error", error)


class TraceMiddleware:
    """One span across the actual ASGI response body, including the streaming lifecycle."""

    def __init__(self, app, tracer):
        self.app, self.tracer = app, tracer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Do not capture paths with conversation IDs, headers, request bodies or auth context.
        with safe_span(
            self.tracer, "application.http", {"http.request.method": scope["method"]}
        ) as span:

            async def traced_send(message):
                if message["type"] == "http.response.start":
                    span.set_attribute("http.response.status_code", message["status"])
                    message = {
                        **message,
                        "headers": [
                            *message.get("headers", []),
                            (
                                b"x-evaluation-trace-id",
                                f"{span.get_span_context().trace_id:032x}".encode(),
                            ),
                        ],
                    }
                await send(message)

            await self.app(scope, receive, traced_send)
