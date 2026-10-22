"""add CRM agent area preferences for renewal leads

Revision ID: b3c5e7a9d1f4
Revises: a34d19c0b621
Create Date: 2026-10-09 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "b3c5e7a9d1f4"
down_revision: Union[str, Sequence[str], None] = "a34d19c0b621"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crm_agent_area_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("organization_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("areas", sa.JSON(), nullable=False),
        sa.Column("forward_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            name="uq_crm_agent_area_preference_owner",
        ),
    )
    for column in ("user_id", "organization_id"):
        op.create_index(
            op.f(f"ix_crm_agent_area_preferences_{column}"),
            "crm_agent_area_preferences",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in ("organization_id", "user_id"):
        op.drop_index(
            op.f(f"ix_crm_agent_area_preferences_{column}"),
            table_name="crm_agent_area_preferences",
        )
    op.drop_table("crm_agent_area_preferences")
