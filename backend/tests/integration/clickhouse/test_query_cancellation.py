"""Abandoned requests must not leave heavy queries running in ClickHouse.

An agent clicking through filters used to pile up minute-long queries until ClickHouse ran out
of memory; every query started under run_cancellable is time-limited and killed on disconnect.
"""

import asyncio

import pytest

from app.clickhouse.client import ClientDisconnected, query, run_cancellable

# About 30 seconds of work, one row per block so time limits and kills apply between rows.
SLOW = "SELECT sleepEachRow(1) FROM numbers(30) SETTINGS max_block_size = 1"


async def _running(warehouse, marker: str) -> int:
    result = await warehouse.query(
        "SELECT count() FROM system.processes "
        "WHERE query LIKE {m:String} AND query NOT LIKE '%system.processes%'",
        parameters={"m": f"%{marker}%"},
    )
    return result.result_rows[0][0]


async def test_a_disconnected_caller_stops_its_query(warehouse):
    disconnected = asyncio.Event()

    async def is_disconnected():
        return disconnected.is_set()

    marker = "cancel-on-disconnect-test"
    run = asyncio.create_task(run_cancellable(is_disconnected, query(f"{SLOW} -- {marker}")))
    await asyncio.sleep(1.5)
    assert await _running(warehouse, marker) == 1

    disconnected.set()
    with pytest.raises(ClientDisconnected):
        await run
    for _ in range(20):
        if await _running(warehouse, marker) == 0:
            break
        await asyncio.sleep(0.25)
    assert await _running(warehouse, marker) == 0


async def test_a_query_over_its_time_limit_fails_fast(warehouse):
    async def connected():
        return False

    with pytest.raises(Exception, match="(?i)timeout|TIMEOUT_EXCEEDED|time limit"):
        await run_cancellable(connected, query(SLOW), max_seconds=1)
