"""Optional content-free OpenTelemetry export; no domain or evaluation dependencies."""

import hashlib
import inspect
import json
import sys
from functools import wraps

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import NoOpTracerProvider, Status, StatusCode

_provider = NoOpTracerProvider()
_operations: set[str] = set()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def runtime_labels():
    """Trust registered code labels, never labels supplied in a model's text."""
    from pydantic_ai import Agent

    agents, tools = {}, set()
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("app.") and module is not None:
            for name, value in list(vars(module).items()):
                if isinstance(value, Agent):
                    static_prompts = list(value._system_prompts)
                    for runner in value._system_prompt_functions:
                        static_prompts.append(inspect.getsource(runner.function))
                    schemas = {
                        key: tool.function_schema.json_schema
                        for key, tool in value._function_toolset.tools.items()
                    }
                    agents[value.name or name] = {
                        "app.prompt.sha256": sha256(json.dumps(static_prompts, sort_keys=True)),
                        "app.tools.sha256": sha256(json.dumps(schemas, sort_keys=True)),
                    }
                    tools.update(value._function_toolset.tools)
    return agents, tools


def safe_span_fields(name, attributes, *, model, agents=(), tools=(), operations=None):
    """Construct an allowlisted payload from scratch; never copy events/resources."""
    attrs = {}
    for key in (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.details.reasoning_tokens",
        "app.query.row_count",
        "http.response.status_code",
    ):
        value = attributes.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1:
            attrs[key] = value
    for key in ("app.query.sha256", "app.prompt.sha256", "app.tools.sha256"):
        value = attributes.get(key)
        if (
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
        ):
            attrs[key] = value
    if attributes.get("db.system") in {"postgresql", "clickhouse"}:
        attrs["db.system"] = attributes["db.system"]
        return f"{attrs['db.system']}.query", attrs
    if "gen_ai.request.model" in attributes:
        for key in ("gen_ai.request.model", "gen_ai.response.model"):
            value = attributes.get(key)
            if value == model:
                attrs[key] = model
            elif isinstance(value, str):
                attrs[f"{key}.sha256"] = sha256(value)
        if attributes.get("gen_ai.system") == "openrouter":
            attrs["gen_ai.system"] = "openrouter"
        attrs["gen_ai.operation.name"] = "chat"
        return "model.request", attrs
    tool = attributes.get("gen_ai.tool.name")
    if tool in tools:
        attrs["gen_ai.tool.name"] = tool
        return f"tool.{tool}", attrs
    agent = attributes.get("gen_ai.agent.name")
    if agent in agents:
        attrs["gen_ai.agent.name"] = agent
        return f"agent.{agent}", attrs
    operation = attributes.get("app.operation")
    if operation in (_operations if operations is None else operations):
        attrs["app.operation"] = operation
        if attributes.get("app.result.success") is False:
            attrs["app.result.success"] = False
        return operation, attrs
    return "application.step", attrs


class AllowlistExporter(SpanExporter):
    def __init__(self, exporter, model, environment):
        self.exporter, self.model, self.environment = exporter, model, environment

    def export(self, spans):
        agents, tools = runtime_labels()
        clean = []
        for span in spans:
            name, attrs = safe_span_fields(
                span.name, span.attributes or {}, model=self.model, agents=agents, tools=tools
            )
            if attrs.get("gen_ai.agent.name") in agents:
                attrs.update(agents[attrs["gen_ai.agent.name"]])
            clean.append(
                ReadableSpan(
                    name=name,
                    context=span.context,
                    parent=span.parent,
                    resource=Resource(
                        {"service.name": "vitevue", "deployment.environment.name": self.environment}
                    ),
                    attributes=attrs,
                    events=(),
                    links=(),
                    kind=span.kind,
                    status=Status(span.status.status_code),
                    start_time=span.start_time,
                    end_time=span.end_time,
                )
            )
        return self.exporter.export(clean)

    def shutdown(self):
        return self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)


