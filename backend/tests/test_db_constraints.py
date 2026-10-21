"""Сохранение входов и результатов и обязательные ограничения модели"""

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db import (
    CalculationResult,
    CalculationRun,
    FxRate,
    Instrument,
    MarketDataSnapshot,
    Trade,
    Workspace,
)
from app.enums import CouponType, Provider, RowStatus, RunStatus

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
DAY = date(2026, 10, 20)


# --- фабрики ---------------------------------------------------------------


def make_workspace(s: Session) -> Workspace:
    ws = Workspace(session_key_hash=uuid.uuid4().hex)
    s.add(ws)
    s.flush()
    return ws


def make_instrument(s: Session, isin: str = "RU000A1038V6", currency: str = "RUB") -> Instrument:
    ins = Instrument(isin=isin, name="ОФЗ", instrument_type="bond", currency=currency)
    s.add(ins)
    s.flush()
    return ins


def make_snapshot(s: Session, ins: Instrument, **kw) -> MarketDataSnapshot:
    values = dict(
        instrument_id=ins.id,
        provider=Provider.CBONDS,
        data_as_of=DAY,
        retrieved_at=NOW,
        currency="RUB",
        nominal=Decimal("1000"),
        price_pct=Decimal("95"),
        accrued_interest=Decimal("12.5"),
        duration_mod=Decimal("3"),
        convexity=Decimal("12"),
        coupon_type=CouponType.FIXED,
        coupon_frequency=2,
    )
    values.update(kw)
    snap = MarketDataSnapshot(**values)
    s.add(snap)
    s.flush()
    return snap


def make_run(s: Session, ws: Workspace, status=RunStatus.SUCCESS, total=Decimal("1")):
    run = CalculationRun(
        workspace_id=ws.id,
        workspace_revision=ws.revision,
        measure_code="DV01",
        methodology_version="dv01-v1",
        valuation_date=DAY,
        status=status,
        total_value=total,
        completed_at=NOW,
    )
    s.add(run)
    s.flush()
    return run


def expect_violation(s: Session, obj=None, sql: str | None = None, params=None):
    """Нарушение ограничения отклоняется БД; сессия остаётся рабочей"""
    with pytest.raises((IntegrityError, DBAPIError)):
        with s.begin_nested():
            if obj is not None:
                s.add(obj)
                s.flush()
            else:
                s.execute(text(sql), params or {})


# --- сохранение ------------------------------------------------------------


def test_full_calculation_is_stored_exactly(session: Session):
    ws = make_workspace(session)
    ins = make_instrument(session)
    snap = make_snapshot(session, ins)
    trade = Trade(workspace_id=ws.id, instrument_id=ins.id, isin=ins.isin, quantity=-100)
    session.add(trade)
    session.flush()

    run = make_run(session, ws, total=Decimal("28.494300000"))
    session.add(
        CalculationResult(
            run_id=run.id,
            trade_id=trade.id,
            market_data_snapshot_id=snap.id,
            input_isin=trade.isin,
            input_quantity=trade.quantity,
            nominal_used=Decimal("1000"),
            price_pct_used=Decimal("95"),
            fx_rate_used=Decimal("1"),
            current_fair_value=Decimal("-95000"),
            duration_used=Decimal("3"),
            convexity_used=Decimal("12"),
            metric_value=Decimal("28.494300000"),
            status=RowStatus.SUCCESS,
        )
    )
    session.flush()
    session.expire_all()

    row = session.scalars(select(CalculationResult)).one()
    assert row.input_quantity == -100 and isinstance(row.input_quantity, int)
    assert row.metric_value == Decimal("28.494300000")  # NUMERIC без потери точности
    assert row.current_fair_value == Decimal("-95000")
    assert row.status is RowStatus.SUCCESS
    assert row.run.status is RunStatus.SUCCESS and row.run.total_value == Decimal("28.4943")

    stored = session.get(MarketDataSnapshot, snap.id)
    assert stored.provider is Provider.CBONDS and stored.coupon_type is CouponType.FIXED
    assert session.get(Workspace, ws.id).revision == 0  # значение по умолчанию из БД


def test_large_quantity_roundtrip(session: Session):
    ws = make_workspace(session)
    trade = Trade(workspace_id=ws.id, isin="RU000A1038V6", quantity=10**15)
    session.add(trade)
    session.flush()
    session.expire_all()
    assert session.get(Trade, trade.id).quantity == 10**15


def test_deleting_trade_keeps_result_history(session: Session):
    ws = make_workspace(session)
    trade = Trade(workspace_id=ws.id, isin="RU000A1038V6", quantity=50)
    session.add(trade)
    session.flush()
    run = make_run(session, ws, status=RunStatus.FAILED, total=None)
    result = CalculationResult(
        run_id=run.id,
        trade_id=trade.id,
        input_isin=trade.isin,
        input_quantity=trade.quantity,
        status=RowStatus.FAILED,
        error_code="MISSING_MARKET_DATA",
    )
    session.add(result)
    session.flush()

    session.execute(text("DELETE FROM trades WHERE id = :id"), {"id": trade.id})
    session.expire_all()
    kept = session.get(CalculationResult, result.id)
    assert kept.trade_id is None  # ON DELETE SET NULL
    assert (kept.input_isin, kept.input_quantity) == ("RU000A1038V6", 50)


