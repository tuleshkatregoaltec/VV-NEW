"""Real ClickHouse execution against a database owned by this test only."""

import re
from pathlib import Path
from uuid import uuid4

import clickhouse_connect
import pytest

from app.clickhouse import client
from app.config import settings


@pytest.fixture
async def warehouse(monkeypatch):
    if settings.CLICKHOUSE_HOST not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("ClickHouse integration requires a local service")
    options = {
        "host": settings.CLICKHOUSE_HOST,
        "port": settings.CLICKHOUSE_PORT,
        "username": settings.CLICKHOUSE_USER,
        "password": settings.CLICKHOUSE_PASSWORD,
        "secure": settings.CLICKHOUSE_SECURE,
    }
    admin = await clickhouse_connect.get_async_client(**options)
    database = "vv_migration_test_" + uuid4().hex
    await admin.command(f"CREATE DATABASE {database}")
    isolated = await clickhouse_connect.get_async_client(**options, database=database)
    monkeypatch.setattr(client, "_client", isolated)
    try:
        yield isolated
    finally:
        await isolated.close()
        await admin.command(f"DROP DATABASE {database} SYNC")
        await admin.close()


SCHEMA = Path(__file__).parents[4] / "holocron/holocron/lib/sql/clickhouse_schema.sql"


@pytest.fixture
def install_serving(warehouse):
    """Create tables from the repository warehouse schema and the backend serving views.

    Uses the production DDL (CREATE plus later ALTER ... ADD COLUMN) instead of fixture DDL.
    """
    from scripts.clickhouse.create_compatibility_views import VIEW_DDL

    async def install(*, tables: tuple[str, ...], views: tuple[str, ...]):
        text = SCHEMA.read_text(encoding="utf-8")
        for table in tables:
            statements = re.findall(
                rf"^(?:CREATE TABLE IF NOT EXISTS|ALTER TABLE) {table}\b.*?;", text, re.S | re.M
            )
            assert statements and statements[0].startswith("CREATE"), table
            for statement in statements:
                await warehouse.command(statement.rstrip(";"))
        ddl = dict(VIEW_DDL)
        for view in views:
            await warehouse.command(ddl[view])

    return install
