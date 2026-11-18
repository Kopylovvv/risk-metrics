"""Путь расчёта DV01: сделки набора → рыночные данные → расчётное ядро → сохранённый результат

Три шага, чтобы не держать транзакцию открытой во время запросов к внешним источникам:
1. короткое чтение: сделки, справочник, снимки и курсы, которые уже есть в БД
2. без транзакции: недостающие данные у источников (MarketDataProvider, FxRateProvider)
3. короткая запись: новые снимки и курсы, запуск расчёта и строки результата
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import contracts
from app.db import (
    CalculationResult,
    CalculationRun,
    FxRate,
    Instrument,
    MarketDataSnapshot,
    Trade,
    Workspace,
)
from app.enums import Provider, RowErrorCode, RowStatus, RunStatus
from app.observability import calculation_stage, error_type, observe_calculation, observe_source
from app.services.market import latest_rates, latest_snapshots
from app.services.workspaces import RevisionConflict

logger = logging.getLogger(__name__)
T = TypeVar("T")

MEASURE_CODE = "DV01"
# v2: CFV считается по цене с НКД (в v1 НКД не входил)
METHODOLOGY_VERSION = "dv01-v2"
BASE_CURRENCY = "RUB"


class WorkspaceEmpty(Exception):
    """В наборе нет сделок"""


# отказ до записи результата: в метрике расчётов свой статус, а не error
_REJECTED = {RevisionConflict: "conflict", WorkspaceEmpty: "empty"}


@dataclass
class _TradeInput:
    """Копия сделки на момент расчёта: дальше работаем только с ней"""

    id: uuid.UUID
    isin: str
    quantity: int


@dataclass
class _Market:
    """Всё, что известно по одному ISIN к моменту записи"""

    instrument: Instrument | None = None  # уже в справочнике
    new_instrument: contracts.InstrumentInfo | None = None  # пришёл от источника
    snapshot: MarketDataSnapshot | None = None  # последний снимок из БД
    new_snapshot: contracts.MarketSnapshot | None = None  # пришёл от источника
    error: RowErrorCode | None = None

    @property
    def currency(self) -> str | None:
        source = self.new_snapshot or self.snapshot
        return source.currency if source else None


@dataclass
class _Fx:
    """Курс одной валюты: из БД, от источника или ошибка"""

    rate: FxRate | None = None
    new_rate: contracts.FxRate | None = None
    error: RowErrorCode | None = None


@dataclass
class _Prepared:
    trades: list[_TradeInput]
    markets: dict[str, _Market]
    rates: dict[str, _Fx] = field(default_factory=dict)


async def run_calculation(
    db: AsyncSession,
    workspace: Workspace,
    expected_revision: int,
    valuation_date: date,
    market: contracts.MarketDataProvider,
    fx: contracts.FxRateProvider,
    calc: contracts.RiskCalculator,
) -> uuid.UUID:
    """Считает DV01 по набору и сохраняет результат; возвращает id запуска

    Каждый этап пишет событие calc.stage, итог пишет calc.finished и метрики расчёта
    """
    started = time.perf_counter()
    try:
        with calculation_stage("read"):
            prepared = await _read_inputs(db, workspace, expected_revision, valuation_date)
        with calculation_stage("sources"):
            await _fetch_missing(prepared, valuation_date, market, fx)
        with calculation_stage("write"):
            run_id, status, rows = await _save(
                db, workspace, expected_revision, valuation_date, prepared, calc
            )
    except Exception as exc:
        outcome = _REJECTED.get(type(exc), "error")
        observe_calculation(outcome, time.perf_counter() - started, [], error_type=error_type(exc))
        raise
    observe_calculation(
        status.value, time.perf_counter() - started, rows, calculation_id=str(run_id)
    )
    return run_id


# --- шаг 1: чтение -------------------------------------------------------------


async def _read_inputs(
    db: AsyncSession, workspace: Workspace, expected_revision: int, valuation_date: date
) -> _Prepared:
    if workspace.revision != expected_revision:
        raise RevisionConflict
    rows = await db.scalars(
        select(Trade).where(Trade.workspace_id == workspace.id).order_by(Trade.created_at, Trade.id)
    )
    trades = [_TradeInput(t.id, t.isin, t.quantity) for t in rows]
    if not trades:
        raise WorkspaceEmpty

    isins = {t.isin for t in trades}
    instruments = {
        i.isin: i for i in await db.scalars(select(Instrument).where(Instrument.isin.in_(isins)))
    }
    snapshots = await latest_snapshots(db, (i.id for i in instruments.values()), valuation_date)
    markets = {}
    for isin in isins:
        instrument = instruments.get(isin)
        snapshot = snapshots.get(instrument.id) if instrument else None
        markets[isin] = _Market(instrument=instrument, snapshot=snapshot)

    currencies = {m.currency for m in markets.values()} - {None, BASE_CURRENCY}
    rates = {
        cur: _Fx(rate=rate)
        for cur, rate in (await latest_rates(db, currencies, valuation_date)).items()
    }
    await db.commit()  # конец чтения: дальше могут быть долгие внешние запросы
    return _Prepared(trades, markets, rates)


# --- шаг 2: внешние источники (транзакции нет) ------------------------------------


async def _fetch_missing(
    prepared: _Prepared,
    valuation_date: date,
    market: contracts.MarketDataProvider,
    fx: contracts.FxRateProvider,
) -> None:
    for isin, m in prepared.markets.items():
        if m.instrument is None:
            try:
                m.new_instrument = await _ask(
                    market.get_instrument(isin),
                    source=market_source(market),
                    operation="instrument",
                    valid=lambda info, isin=isin: info.isin == isin,
                    isin=isin,
                )
            except contracts.MarketDataError as exc:
                m.error = exc.code
                continue
        # снимок этого источника за дату оценки уже есть в БД: повторно не спрашиваем
        # (у заглушки источника нет, тогда подходит любой сохранённый снимок за эту дату)
        source = getattr(market, "provider", None)
        if (
            m.snapshot is not None
            and m.snapshot.data_as_of == valuation_date
            and (source is None or m.snapshot.provider == source)
        ):
            continue
        try:
            m.new_snapshot = await _ask(
                market.get_snapshot(isin, valuation_date),
                source=market_source(market),
                operation="snapshot",
                valid=lambda snap, isin=isin: (
                    snap.isin == isin and snap.data_as_of <= valuation_date
                ),
                isin=isin,
            )
        except contracts.MarketDataError as exc:
            # источник не ответил: берём последний сохранённый снимок (его дата есть в ответе)
            if m.snapshot is None:
                m.error = exc.code

    # The former demo catalog assigned RUB to the real USD ISIN RU000A105A95.
    # Refresh explicitly demo metadata from MOEX; historical snapshots/results stay frozen.
    # This also covers a MOEX snapshot cached by an earlier, failed calculation.
    if getattr(market, "provider", None) == Provider.MOEX:
        for isin, m in prepared.markets.items():
            snapshot = m.new_snapshot or m.snapshot
            if (
                m.error is None
                and m.instrument is not None
                and m.instrument.name.startswith("ДЕМО ")
                and snapshot is not None
                and snapshot.provider == Provider.MOEX
            ):
                try:
                    m.new_instrument = await _ask(
                        market.get_instrument(isin),
                        source=market_source(market),
                        operation="instrument",
                        valid=lambda info, isin=isin: info.isin == isin,
                        isin=isin,
                    )
                except contracts.MarketDataError as exc:
                    m.error = exc.code

    for currency in {m.currency for m in prepared.markets.values() if not m.error}:
        if currency in (None, BASE_CURRENCY):
            continue
        known = prepared.rates.setdefault(currency, _Fx())
        fx_source = getattr(fx, "source", None)
        if (
            known.rate is not None
            and known.rate.rate_date == valuation_date
            and (fx_source is None or known.rate.source == fx_source)
        ):
            continue
        try:
            known.new_rate = await _ask(
                fx.get_rate_to_rub(currency, valuation_date),
                source=fx_source_name(fx),
                operation="fx_rate",
                valid=lambda rate, currency=currency: (
                    rate.currency == currency
                    and rate.rate_date <= valuation_date
                    and rate.rate_to_rub > 0
                ),
                currency=currency,
            )
        except contracts.MarketDataError as exc:
            # источник курсов не ответил: берём последний сохранённый курс (его дата в ответе)
            if known.rate is None:
                known.error = _fx_error(exc.code)


def market_source(market: contracts.MarketDataProvider) -> str:
    """Имя источника для диагностики: moex, test; none у временной заглушки"""
    provider = getattr(market, "provider", None)
    return provider.value if provider is not None else "none"


def fx_source_name(fx: contracts.FxRateProvider) -> str:
    return getattr(fx, "source", None) or "none"


async def _ask(
    call: Awaitable[T],
    *,
    source: str,
    operation: str,
    valid: Callable[[T], bool] = lambda _: True,
    **fields: object,
) -> T:
    """Запрос к источнику: любой сбой адаптера становится ошибкой строки, а не ответом 500

    Длительность и исход (ok или код ошибки) уходят в событие source.request и метрики
    """
    unavailable = (
        RowErrorCode.FX_PROVIDER_UNAVAILABLE
        if operation == "fx_rate"
        else RowErrorCode.PROVIDER_UNAVAILABLE
    )
    started = time.perf_counter()
    try:
        result = await call
        _check(valid(result))
    except contracts.MarketDataError as exc:
        # недоступность курсов и рыночных данных различаем по операции, а не по коду адаптера
        code = exc.code
        if code in (RowErrorCode.PROVIDER_UNAVAILABLE, RowErrorCode.FX_PROVIDER_UNAVAILABLE):
            code = unavailable
        observe_source(source, operation, code.value, time.perf_counter() - started, **fields)
        if code != exc.code:
            raise contracts.MarketDataError(code, str(exc)) from exc
        raise
    except Exception as exc:
        observe_source(
            source,
            operation,
            unavailable.value,
            time.perf_counter() - started,
            error_type=error_type(exc),
            **fields,
        )
        logger.warning("source.crash", exc_info=exc, extra={"source": source})
        raise contracts.MarketDataError(unavailable) from exc
    observe_source(source, operation, "ok", time.perf_counter() - started, **fields)
    return result


def _fx_error(code: RowErrorCode) -> RowErrorCode:
    """Недоступность источника курсов отличаем от недоступности рыночных данных"""
    if code == RowErrorCode.PROVIDER_UNAVAILABLE:
        return RowErrorCode.FX_PROVIDER_UNAVAILABLE
    return code


def _check(condition: bool) -> None:
    """Источник вернул не то, что просили (другой ISIN, данные позже даты оценки)"""
    if not condition:
        raise contracts.MarketDataError(RowErrorCode.INCOMPATIBLE_MARKET_DATA)


# --- шаг 3: запись ---------------------------------------------------------------


async def _save(
    db: AsyncSession,
    workspace: Workspace,
    expected_revision: int,
    valuation_date: date,
    prepared: _Prepared,
    calc: contracts.RiskCalculator,
) -> tuple[uuid.UUID, RunStatus, list[tuple[str, str]]]:
    # пока шли внешние запросы, набор могли изменить: тогда результат уже не про него
    revision = await db.scalar(
        select(Workspace.revision).where(Workspace.id == workspace.id).with_for_update()
    )
    if revision != expected_revision:
        await db.rollback()
        raise RevisionConflict

    await _save_instruments(db, workspace, prepared.markets)
    await _save_snapshots(db, prepared.markets)
    await _save_rates(db, prepared.rates)

    results = [_result_row(t, prepared, calc) for t in prepared.trades]
    ok = [r for r in results if r.status == RowStatus.SUCCESS]
    if len(ok) == len(results):
        status, total = RunStatus.SUCCESS, sum((r.metric_value for r in ok), Decimal(0))
    else:
        status, total = (RunStatus.PARTIAL if ok else RunStatus.FAILED), None

    run = CalculationRun(
        workspace_id=workspace.id,
        workspace_revision=expected_revision,
        measure_code=MEASURE_CODE,
        methodology_version=METHODOLOGY_VERSION,
        valuation_date=valuation_date,
        status=status,
        total_value=total,
        completed_at=func.now(),
        results=results,
    )
    rows = [(r.status.value, r.error_code or "none") for r in results]
    db.add(run)
    await db.commit()
    return run.id, status, rows


async def _save_instruments(
    db: AsyncSession, workspace: Workspace, markets: dict[str, _Market]
) -> None:
    for isin, m in markets.items():
        if m.new_instrument is None:
            continue
        info = m.new_instrument
        await db.execute(
            pg_insert(Instrument)
            .values(
                id=uuid.uuid4(),
                isin=isin,
                name=info.name,
                instrument_type=info.instrument_type,
                currency=info.currency,
            )
            .on_conflict_do_update(
                index_elements=["isin"],
                set_={
                    "name": info.name,
                    "instrument_type": info.instrument_type,
                    "currency": info.currency,
                },
            )
        )
        m.instrument = await db.scalar(
            select(Instrument)
            .where(Instrument.isin == isin)
            .execution_options(populate_existing=True)
        )
        # сделки набора с этим ISIN теперь связаны со справочником
        await db.execute(
            update(Trade)
            .where(
                Trade.workspace_id == workspace.id,
                Trade.isin == isin,
                Trade.instrument_id.is_(None),
            )
            .values(instrument_id=m.instrument.id)
        )


async def _save_snapshots(db: AsyncSession, markets: dict[str, _Market]) -> None:
    for m in markets.values():
        new = m.new_snapshot
        if new is None or m.instrument is None:
            continue
        key = dict(instrument_id=m.instrument.id, data_as_of=new.data_as_of, provider=new.provider)
        await db.execute(
            pg_insert(MarketDataSnapshot)
            .values(
                id=uuid.uuid4(),
                **key,
                retrieved_at=new.retrieved_at,
                currency=new.currency,
                settlement_currency=new.settlement_currency,
                nominal=new.nominal,
                price_pct=new.price_pct,
                accrued_interest=new.accrued_interest,
                duration_mod=new.duration_mod,
                convexity=new.convexity,
                coupon_type=new.coupon_type,
                coupon_frequency=new.coupon_frequency,
            )
            .on_conflict_do_nothing(constraint="uq_snapshot_per_day")  # один снимок в день
        )
        saved = await db.scalar(select(MarketDataSnapshot).filter_by(**key))
        # источник мог вернуть данные старше уже сохранённых: берём более свежие
        if m.snapshot is None or saved.data_as_of >= m.snapshot.data_as_of:
            m.snapshot = saved


async def _save_rates(db: AsyncSession, rates: dict[str, _Fx]) -> None:
    for currency, fx in rates.items():
        new = fx.new_rate
        if new is None:
            continue
        key = dict(currency=currency, rate_date=new.rate_date, source=new.source)
        await db.execute(
            pg_insert(FxRate)
            .values(id=uuid.uuid4(), **key, rate_to_rub=new.rate_to_rub, retrieved_at=func.now())
            .on_conflict_do_nothing(constraint="uq_fx_rates_currency_rate_date_source")
        )
        saved = await db.scalar(select(FxRate).filter_by(**key))
        if fx.rate is None or saved.rate_date >= fx.rate.rate_date:
            fx.rate = saved


def _result_row(
    trade: _TradeInput, prepared: _Prepared, calc: contracts.RiskCalculator
) -> CalculationResult:
    """Строка результата: копия входов сделки + подставленные параметры + DV01 или ошибка"""
    row = CalculationResult(trade_id=trade.id, input_isin=trade.isin, input_quantity=trade.quantity)

    def failed(code: RowErrorCode) -> CalculationResult:
        row.status, row.error_code = RowStatus.FAILED, code.value
        return row

    m = prepared.markets[trade.isin]
    if m.error is not None:
        return failed(m.error)
    snap = m.snapshot
    row.market_data_snapshot_id = snap.id
    row.snapshot = snap
    if snap.nominal is None or snap.price_pct is None or snap.accrued_interest is None:
        return failed(RowErrorCode.MISSING_MARKET_DATA)
    if m.instrument is not None and snap.currency != m.instrument.currency:
        return failed(RowErrorCode.INCOMPATIBLE_MARKET_DATA)

    if snap.currency == BASE_CURRENCY:
        rate = Decimal(1)  # для рублёвой бумаги курс не нужен, fx_rate_id остаётся NULL
    else:
        fx = prepared.rates.get(snap.currency) or _Fx(error=RowErrorCode.MISSING_FX_RATE)
        if fx.rate is None:
            return failed(fx.error or RowErrorCode.MISSING_FX_RATE)
        rate = fx.rate.rate_to_rub
        row.fx_rate_id = fx.rate.id

    row.nominal_used, row.price_pct_used, row.fx_rate_used = snap.nominal, snap.price_pct, rate
    row.accrued_interest_used = snap.accrued_interest
    try:
        result = calc(
            contracts.Dv01Input(
                quantity=trade.quantity,
                nominal=snap.nominal,
                price_pct=snap.price_pct,
                accrued_interest=snap.accrued_interest,
                fx_rate=rate,
                duration_mod=snap.duration_mod,
                convexity=snap.convexity,
                coupon_type=snap.coupon_type,
                coupon_frequency=snap.coupon_frequency,
            )
        )
    except contracts.CalculationInputError as exc:
        return failed(exc.code)

    row.status = RowStatus.SUCCESS
    row.current_fair_value = result.current_fair_value
    row.duration_used = result.duration_used
    row.convexity_used = result.convexity_used
    row.metric_value = result.dv01
    return row


async def latest_calculation_id(db: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        select(CalculationRun.id)
        .where(CalculationRun.workspace_id == workspace_id)
        .order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc())
        .limit(1)
    )


# --- чтение сохранённого результата ------------------------------------------------


async def get_calculation(
    db: AsyncSession, run_id: uuid.UUID
) -> tuple[CalculationRun, list[CalculationResult]] | None:
    """Запуск и его строки в порядке добавления сделок (удалённые сделки в конце)"""
    # populate_existing: свежие created_at/completed_at, даже если запуск только что создан
    run = await db.get(CalculationRun, run_id, populate_existing=True)
    if run is None:
        return None
    query = (
        select(CalculationResult)
        .outerjoin(Trade, Trade.id == CalculationResult.trade_id)
        .where(CalculationResult.run_id == run_id)
        .options(selectinload(CalculationResult.snapshot), selectinload(CalculationResult.fx_rate))
        .execution_options(populate_existing=True)
        .order_by(Trade.created_at.asc().nulls_last(), CalculationResult.input_isin)
    )
    return run, list(await db.scalars(query))