# --- ограничения -----------------------------------------------------------


def test_trade_quantity_cannot_be_zero(session: Session):
    ws = make_workspace(session)
    expect_violation(session, Trade(workspace_id=ws.id, isin="RU000A1038V6", quantity=0))


def test_trade_requires_existing_workspace(session: Session):
    expect_violation(session, Trade(workspace_id=uuid.uuid4(), isin="RU000A1038V6", quantity=1))


def test_required_fields_not_null(session: Session):
    ws = make_workspace(session)
    expect_violation(session, Trade(workspace_id=ws.id, isin=None, quantity=1))
    expect_violation(session, Instrument(isin="RU000A105A95", instrument_type=None, currency="RUB"))


def test_unique_isin_session_and_fx(session: Session):
    make_instrument(session)
    expect_violation(
        session, Instrument(isin="RU000A1038V6", instrument_type="bond", currency="RUB")
    )

    ws = make_workspace(session)
    expect_violation(session, Workspace(session_key_hash=ws.session_key_hash))

    def fx(source="cbr"):
        return FxRate(
            currency="USD",
            rate_date=DAY,
            rate_to_rub=Decimal("92.5"),
            source=source,
            retrieved_at=NOW,
        )

    session.add(fx())
    session.flush()
    expect_violation(session, fx())
    session.add(fx(source="other"))  # другой источник допустим
    session.flush()


def test_one_snapshot_per_instrument_day_provider(session: Session):
    ins = make_instrument(session)
    make_snapshot(session, ins)
    expect_violation(
        session,
        MarketDataSnapshot(
            instrument_id=ins.id,
            provider=Provider.CBONDS,
            data_as_of=DAY,
            retrieved_at=NOW,
            currency="RUB",
            coupon_type=CouponType.FIXED,
        ),
    )
    make_snapshot(session, ins, provider=Provider.RUDATA)  # другой источник допустим
    make_snapshot(session, ins, data_as_of=date(2026, 10, 21))  # другой день допустим


def test_positive_market_values(session: Session):
    ins = make_instrument(session)
    for field, bad in (("nominal", 0), ("price_pct", -1), ("coupon_frequency", 0)):
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                make_snapshot(session, ins, **{field: bad})
    expect_violation(
        session,
        FxRate(currency="USD", rate_date=DAY, rate_to_rub=0, source="cbr", retrieved_at=NOW),
    )


def test_enums_reject_unknown_values(session: Session):
    ins = make_instrument(session)
    expect_violation(
        session,
        sql="INSERT INTO market_data_snapshots (id, instrument_id, provider, data_as_of,"
        " retrieved_at, currency, coupon_type) VALUES (gen_random_uuid(), :i, 'moex',"
        " :d, now(), 'RUB', 'fixed')",
        params={"i": ins.id, "d": DAY},
    )


def test_run_total_only_on_success(session: Session):
    ws = make_workspace(session)
    for status, total in (
        (RunStatus.SUCCESS, None),
        (RunStatus.PARTIAL, Decimal("1")),
        (RunStatus.FAILED, Decimal("0")),
    ):
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                make_run(session, ws, status=status, total=total)
    make_run(session, ws, status=RunStatus.PARTIAL, total=None)


def test_result_status_matches_values(session: Session):
    ws = make_workspace(session)
    run = make_run(session, ws, status=RunStatus.PARTIAL, total=None)

    def row(**kw):
        base = dict(run_id=run.id, input_isin="RU000A1038V6", input_quantity=1)
        base.update(kw)
        return CalculationResult(**base)

    # ошибка без кода
    expect_violation(session, row(status=RowStatus.FAILED))
    # ошибка, но подставлен ноль вместо NULL
    expect_violation(
        session, row(status=RowStatus.FAILED, error_code="MISSING_MARKET_DATA", metric_value=0)
    )
    # успех без значения
    expect_violation(session, row(status=RowStatus.SUCCESS, current_fair_value=Decimal("1")))
    # успех с кодом ошибки
    expect_violation(
        session,
        row(
            status=RowStatus.SUCCESS,
            error_code="X",
            current_fair_value=Decimal("1"),
            metric_value=Decimal("1"),
        ),
    )
    # нулевое количество во входах
    expect_violation(session, row(status=RowStatus.FAILED, error_code="X", input_quantity=0))


def test_application_connects_to_postgres(test_db_url):
    from app.db.session import SessionFactory, engine  # импорт после выбора тестовой базы

    async def ping() -> int:
        async with SessionFactory() as s:
            value = (await s.execute(text("SELECT 1"))).scalar_one()
        await engine.dispose()
        return value

    assert asyncio.run(ping()) == 1
