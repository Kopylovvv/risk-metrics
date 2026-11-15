"""Мосбиржа как источник рыночных данных и валюта расчётов в снимке

* enum provider: значение moex (cbonds и rudata остаются для сохранённых снимков)
* market_data_snapshots.settlement_currency: валюта расчётов по сделкам, у Мосбиржи
  CURRENCYID; может отличаться от валюты номинала

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE provider ADD VALUE IF NOT EXISTS 'moex' BEFORE 'cbonds'")
    op.add_column(
        "market_data_snapshots", sa.Column("settlement_currency", sa.CHAR(3), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("market_data_snapshots", "settlement_currency")
    # значение из enum PostgreSQL удалить нельзя: тип пересоздаётся без moex
    # (снимки Мосбиржи перед откатом нужно удалить, иначе приведение типа упадёт)
    op.execute("ALTER TYPE provider RENAME TO provider_old")
    op.execute("CREATE TYPE provider AS ENUM ('cbonds', 'rudata', 'test')")
    op.execute(
        "ALTER TABLE market_data_snapshots"
        " ALTER COLUMN provider TYPE provider USING provider::text::provider"
    )
    op.execute("DROP TYPE provider_old")
