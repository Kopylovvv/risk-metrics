"""Временные источники данных, пока не подключены адаптеры Cbonds/RuData

Реализуют протоколы из contracts.py: расчёт берёт данные только из БД, а за недостающими
«идёт» сюда и получает понятную ошибку строки. Подключаются в app/api/deps.py
"""

from datetime import date

from app.contracts import FxRate, InstrumentInfo, MarketDataError, MarketSnapshot
from app.enums import RowErrorCode


class NoMarketDataSource:
    async def get_instrument(self, isin: str) -> InstrumentInfo:
        raise MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE, "источники ещё не подключены")

    async def get_snapshot(self, isin: str, as_of: date) -> MarketSnapshot:
        raise MarketDataError(RowErrorCode.PROVIDER_UNAVAILABLE, "источники ещё не подключены")


class NoFxRateSource:
    async def get_rate_to_rub(self, currency: str, on: date) -> FxRate:
        raise MarketDataError(RowErrorCode.MISSING_FX_RATE, "источник курсов ещё не подключён")
