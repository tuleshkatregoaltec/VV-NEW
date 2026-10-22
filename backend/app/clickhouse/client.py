"""ClickHouse async client for FastAPI.

Global async client that releases GIL during I/O operations for better concurrency.
Uses server-side parameter binding ({param:Type}) for security.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, TypeVar
from uuid import uuid4

import clickhouse_connect
import pandas as pd
from clickhouse_connect.driver.asyncclient import AsyncClient

from app.config import settings
from app.core.observability import warehouse_query

logger = logging.getLogger(__name__)

# Global singleton async client
_client: Optional[AsyncClient] = None

T = TypeVar("T")

# Set by run_cancellable: (query-id prefix, time limit in seconds) for the queries it runs.
_query_scope: ContextVar[tuple[str, int] | None] = ContextVar(
    "clickhouse_query_scope", default=None
)


class ClientDisconnected(Exception):
    """The caller went away; its ClickHouse queries were stopped."""


def _scoped_settings() -> Dict[str, Any]:
    scope = _query_scope.get()
    if scope is None:
        return {}
    group, max_seconds = scope
    return {"query_id": f"{group}-{uuid4().hex}", "max_execution_time": max_seconds}


async def kill_query_group(group: str) -> None:
    """Stop every running query started under one run_cancellable call."""
    client = await get_client()
    await client.command(f"KILL QUERY WHERE startsWith(query_id, '{group}-') ASYNC")


async def run_cancellable(
    is_disconnected: Callable[[], Awaitable[bool]],
    work: Coroutine[Any, Any, T],
    *,
    max_seconds: int = 60,
    poll_seconds: float = 0.25,
) -> T:
    """Run `work`, limiting each ClickHouse query it starts and stopping them if the caller leaves.

    A request that is abandoned (the agent changed a filter) would otherwise keep a heavy query
    running in ClickHouse for minutes. Each query gets a shared id prefix and a time limit;
    when `is_disconnected()` turns true the queries are killed and ClientDisconnected raised.
    """
    group = uuid4().hex
    token = _query_scope.set((group, max_seconds))
    try:
        task = asyncio.ensure_future(work)  # The task copies the scope set above.
    finally:
        _query_scope.reset(token)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=poll_seconds)
            if not task.done() and await is_disconnected():
                raise ClientDisconnected
        return task.result()
    except BaseException:
        if not task.done():
            task.cancel()
            await kill_query_group(group)
        raise


async def get_client() -> AsyncClient:
    """Get or create global async ClickHouse client."""
    global _client
    if _client is None:
        logger.info(
            f"Initializing ClickHouse async client: {settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}"
        )
        _client = await clickhouse_connect.get_async_client(
            host=settings.CLICKHOUSE_HOST,
            port=settings.CLICKHOUSE_PORT,
            username=settings.CLICKHOUSE_USER,
            password=settings.CLICKHOUSE_PASSWORD,
            database=settings.CLICKHOUSE_DATABASE,
            secure=settings.CLICKHOUSE_SECURE,
            connect_timeout=10,
            send_receive_timeout=300,
            query_retries=2,
            compress=True,
        )
        logger.info("ClickHouse async client initialized")
    return _client


async def close_clickhouse() -> None:
    """Close global ClickHouse client and release connection pool resources."""
    global _client
    if _client is not None:
        logger.info("Closing ClickHouse client")
        await _client.close()
        _client = None


@warehouse_query
async def query(
    sql: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Execute ClickHouse query and return list of dicts.

    Uses parameter binding ({param:Type} syntax) for security.
    Always include FINAL when querying ReplacingMergeTree tables.
    """
    client = await get_client()
    result = await client.query(sql, parameters=parameters or {}, settings=_scoped_settings())
    column_names = result.column_names
    return [dict(zip(column_names, row)) for row in result.result_rows]


@warehouse_query
async def query_df(
    sql: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Execute ClickHouse query and return pandas DataFrame.

    Useful for heavy aggregations. Always include FINAL for ReplacingMergeTree tables.
    """
    client = await get_client()
    return await client.query_df(sql, parameters=parameters or {})
