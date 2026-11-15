"""История расчётов и загруженные данные только добавляются, не переписываются

* market_data_snapshots, fx_rates, calculation_runs: UPDATE запрещён. Новый ответ источника
  ложится новой строкой (другая дата или источник), а не правкой старой
* calculation_results: менять можно только trade_id (ON DELETE SET NULL при удалении сделки),
  входы, подставленные параметры и ссылки на снимок и курс остаются как при расчёте

Удаление строк, на которые ссылается расчёт, уже запрещают внешние ключи

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FROZEN = ("market_data_snapshots", "fx_rates", "calculation_runs")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION forbid_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Строки % не изменяются: сохранённые данные расчёта', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION forbid_result_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF to_jsonb(NEW) - 'trade_id' IS DISTINCT FROM to_jsonb(OLD) - 'trade_id' THEN
                RAISE EXCEPTION 'Строка результата не изменяется, кроме trade_id'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    for table in _FROZEN:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION forbid_update()"
        )
    op.execute(
        "CREATE TRIGGER calculation_results_no_update BEFORE UPDATE ON calculation_results"
        " FOR EACH ROW EXECUTE FUNCTION forbid_result_update()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER calculation_results_no_update ON calculation_results")
    for table in _FROZEN:
        op.execute(f"DROP TRIGGER {table}_no_update ON {table}")
    op.execute("DROP FUNCTION forbid_result_update()")
    op.execute("DROP FUNCTION forbid_update()")
