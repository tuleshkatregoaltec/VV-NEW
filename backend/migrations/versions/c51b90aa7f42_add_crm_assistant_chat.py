"""Add isolated CRM assistant conversation storage.

Revision ID: c51b90aa7f42
Revises: c4d6f8b0e2a5
"""

from alembic import op
import sqlalchemy as sa

revision = "c51b90aa7f42"
down_revision = "c4d6f8b0e2a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_assistant_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_crm_assistant_conversations_organization_id",
        "crm_assistant_conversations",
        ["organization_id"],
    )
    op.create_index(
        "ix_crm_assistant_conversations_user_id",
        "crm_assistant_conversations",
        ["user_id"],
    )
    op.create_index(
        "ix_crm_assistant_conversation_owner",
        "crm_assistant_conversations",
        ["organization_id", "user_id"],
    )
    op.create_table(
        "crm_assistant_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["crm_assistant_conversations.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_crm_assistant_message_order",
        "crm_assistant_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("crm_assistant_messages")
    op.drop_table("crm_assistant_conversations")
