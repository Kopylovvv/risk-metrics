"""НКД входит в CFV: строка результата хранит подставленный НКД

Новая колонка calculation_results.accrued_interest_used. В расчётах dv01-v1
она остаётся NULL: тогда НКД в CFV не входил, их числа не меняются

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calculation_results", sa.Column("accrued_interest_used", sa.Numeric(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("calculation_results", "accrued_interest_used")
