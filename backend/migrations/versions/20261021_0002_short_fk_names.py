"""Короткие имена внешних ключей: fk_<таблица>_<колонка>

Старое правило добавляло имя связанной таблицы, и одно имя вышло длиннее 63 символов,
поэтому SQLAlchemy обрезал его с хэшем (..._market_d_3937). Схема не меняется, только имена

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (таблица, старое имя из 0001, новое имя)
RENAMES = [
    (
        "calculation_runs",
        "fk_calculation_runs_workspace_id_workspaces",
        "fk_calculation_runs_workspace_id",
    ),
    (
        "market_data_snapshots",
        "fk_market_data_snapshots_instrument_id_instruments",
        "fk_market_data_snapshots_instrument_id",
    ),
    ("trades", "fk_trades_instrument_id_instruments", "fk_trades_instrument_id"),
    ("trades", "fk_trades_workspace_id_workspaces", "fk_trades_workspace_id"),
    (
        "calculation_results",
        "fk_calculation_results_fx_rate_id_fx_rates",
        "fk_calculation_results_fx_rate_id",
    ),
    (
        "calculation_results",
        "fk_calculation_results_market_data_snapshot_id_market_d_3937",
        "fk_calculation_results_market_data_snapshot_id",
    ),
    (
        "calculation_results",
        "fk_calculation_results_run_id_calculation_runs",
        "fk_calculation_results_run_id",
    ),
    (
        "calculation_results",
        "fk_calculation_results_trade_id_trades",
        "fk_calculation_results_trade_id",
    ),
]


def upgrade() -> None:
    for table, old, new in RENAMES:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{old}" TO "{new}"')


def downgrade() -> None:
    for table, old, new in RENAMES:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{new}" TO "{old}"')
