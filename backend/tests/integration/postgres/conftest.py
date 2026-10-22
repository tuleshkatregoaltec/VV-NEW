"""Each run owns a fresh database; application commits are tested for real."""

import os
import subprocess
import uuid
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings


@pytest.fixture(scope="session")
def postgres_test_url():
    configured = os.environ.get("TEST_POSTGRES_URL", str(settings.POSTGRES_URL))
    url = make_url(configured)
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("PostgreSQL integration requires an explicitly local service")
    database = "vv_migration_test_" + uuid.uuid4().hex
    admin_url = url.set(drivername="postgresql", database="postgres")
    admin = psycopg2.connect(admin_url.render_as_string(hide_password=False))
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    target = url.set(drivername="postgresql+asyncpg", database=database).render_as_string(
        hide_password=False
    )
    try:
        subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=Path(__file__).parents[3],
            env={**os.environ, "POSTGRES_URL": target},
            check=True,
            capture_output=True,
            text=True,
        )
        yield target
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
        admin.close()


@pytest.fixture
async def pg_session(postgres_test_url):
    engine = create_async_engine(postgres_test_url)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
