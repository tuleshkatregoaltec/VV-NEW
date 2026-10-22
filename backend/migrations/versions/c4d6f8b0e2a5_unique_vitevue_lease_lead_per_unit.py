"""one shared Vitevue lease lead per unit per agency

Revision ID: c4d6f8b0e2a5
Revises: b3c5e7a9d1f4
Create Date: 2026-10-09 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d6f8b0e2a5"
down_revision: Union[str, Sequence[str], None] = "b3c5e7a9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_crm_inbound_vitevue_lease_unit",
        "crm_inbound_enquiries",
        ["organization_id", "source_lead_id"],
        unique=True,
        postgresql_where=sa.text("source = 'Vitevue lease lead'"),
    )


def downgrade() -> None:
    op.drop_index("uq_crm_inbound_vitevue_lease_unit", table_name="crm_inbound_enquiries")
