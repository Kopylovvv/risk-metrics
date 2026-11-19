"""Признак запасных данных в строке результата

* calculation_results.market_data_fallback: источник рыночных данных не ответил, расчёт
  взял ранее сохранённый снимок
* calculation_results.fx_rate_fallback: то же для курса валюты

В строках, записанных до этой миграции, NULL: тогда признак не сохранялся
Колонки nullable без значения по умолчанию: таблица не переписывается, триггер 0005
(запрет UPDATE строк результата) не затрагивается

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calculation_results", sa.Column("market_data_fallback", sa.Boolean()))
    op.add_column("calculation_results", sa.Column("fx_rate_fallback", sa.Boolean()))


def downgrade() -> None:
    op.drop_column("calculation_results", "fx_rate_fallback")
    op.drop_column("calculation_results", "market_data_fallback")
