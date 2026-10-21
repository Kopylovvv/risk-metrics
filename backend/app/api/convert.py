"""Записи БД → модели ответов API"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    CalculationResult,
    CalculationRun,
    Instrument,
    MarketDataSnapshot,
    Trade,
    Workspace,
)
from app.schemas import (
    CalculationOut,
    CalculationRowOut,
    InstrumentOut,
    TradeOut,
    WorkspaceOut,
)
from app.services.workspaces import latest_snapshots, list_trades


def instrument_out(instrument: Instrument, snapshot: MarketDataSnapshot | None) -> InstrumentOut:
    # рыночные поля берутся из последнего снимка; если снимка нет, будет null
    return InstrumentOut(
        isin=instrument.isin,
        name=instrument.name,
        instrument_type=instrument.instrument_type,
        currency=instrument.currency,
        nominal=snapshot.nominal if snapshot else None,
        price_pct=snapshot.price_pct if snapshot else None,
        coupon_type=snapshot.coupon_type if snapshot else None,
        coupon_frequency=snapshot.coupon_frequency if snapshot else None,
        data_as_of=snapshot.data_as_of if snapshot else None,
        provider=snapshot.provider if snapshot else None,
    )


def trade_out(trade: Trade, snapshots: dict) -> TradeOut:
    instrument = None
    if trade.instrument is not None:
        instrument = instrument_out(trade.instrument, snapshots.get(trade.instrument_id))
    return TradeOut(
        id=trade.id,
        isin=trade.isin,
        quantity=trade.quantity,
        instrument=instrument,
        created_at=trade.created_at,
        updated_at=trade.updated_at,
    )


async def workspace_out(db: AsyncSession, workspace: Workspace) -> WorkspaceOut:
    """Набор целиком: сделки по порядку добавления и карточки их инструментов"""
    trades = await list_trades(db, workspace.id)
    snapshots = await latest_snapshots(db, (t.instrument_id for t in trades if t.instrument_id))
    return WorkspaceOut(
        id=workspace.id,
        revision=workspace.revision,
        trades=[trade_out(t, snapshots) for t in trades],
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def calculation_out(run: CalculationRun, results: list[CalculationResult]) -> CalculationOut:
    rows = [
        CalculationRowOut(
            trade_id=r.trade_id,
            isin=r.input_isin,
            quantity=r.input_quantity,
            status=r.status,
            error_code=r.error_code,
            provider=r.snapshot.provider if r.snapshot else None,
            data_as_of=r.snapshot.data_as_of if r.snapshot else None,
            nominal_used=r.nominal_used,
            price_pct_used=r.price_pct_used,
            fx_rate_used=r.fx_rate_used,
            current_fair_value=r.current_fair_value,
            duration_used=r.duration_used,
            convexity_used=r.convexity_used,
            metric_value=r.metric_value,
        )
        for r in results
    ]
    return CalculationOut(
        id=run.id,
        workspace_id=run.workspace_id,
        workspace_revision=run.workspace_revision,
        measure_code=run.measure_code,
        methodology_version=run.methodology_version,
        valuation_date=run.valuation_date,
        status=run.status,
        total_value=run.total_value,
        results=rows,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )
