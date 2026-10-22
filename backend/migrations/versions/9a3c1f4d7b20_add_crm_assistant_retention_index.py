"""Index CRM assistant conversation activity for retention cleanup.

Revision ID: 9a3c1f4d7b20
Revises: c51b90aa7f42
"""

from alembic import op

revision = "9a3c1f4d7b20"
down_revision = "c51b90aa7f42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_crm_assistant_conversation_updated_at",
        "crm_assistant_conversations",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crm_assistant_conversation_updated_at",
        table_name="crm_assistant_conversations",
    )
