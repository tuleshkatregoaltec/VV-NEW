"""Dedicated loopback server running the unchanged authenticated FastAPI application."""

import logging
import os
from pathlib import Path


def create_app():
    # Evaluation process only: prevent any inherited remote telemetry configuration.
    for key in list(os.environ):
        if key.startswith(
            ("LANGSMITH_", "LANGCHAIN_", "OTEL_", "LOGFIRE_", "HONEYBADGER_", "POSTHOG_")
        ):
            os.environ.pop(key)
    from app.config import settings

    if settings.ENVIRONMENT != "local":
        raise RuntimeError("Live evaluation server requires ENVIRONMENT=local")
    settings.AI_TELEMETRY_ENABLED = False
    settings.LOGFIRE_TOKEN = None
    settings.HONEYBADGER_API_KEY = None
    settings.POSTHOG_API_KEY = None
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    from scripts.evaluation.tracing import (
        LocalExporter,
        TraceMiddleware,
        fingerprint,
        instrument_postgres,
        observe_operation,
        observe_query,
        private_directory,
        private_write,
    )

    directory = private_directory(Path(os.environ["VV_EVALUATION_DIRECTORY"]))
    secrets = tuple(
        str(value)
        for key, value in settings.model_dump().items()
        if value and any(word in key for word in ("KEY", "SECRET", "PASSWORD", "TOKEN"))
    )
    provider = TracerProvider(
        resource=Resource.create({"service.name": "vitevue-local-evaluation"})
    )
    provider.add_span_processor(
        SimpleSpanProcessor(LocalExporter(directory / "spans.jsonl", secrets))
    )
    tracer = provider.get_tracer("vitevue.evaluation")

    # Install client wrappers before domains import the query functions. No SQL changes.
    from app.clickhouse import client

    client.query = observe_query(client.query, tracer)
    client.query_df = observe_query(client.query_df, tracer)
    # Logfire owns FastAPI's internal spans on a separate provider. Capture them
    # through its public processor hook so native children never have missing parents.
    import logfire

    from app.main import app

    logfire.configure(
        send_to_logfire=False,
        console=False,
        additional_span_processors=[
            SimpleSpanProcessor(LocalExporter(directory / "spans.jsonl", secrets))
        ],
    )
    logging.disable(
        logging.CRITICAL
    )  # Avoid normal SQL logs writing bind values outside the private run.
    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=provider, include_content=True, include_binary_content=False
        )
    )
    from app.analytics import service as analytics
    from app.project import service as projects

    analytics.get_market_pulse = observe_operation(analytics.get_market_pulse, tracer)
    analytics.rank_areas = observe_operation(analytics.rank_areas, tracer)
    projects.get_similar_projects = observe_operation(projects.get_similar_projects, tracer)
    from app.postgres import get_postgres_engine

    instrument_postgres(get_postgres_engine(), tracer)
    from app.chat.agent import agent

    tools = {
        name: {"description": tool.description, "schema": tool.function_schema.json_schema}
        for name, tool in agent._function_toolset.tools.items()
    }
    private_write(
        directory / "runtime.json",
        {
            "configured_model": settings.DEFAULT_CHAT_MODEL,
            "provider": "openrouter",
            "agent": "app.chat.agent.agent",
            "agent_name": agent.name,
            "prompt_sha256": fingerprint(agent._system_prompts),
            "tools_sha256": fingerprint(tools),
            "tools": tools,
            "model_settings": {"openrouter_reasoning": {"effort": "low"}},
            "remote_telemetry": False,
        },
    )
    return TraceMiddleware(app, tracer)
