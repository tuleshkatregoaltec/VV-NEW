import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations


def repair(connection):
    path = (
        Path(__file__).parents[3]
        / "migrations/versions/a34d19c0b621_restore_conversation_org_column.py"
    )
    spec = importlib.util.spec_from_file_location("conversation_repair", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    module.upgrade()


async def test_repair_refuses_to_guess_historical_tenant_ownership(pg_session):
    connection = await pg_session.connection()

    def verify(sync):
        sync.execute(sa.text("DROP TABLE conversations CASCADE"))
        sync.execute(
            sa.text("CREATE TABLE conversations (id integer PRIMARY KEY, user_id varchar NOT NULL)")
        )
        sync.execute(sa.text("INSERT INTO conversations VALUES (1, 'legacy-user')"))
        with pytest.raises(RuntimeError, match="historical organization assignment"):
            repair(sync)
        assert sync.execute(sa.text("SELECT user_id FROM conversations")).scalar() == "legacy-user"
        assert "organization_id" not in {
            column["name"] for column in sa.inspect(sync).get_columns("conversations")
        }

    try:
        await connection.run_sync(verify)
    finally:
        await pg_session.rollback()  # PostgreSQL restores all test-only DDL too.


async def test_repair_preserves_an_existing_organization_assignment(pg_session):
    connection = await pg_session.connection()

    def verify(sync):
        sync.execute(
            sa.text(
                "INSERT INTO conversations (user_id, organization_id, created_at, updated_at) VALUES ('legacy-user', 'historical-org', now(), now())"
            )
        )
        repair(sync)
        repair(sync)
        assert (
            sync.execute(
                sa.text("SELECT organization_id FROM conversations WHERE user_id='legacy-user'")
            ).scalar()
            == "historical-org"
        )

    try:
        await connection.run_sync(verify)
    finally:
        await pg_session.rollback()


async def test_runtime_models_match_the_migrated_schema(pg_session):
    """The migration chain is the schema authority; models must not describe another schema.

    create_all/model-contract checks cannot see drift such as INTEGER amounts that the
    migrations store as BIGINT, or migration-owned indexes the model omits.
    """
    from sqlmodel import SQLModel

    import app.postgres.models  # noqa: F401 - registers every managed table

    connection = await pg_session.connection()

    def diff(sync):
        context = MigrationContext.configure(
            sync,
            opts={
                "compare_type": True,
                "include_object": lambda obj, name, type_, reflected, compare_to: (
                    type_ != "table" or name in SQLModel.metadata.tables
                ),
            },
        )
        return compare_metadata(context, SQLModel.metadata)

    changes = await connection.run_sync(diff)
    # PostgreSQL rewrites expression-index text (casts, spacing), so an expression index
    # declared in both places shows up as remove+add of the same name. Columns and names of
    # those indexes are still compared; anything else is real drift.
    # ponytail: compared by name, not expression text; render via pg_get_indexdef if needed.
    by_name = {}
    for change in changes:
        if change[0] in {"add_index", "remove_index"}:
            by_name.setdefault(change[1].name, set()).add(change[0])
    rendering_only = {
        name for name, kinds in by_name.items() if kinds == {"add_index", "remove_index"}
    }
    assert rendering_only <= {
        "ix_crm_claim_owner_property_preference",
        "ix_crm_claim_list_owner_property_preference",
    }
    assert [
        change
        for change in changes
        if not (change[0] in {"add_index", "remove_index"} and change[1].name in rendering_only)
    ] == []