def configure(settings):
    global _provider
    if not settings.AI_TELEMETRY_ENABLED:
        return None
    if not settings.LANGSMITH_API_KEY:
        raise ValueError("AI_TELEMETRY_ENABLED requires LANGSMITH_API_KEY")
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    headers = {
        "x-api-key": settings.LANGSMITH_API_KEY,
        "Langsmith-Project": f"vitevue-{settings.ENVIRONMENT}",
    }
    if settings.LANGSMITH_WORKSPACE_ID:
        headers["X-Tenant-Id"] = settings.LANGSMITH_WORKSPACE_ID
    exporter = OTLPSpanExporter(
        endpoint=settings.LANGSMITH_ENDPOINT.rstrip("/") + "/otel/v1/traces", headers=headers
    )
    provider = TracerProvider(resource=Resource({"service.name": "vitevue"}))
    provider.add_span_processor(
        BatchSpanProcessor(
            AllowlistExporter(exporter, settings.DEFAULT_CHAT_MODEL, settings.ENVIRONMENT)
        )
    )
    _provider = provider
    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=provider, include_content=False, include_binary_content=False
        )
    )
    return provider


def tracer():
    return _provider.get_tracer("vitevue.observability")


def operation(function):
    name = f"{function.__module__}.{function.__name__}"
    _operations.add(name)

    @wraps(function)
    async def observed(*args, **kwargs):
        with tracer().start_as_current_span(
            name,
            attributes={"app.operation": name},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = await function(*args, **kwargs)
                if isinstance(result, dict) and result.get("success") is False:
                    span.set_attribute("app.result.success", False)
                    span.set_status(Status(StatusCode.ERROR))
                return result
            except BaseException:
                span.set_status(Status(StatusCode.ERROR))
                raise

    return observed


async def provider_request(client, url, **kwargs):
    """Observe an actual provider POST without capturing request/response content."""
    model = kwargs.get("json", {}).get("model", "")
    with tracer().start_as_current_span(
        "model.request",
        attributes={"gen_ai.request.model": model, "gen_ai.system": "openrouter"},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            response = await client.post(url, **kwargs)
            if response.is_error:
                span.set_status(Status(StatusCode.ERROR))
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    usage = payload.get("usage") or {}
                    if isinstance(usage, dict):
                        for source, target in (
                            ("prompt_tokens", "input_tokens"),
                            ("completion_tokens", "output_tokens"),
                        ):
                            value = usage.get(source)
                            if isinstance(value, int):
                                span.set_attribute(f"gen_ai.usage.{target}", value)
                    if isinstance(payload.get("model"), str):
                        span.set_attribute("gen_ai.response.model", payload["model"])
            except ValueError:
                span.set_status(Status(StatusCode.ERROR))
            return response
        except BaseException:
            span.set_status(Status(StatusCode.ERROR))
            raise


def warehouse_query(function):
    @wraps(function)
    async def observed(sql, parameters=None):
        with tracer().start_as_current_span(
            "clickhouse.query",
            attributes={"db.system": "clickhouse", "app.query.sha256": sha256(sql)},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = await function(sql, parameters)
                span.set_attribute("app.query.row_count", len(result))
                return result
            except BaseException:
                span.set_status(Status(StatusCode.ERROR))
                raise

    return observed


def instrument_postgres(engine):
    from sqlalchemy import event

    def before(conn, cursor, statement, parameters, context, executemany):
        context._observability_span = tracer().start_span(
            "postgresql.query",
            attributes={"db.system": "postgresql", "app.query.sha256": sha256(statement)},
        )

    def after(conn, cursor, statement, parameters, context, executemany):
        span = getattr(context, "_observability_span", None)
        if span:
            span.set_attribute("app.query.row_count", cursor.rowcount)
            span.end()

    def error(context):
        span = getattr(context.execution_context, "_observability_span", None)
        if span:
            span.set_status(Status(StatusCode.ERROR))
            span.end()

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(engine.sync_engine, "after_cursor_execute", after)
    event.listen(engine.sync_engine, "handle_error", error)


class Middleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        with tracer().start_as_current_span(
            "application.http", record_exception=False, set_status_on_exception=False
        ) as span:

            async def observed_send(message):
                if message["type"] == "http.response.start":
                    span.set_attribute("http.response.status_code", message["status"])
                    if message["status"] >= 500:
                        span.set_status(Status(StatusCode.ERROR))
                await send(message)

            try:
                await self.app(scope, receive, observed_send)
            except BaseException:
                span.set_status(Status(StatusCode.ERROR))
                raise
