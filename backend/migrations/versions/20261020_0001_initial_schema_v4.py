"""Начальная схема БД v4: 7 таблиц, enum-ы, связи и ограничения

Revision ID: 0001
Revises:
Create Date: 2026-10-20 09:26:25.531484+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # enum-типы создаются автоматически вместе с первой таблицей, где они используются
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rate_to_rub", sa.Numeric(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rate_to_rub > 0", name=op.f("ck_fx_rates_rate_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rates")),
        sa.UniqueConstraint(
            "currency", "rate_date", "source", name=op.f("uq_fx_rates_currency_rate_date_source")
        ),
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("instrument_type", sa.String(length=24), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("isin", name=op.f("uq_instruments_isin")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_key_hash", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_workspaces_revision_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("session_key_hash", name=op.f("uq_workspaces_session_key_hash")),
    )
    op.create_table(
        "calculation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("measure_code", sa.String(length=32), nullable=False),
        sa.Column("methodology_version", sa.String(length=32), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.Enum("success", "partial", "failed", name="run_status"), nullable=False
        ),
        sa.Column("total_value", sa.Numeric(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'success') = (total_value IS NOT NULL)",
            name=op.f("ck_calculation_runs_total_only_on_success"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_calculation_runs_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calculation_runs")),
    )
    op.create_index(
        op.f("ix_calculation_runs_workspace_id"), "calculation_runs", ["workspace_id"], unique=False
    )
    op.create_table(
        "market_data_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Enum("cbonds", "rudata", "test", name="provider"), nullable=False),
        sa.Column("data_as_of", sa.Date(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("nominal", sa.Numeric(), nullable=True),
        sa.Column("price_pct", sa.Numeric(), nullable=True),
        sa.Column("accrued_interest", sa.Numeric(), nullable=True),
        sa.Column("duration_mod", sa.Numeric(), nullable=True),
        sa.Column("convexity", sa.Numeric(), nullable=True),
        sa.Column(
            "coupon_type", sa.Enum("fixed", "floating", "zero", name="coupon_type"), nullable=False
        ),
        sa.Column("coupon_frequency", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "coupon_frequency > 0", name=op.f("ck_market_data_snapshots_coupon_frequency_positive")
        ),
        sa.CheckConstraint("nominal > 0", name=op.f("ck_market_data_snapshots_nominal_positive")),
        sa.CheckConstraint(
            "price_pct > 0", name=op.f("ck_market_data_snapshots_price_pct_positive")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_market_data_snapshots_instrument_id_instruments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_data_snapshots")),
        sa.UniqueConstraint("instrument_id", "data_as_of", "provider", name="uq_snapshot_per_day"),
    )
    op.create_table(
        "trades",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=True),
        sa.Column("isin", sa.String(length=12), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity <> 0", name=op.f("ck_trades_quantity_non_zero")),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], name=op.f("fk_trades_instrument_id_instruments")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_trades_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trades")),
    )
    op.create_index(op.f("ix_trades_instrument_id"), "trades", ["instrument_id"], unique=False)
    op.create_index(op.f("ix_trades_workspace_id"), "trades", ["workspace_id"], unique=False)
    op.create_table(
        "calculation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trade_id", sa.Uuid(), nullable=True),
        sa.Column("market_data_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("fx_rate_id", sa.Uuid(), nullable=True),
        sa.Column("input_isin", sa.String(length=12), nullable=False),
        sa.Column("input_quantity", sa.Numeric(precision=20, scale=0), nullable=False),
        sa.Column("nominal_used", sa.Numeric(), nullable=True),
        sa.Column("price_pct_used", sa.Numeric(), nullable=True),
        sa.Column("fx_rate_used", sa.Numeric(), nullable=True),
        sa.Column("current_fair_value", sa.Numeric(), nullable=True),
        sa.Column("duration_used", sa.Numeric(), nullable=True),
        sa.Column("convexity_used", sa.Numeric(), nullable=True),
        sa.Column("metric_value", sa.Numeric(), nullable=True),
        sa.Column("status", sa.Enum("success", "failed", name="row_status"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(status = 'success' AND error_code IS NULL"
            " AND current_fair_value IS NOT NULL AND metric_value IS NOT NULL)"
            " OR (status = 'failed' AND error_code IS NOT NULL AND metric_value IS NULL)",
            name=op.f("ck_calculation_results_status_matches_values"),
        ),
        sa.CheckConstraint(
            "input_quantity <> 0", name=op.f("ck_calculation_results_input_quantity_non_zero")
        ),
        sa.ForeignKeyConstraint(
            ["fx_rate_id"], ["fx_rates.id"], name=op.f("fk_calculation_results_fx_rate_id_fx_rates")
        ),
        sa.ForeignKeyConstraint(
            ["market_data_snapshot_id"],
            ["market_data_snapshots.id"],
            name=op.f("fk_calculation_results_market_data_snapshot_id_market_data_snapshots"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["calculation_runs.id"],
            name=op.f("fk_calculation_results_run_id_calculation_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["trade_id"],
            ["trades.id"],
            name=op.f("fk_calculation_results_trade_id_trades"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calculation_results")),
    )
    op.create_index(
        op.f("ix_calculation_results_market_data_snapshot_id"),
        "calculation_results",
        ["market_data_snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calculation_results_run_id"), "calculation_results", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_calculation_results_trade_id"), "calculation_results", ["trade_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_calculation_results_trade_id"), table_name="calculation_results")
    op.drop_index(op.f("ix_calculation_results_run_id"), table_name="calculation_results")
    op.drop_index(
        op.f("ix_calculation_results_market_data_snapshot_id"), table_name="calculation_results"
    )
    op.drop_table("calculation_results")
    op.drop_index(op.f("ix_trades_workspace_id"), table_name="trades")
    op.drop_index(op.f("ix_trades_instrument_id"), table_name="trades")
    op.drop_table("trades")
    op.drop_table("market_data_snapshots")
    op.drop_index(op.f("ix_calculation_runs_workspace_id"), table_name="calculation_runs")
    op.drop_table("calculation_runs")
    op.drop_table("workspaces")
    op.drop_table("instruments")
    op.drop_table("fx_rates")
    # enum-типы Alembic сам не удаляет
    for enum_name in ("row_status", "run_status", "coupon_type", "provider"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=False)
