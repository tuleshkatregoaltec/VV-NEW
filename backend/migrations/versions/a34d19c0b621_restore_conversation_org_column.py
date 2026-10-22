"""Restore the conversation column already required by the application.

Revision ID: a34d19c0b621
Revises: f2b8d0e4a6c1

Existing installations may already have this column from an out-of-band schema
update. Do not overwrite their ownership. A populated legacy table needs an
explicit historical ownership backfill, which cannot be inferred safely from a
user's current membership.
"""

from alembic import op
import sqlalchemy as sa

revision = "a34d19c0b621"
down_revision = "f2b8d0e4a6c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "organization_id" not in columns:
        if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM conversations)")).scalar():
            raise RuntimeError(
                "conversations.organization_id is missing on a populated legacy table. "
                "Restore the historical organization assignment before upgrading; "
                "current user membership is not a safe backfill."
            )
        op.add_column("conversations", sa.Column("organization_id", sa.String(), nullable=False))
    indexes = {index["name"] for index in sa.inspect(connection).get_indexes("conversations")}
    if "ix_conversations_organization_id" not in indexes:
        op.create_index("ix_conversations_organization_id", "conversations", ["organization_id"])


def downgrade() -> None:
    raise RuntimeError(
        "This repair cannot safely remove a column that may predate the revision. "
        "Restore a verified schema backup instead."
    )
