"""Единицы котировки облигации: в контракте цена в % от номинала (95.5, а не 0.955)

Единицы берутся из описания поля источника, а не угадываются по величине числа:
цена 0.9 может быть и 90% в долях, и 0.9% у бумаги в дефолте
"""

from decimal import Decimal

from app.contracts import MarketDataError
from app.enums import RowErrorCode

PRICE_PERCENT = "percent"  # 95.5 = 95.5% номинала (ISS: «Цена закрытия, % от номинала»)
PRICE_FRACTION = "fraction"  # 0.955 = 95.5% номинала


def price_to_pct(value: Decimal, unit: str) -> Decimal:
    """Котировка в единицах источника → % от номинала; деление на 100 делает только risk_core"""
    if unit == PRICE_PERCENT:
        return value
    if unit == PRICE_FRACTION:
        return value * 100
    raise MarketDataError(
        RowErrorCode.INCOMPATIBLE_MARKET_DATA, f"неизвестные единицы цены {unit!r}"
    )
