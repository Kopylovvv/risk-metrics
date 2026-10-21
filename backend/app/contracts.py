"""Контракт между backend (Семён) и модулями данных и расчёта (Андрей)

Кто за что отвечает:

* ``market_data`` (Андрей): адаптеры Cbonds и RuData, порядок Cbonds -> RuData,
  нормализация ответа источника в ``MarketSnapshot`` (единицы, даты, полнота)
  Сам в БД не пишет
* ``risk_core`` (Андрей): чистая функция ``calc_dv01``: только арифметика на Decimal,
  без сети, БД, текущей даты и глобального состояния
* backend (Семён): API, сессия и доступ к набору, revision, поиск снимка в БД,
  вызов адаптеров при его отсутствии, сохранение снимков и результатов,
  статус расчёта и итог total_value = Σ metric_value

Все денежные величины в ``Dv01Result`` указаны в рублях
Значения не округляются: округление (если будет) делается только при выводе
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class Provider(StrEnum):
    CBONDS = "cbonds"
    RUDATA = "rudata"
    TEST = "test"  # контрольный набор, только для тестового окружения


class CouponType(StrEnum):
    FIXED = "fixed"
    FLOATING = "floating"  # Duration = 1 / coupon_frequency, Convexity = 0
    ZERO = "zero"


class RowErrorCode(StrEnum):
    """Коды ошибок по одной сделке (calculation_results.error_code)"""

    INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INCOMPATIBLE_MARKET_DATA = "INCOMPATIBLE_MARKET_DATA"
    MISSING_FX_RATE = "MISSING_FX_RATE"


# --------------------------------------------------------------------------
# Данные из источников (выход market_data)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    """Справочные данные выпуска (таблица instruments)"""

    isin: str
    name: str | None
    instrument_type: str  # например "bond"
    currency: str  # ISO 4217, валюта номинала


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Рыночные параметры на дату (таблица market_data_snapshots)

    Нормализацию делает адаптер:
    * nominal: текущий номинал ОДНОЙ бумаги в валюте ``currency``;
    * price_pct: чистая котировка в % от номинала (95.5, а не 0.955);
    * accrued_interest: НКД на одну бумагу, только для справки, в CFV не входит;
    * duration_mod, convexity: как отдал источник; None, если источник не дал
    """

    isin: str
    provider: Provider
    data_as_of: date
    retrieved_at: datetime
    currency: str
    nominal: Decimal | None
    price_pct: Decimal | None
    accrued_interest: Decimal | None
    duration_mod: Decimal | None
    convexity: Decimal | None
    coupon_type: CouponType
    coupon_frequency: int | None  # выплат в год


@dataclass(frozen=True, slots=True)
class FxRate:
    """Курс валюты к рублю (таблица fx_rates). Для RUB не запрашивается: курс = 1"""

    currency: str
    rate_date: date
    rate_to_rub: Decimal
    source: str  # например "cbr"


class MarketDataError(Exception):
    """Источник не смог вернуть данные. ``code`` попадает в error_code строки"""

    def __init__(self, code: RowErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


class MarketDataProvider(Protocol):
    """Реализует Андрей. Цепочка Cbonds -> RuData тоже реализует этот протокол

    Методы бросают ``MarketDataError`` с кодами INSTRUMENT_NOT_FOUND,
    MISSING_MARKET_DATA, PROVIDER_UNAVAILABLE или INCOMPATIBLE_MARKET_DATA
    """

    async def get_instrument(self, isin: str) -> InstrumentInfo: ...

    async def get_snapshot(self, isin: str, as_of: date) -> MarketSnapshot:
        """Последние данные на дату ``as_of`` или раньше; фактическая дата в ``data_as_of``"""
        ...


class FxRateProvider(Protocol):
    """Бросает ``MarketDataError(MISSING_FX_RATE)``, если курса нет"""

    async def get_rate_to_rub(self, currency: str, on: date) -> FxRate: ...


# --------------------------------------------------------------------------
# Расчёт (вход и выход risk_core)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dv01Input:
    """Всё, что нужно для расчёта одной сделки. Собирает backend"""

    quantity: int  # количество бумаг, != 0; < 0 означает шорт
    nominal: Decimal
    price_pct: Decimal  # % от номинала
    fx_rate: Decimal  # к рублю; для RUB = 1
    duration_mod: Decimal | None
    convexity: Decimal | None
    coupon_type: CouponType
    coupon_frequency: int | None


@dataclass(frozen=True, slots=True)
class Dv01Result:
    """Выход risk_core. Всё в рублях, без округления"""

    current_fair_value: Decimal  # quantity * nominal * price_pct / 100 * fx_rate
    duration_used: Decimal  # floating: 1 / coupon_frequency
    convexity_used: Decimal  # floating: 0
    dv01: Decimal  # CFV * (-D * 1bp + 1/2 * C * 1bp^2)


class CalculationInputError(Exception):
    """risk_core не может посчитать по этим входам (например, нет дюрации у fixed)"""

    def __init__(self, code: RowErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


class RiskCalculator(Protocol):
    """Сигнатура ``risk_core.calc_dv01``. Бросает ``CalculationInputError``"""

    def __call__(self, data: Dv01Input) -> Dv01Result: ...
