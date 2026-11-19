"""Сравнение сохранённого расчёта с новым ответом источника

По каждой бумаге: сохранённые и новые данные, DV01 по тем и другим, плюс итог по составу
Ничего не записывает: сохранённые снимки, курсы и строки результата остаются как были
Как и расчёт, не держит транзакцию во время запросов к источникам
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import contracts
from app.db import CalculationResult, CalculationRun, Instrument
from app.enums import RowErrorCode, RowStatus
from app.schemas.source_diff import FxData, SnapshotData, SourceDiffOut, SourceDiffRow
from app.services.calculation import (
    BASE_CURRENCY,
    _ask,
    _fx_error,
    fx_source_name,
    get_calculation,
    market_source,
)
from app.services.market import latest_rates, latest_snapshots

# время получения отличается всегда, сравнивать его бессмысленно
_NOT_COMPARED = {"retrieved_at"}


async def compare_with_source(
    db: AsyncSession,
    run: CalculationRun,
    as_of: date,
    market: contracts.MarketDataProvider,
    fx: contracts.FxRateProvider,
    calc: contracts.RiskCalculator,
) -> SourceDiffOut:
    # 1. сохранённое: по одной строке результата на ISIN (у одного ISIN один снимок и курс)
    _, results = await get_calculation(db, run.id)
    saved: dict[str, CalculationResult] = {}
    by_isin: dict[str, list[CalculationResult]] = {}
    for r in results:
        saved.setdefault(r.input_isin, r)
        by_isin.setdefault(r.input_isin, []).append(r)
    instruments = {
        i.isin: i.id for i in await db.scalars(select(Instrument).where(Instrument.isin.in_(saved)))
    }
    await db.commit()

    # 2. источники, без транзакции
    snapshots: dict[str, SnapshotData] = {}
    snapshot_errors: dict[str, contracts.RowErrorCode] = {}
    for isin in saved:
        try:
            snap = await _ask(
                market.get_snapshot(isin, as_of),
                source=market_source(market),
                operation="snapshot",
                valid=lambda snap, isin=isin: snap.isin == isin and snap.data_as_of <= as_of,
                isin=isin,
            )
            snapshots[isin] = SnapshotData.model_validate(snap, from_attributes=True)
        except contracts.MarketDataError as exc:
            snapshot_errors[isin] = exc.code

    def currency_of(isin: str) -> str | None:
        snap = snapshots.get(isin) or saved[isin].snapshot
        return snap.currency if snap else None

    currencies = {currency_of(isin) for isin in saved} - {None, BASE_CURRENCY}
    rates: dict[str, FxData] = {}
    rate_errors: dict[str, contracts.RowErrorCode] = {}
    for currency in currencies:
        try:
            rate = await _ask(
                fx.get_rate_to_rub(currency, as_of),
                source=fx_source_name(fx),
                operation="fx_rate",
                valid=lambda rate, currency=currency: (
                    rate.currency == currency and rate.rate_date <= as_of and rate.rate_to_rub > 0
                ),
                currency=currency,
            )
            rates[currency] = FxData.model_validate(rate, from_attributes=True)
        except contracts.MarketDataError as exc:
            rate_errors[currency] = _fx_error(exc.code)

    # 3. источник не ответил: последнее сохранённое в БД на ту же дату
    ids = {instruments[i]: i for i in snapshot_errors if i in instruments}
    from_db = {
        ids[instrument_id]: SnapshotData.model_validate(snap, from_attributes=True)
        for instrument_id, snap in (await latest_snapshots(db, ids, as_of)).items()
    }
    rates_from_db = {
        cur: FxData.model_validate(rate, from_attributes=True)
        for cur, rate in (await latest_rates(db, set(rate_errors), as_of)).items()
    }
    await db.commit()

    rows = []
    for isin, result in saved.items():
        old = _snapshot(result)
        new, origin = _pick(isin, snapshots, from_db)
        old_fx = (
            FxData.model_validate(result.fx_rate, from_attributes=True) if result.fx_rate else None
        )
        currency = currency_of(isin)
        new_fx, fx_origin = _pick(currency, rates, rates_from_db)
        rows_of_isin = by_isin[isin]
        saved_dv01 = _saved_dv01(rows_of_isin)
        current_dv01, dv01_error = _current_dv01(
            rows_of_isin,
            new,
            new_fx,
            snapshot_error=snapshot_errors.get(isin),
            fx_error=rate_errors.get(currency),
            calc=calc,
        )
        rows.append(
            SourceDiffRow(
                isin=isin,
                saved=old,
                current=new,
                current_origin=origin,
                current_error=snapshot_errors.get(isin),
                saved_fx=old_fx,
                current_fx=new_fx,
                current_fx_origin=fx_origin,
                current_fx_error=rate_errors.get(currency),
                changed=_changed(old, new) + _changed(old_fx, new_fx, prefix="fx_"),
                quantity=sum(r.input_quantity for r in rows_of_isin),
                saved_dv01=saved_dv01,
                current_dv01=current_dv01,
                current_dv01_error=dv01_error,
                dv01_change=_minus(current_dv01, saved_dv01),
            )
        )
    current_total = (
        sum((r.current_dv01 for r in rows), Decimal(0))
        if rows and all(r.current_dv01 is not None for r in rows)
        else None
    )
    return SourceDiffOut(
        calculation_id=run.id,
        valuation_date=run.valuation_date,
        compared_as_of=as_of,
        saved_total=run.total_value,
        current_total=current_total,
        total_change=_minus(current_total, run.total_value),
        rows=rows,
    )


def _saved_dv01(rows: list[CalculationResult]) -> Decimal | None:
    """Сумма DV01 строк этого ISIN; если хоть одна с ошибкой, итога по бумаге нет"""
    if any(r.status != RowStatus.SUCCESS for r in rows):
        return None
    return sum((r.metric_value for r in rows), Decimal(0))


def _current_dv01(
    rows: list[CalculationResult],
    snap: SnapshotData | None,
    rate: FxData | None,
    *,
    snapshot_error: RowErrorCode | None,
    fx_error: RowErrorCode | None,
    calc: contracts.RiskCalculator,
) -> tuple[Decimal | None, RowErrorCode | None]:
    """DV01 того же количества по новым данным, той же формулой, что и расчёт"""
    if snap is None:
        return None, snapshot_error or RowErrorCode.MISSING_MARKET_DATA
    if snap.nominal is None or snap.price_pct is None or snap.accrued_interest is None:
        return None, RowErrorCode.MISSING_MARKET_DATA
    if snap.currency == BASE_CURRENCY:
        fx_rate = Decimal(1)
    elif rate is None:
        return None, fx_error or RowErrorCode.MISSING_FX_RATE
    else:
        fx_rate = rate.rate_to_rub
    total = Decimal(0)
    # по каждой сделке отдельно, как в расчёте: сумма количеств может оказаться нулём
    for r in rows:
        try:
            result = calc(
                contracts.Dv01Input(
                    quantity=r.input_quantity,
                    nominal=snap.nominal,
                    price_pct=snap.price_pct,
                    accrued_interest=snap.accrued_interest,
                    fx_rate=fx_rate,
                    duration_mod=snap.duration_mod,
                    convexity=snap.convexity,
                    coupon_type=snap.coupon_type,
                    coupon_frequency=snap.coupon_frequency,
                )
            )
        except contracts.CalculationInputError as exc:
            return None, exc.code
        total += result.dv01
    return total, None


def _minus(new: Decimal | None, old: Decimal | None) -> Decimal | None:
    return new - old if new is not None and old is not None else None


def _snapshot(result: CalculationResult) -> SnapshotData | None:
    if result.snapshot is None:
        return None
    return SnapshotData.model_validate(result.snapshot, from_attributes=True)


def _pick(key: str | None, fresh: dict, saved: dict) -> tuple:
    """Новый ответ источника, иначе последнее сохранённое в БД"""
    if key in fresh:
        return fresh[key], "source"
    if key in saved:
        return saved[key], "db"
    return None, None


def _changed(old: BaseModel | None, new: BaseModel | None, prefix: str = "") -> list[str]:
    """Имена отличающихся полей; если одной из сторон нет, сравнивать нечего"""
    if old is None or new is None:
        return []
    names = old.model_dump().keys() - _NOT_COMPARED
    return [
        prefix + name
        for name in type(old).model_fields
        if name in names and getattr(old, name) != getattr(new, name)
    ]
