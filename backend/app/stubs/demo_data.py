"""Демо-данные для ручной проверки расчёта, пока не подключены источники

Значения выдуманы. Запуск из папки backend: python -m app.stubs.demo_data [ДАТА]
Повторный запуск ничего не дублирует (один снимок на инструмент, день и источник)
"""

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal as D

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import FxRate, Instrument, MarketDataSnapshot
from app.db.session import SessionFactory, engine
from app.enums import CouponType, Provider

# ISIN, название, валюта, номинал, цена %, НКД, дюрация, выпуклость, тип купона, выплат в год
BONDS = [
    (
        "RU000A1038V6",
        "ДЕМО ОФЗ, фиксированный купон",
        "RUB",
        D(1000),
        D(95),
        D("12.5"),
        D(3),
        D(12),
        CouponType.FIXED,
        2,
    ),
    (
        "RU000A105A95",
        "ДЕМО флоатер",
        "RUB",
        D(1000),
        D(100),
        D(5),
        None,
        None,
        CouponType.FLOATING,
        4,
    ),
    (
        "XS0191754729",
        "ДЕМО еврооблигация (USD)",
        "USD",
        D(1000),
        D("98.5"),
        D("10.25"),
        D("4.2"),
        D(25),
        CouponType.FIXED,
        2,
    ),
    (
        "RU000A0JX0J2",
        "ДЕМО выпуск без дюрации",
        "RUB",
        D(1000),
        D("101.2"),
        D("3.1"),
        None,
        None,
        CouponType.FIXED,
        2,
    ),
]
FX = {"USD": D("92.5")}


async def seed(on: date) -> None:
    now = datetime.now(UTC)
    async with SessionFactory() as db:
        for isin, name, currency, nominal, price, accrued, dur, conv, coupon, freq in BONDS:
            await db.execute(
                pg_insert(Instrument)
                .values(
                    id=uuid.uuid4(), isin=isin, name=name, instrument_type="bond", currency=currency
                )
                .on_conflict_do_nothing(index_elements=["isin"])
            )
            instrument_id = await db.scalar(select(Instrument.id).where(Instrument.isin == isin))
            await db.execute(
                pg_insert(MarketDataSnapshot)
                .values(
                    id=uuid.uuid4(),
                    instrument_id=instrument_id,
                    provider=Provider.TEST,
                    data_as_of=on,
                    retrieved_at=now,
                    currency=currency,
                    nominal=nominal,
                    price_pct=price,
                    accrued_interest=accrued,
                    duration_mod=dur,
                    convexity=conv,
                    coupon_type=coupon,
                    coupon_frequency=freq,
                )
                .on_conflict_do_nothing(constraint="uq_snapshot_per_day")
            )
        for currency, rate in FX.items():
            await db.execute(
                pg_insert(FxRate)
                .values(
                    id=uuid.uuid4(),
                    currency=currency,
                    rate_date=on,
                    rate_to_rub=rate,
                    source="demo",
                    retrieved_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_fx_rates_currency_rate_date_source")
            )
        await db.commit()
    await engine.dispose()
    print(f"Демо-данные на {on}: {len(BONDS)} облигации, курсы {', '.join(FX)}")


if __name__ == "__main__":
    asyncio.run(seed(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()))
