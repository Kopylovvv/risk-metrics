"""Схема создаётся миграциями и совпадает с моделями; downgrade/upgrade работают"""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text

from app.db import Base
from tests.conftest import alembic_config, create_empty_database, drop_database

TABLES = {
    "workspaces",
    "instruments",
    "trades",
    "market_data_snapshots",
    "fx_rates",
    "calculation_runs",
    "calculation_results",
}
ENUMS = {
    "provider": ["cbonds", "rudata", "test"],
    "coupon_type": ["fixed", "floating", "zero"],
    "run_status": ["success", "partial", "failed"],
    "row_status": ["success", "failed"],
}


def test_migrations_create_full_schema(engine: Engine):
    insp = inspect(engine)
    assert set(insp.get_table_names()) == TABLES | {"alembic_version"}

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.typname, e.enumlabel FROM pg_type t"
                " JOIN pg_enum e ON e.enumtypid = t.oid ORDER BY t.typname, e.enumsortorder"
            )
        ).all()
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    enums: dict[str, list[str]] = {}
    for name, label in rows:
        enums.setdefault(name, []).append(label)
    assert enums == ENUMS

    head = ScriptDirectory.from_config(alembic_config(None)).get_current_head()
    assert version == head


def test_models_match_migrations(engine: Engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_key_constraints_exist(engine: Engine):
    insp = inspect(engine)
    uniques = {u["name"] for t in TABLES for u in insp.get_unique_constraints(t)}
    assert {
        "uq_snapshot_per_day",
        "uq_instruments_isin",
        "uq_workspaces_session_key_hash",
        "uq_fx_rates_currency_rate_date_source",
    } <= uniques

    fk = next(
        f
        for f in insp.get_foreign_keys("calculation_results")
        if f["constrained_columns"] == ["trade_id"]
    )
    assert fk["options"].get("ondelete") == "SET NULL"


def test_downgrade_to_empty_and_upgrade_again():
    url = create_empty_database("risk_metrics_migration_check")
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            command.upgrade(alembic_config(conn), "head")
        with eng.begin() as conn:
            command.downgrade(alembic_config(conn), "base")
        insp = inspect(eng)
        assert set(insp.get_table_names()) == {"alembic_version"}
        with eng.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM pg_enum")).scalar_one() == 0
        with eng.begin() as conn:
            command.upgrade(alembic_config(conn), "head")
        assert TABLES <= set(inspect(eng).get_table_names())
    finally:
        eng.dispose()
        drop_database("risk_metrics_migration_check")
