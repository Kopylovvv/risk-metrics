"""Контракт между backend (Семён) и модулями данных и расчёта (Андрей)

Кто за что отвечает:

* ``market_data`` (Андрей): адаптер Мосбиржи (параметры облигаций) и курсы ЦБ РФ,
  нормализация ответа источника в ``MarketSnapshot`` и ``FxRate`` (единицы, даты, полнота)
  Сам в БД не пишет. Cbonds и RuData в расчёте больше не используются
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
from typing import Protocol

# Перечисления лежат в app/enums.py; импорт отсюда тоже работает:
# from app.contracts import Provider, CouponType, RowErrorCode
from app.enums import CouponType, FxSource, Provider, RowErrorCode

__all__ = [
    "CalculationInputError",
    "CouponType",
    "Dv01Input",
    "Dv01Result",
    "FxRate",
    "FxRateProvider",
    "FxSource",
    "InstrumentInfo",
    "MarketDataError",
    "MarketDataProvider",
    "MarketSnapshot",
    "Provider",
    "RiskCalculator",
    "RowErrorCode",
]


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
    * currency: валюта НОМИНАЛА (у Мосбиржи FACEUNIT); курс ЦБ берётся для неё;
    * settlement_currency: валюта расчётов по сделкам (у Мосбиржи CURRENCYID, SUR → RUB),
      может отличаться от валюты номинала (например, номинал USD, расчёты в RUB);
      на формулу не влияет: цена дана в % от номинала, НКД в валюте номинала,
      поэтому CFV = количество × (номинал × цена/100 + НКД) × курс валюты номинала;
    * nominal: текущий номинал ОДНОЙ бумаги в валюте ``currency``;
    * price_pct: чистая котировка в % от номинала (95.5, а не 0.955). Единицы берутся
      из описания поля источника, а не угадываются по величине; доля 0.955 → 95.5;
    * accrued_interest: НКД на одну бумагу в валюте ``currency`` на дату данных,
      входит в CFV (цена с НКД); 0, если купон только что выплачен; None, если нет данных.
      Если источник отдал НКД в валюте расчётов, адаптер переводит его в валюту номинала
      или бросает INCOMPATIBLE_MARKET_DATA;
    * duration_mod, convexity: модифицированная дюрация в годах и выпуклость в годах²;
      None, если источник не дал
    * data_as_of: дата торгов, к которой относятся цена и НКД
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
    settlement_currency: str | None = None  # ISO 4217; None, если источник не дал


@dataclass(frozen=True, slots=True)
class FxRate:
    """Курс валюты к рублю (таблица fx_rates). Для RUB не запрашивается: курс = 1

    rate_to_rub: рублей за ОДНУ единицу валюты. ЦБ публикует часть курсов за 10, 100
    или 10 000 единиц (поле Nominal): такой курс нужно разделить, удобно через ``per_unit``
    rate_date: дата, на которую ЦБ установил курс
    """

    currency: str
    rate_date: date
    rate_to_rub: Decimal
    source: str  # FxSource: "cbr" или "demo"

    @classmethod
    def per_unit(
        cls,
        currency: str,
        rate_date: date,
        value: Decimal,
        nominal: int,
        source: str = FxSource.CBR,
    ) -> "FxRate":
        """Курс ЦБ «value рублей за nominal единиц» → рублей за одну единицу"""
        if nominal <= 0 or value <= 0:
            raise MarketDataError(
                RowErrorCode.INCOMPATIBLE_MARKET_DATA, f"{currency}: курс {value} за {nominal}"
            )
        return cls(currency, rate_date, Decimal(value) / Decimal(nominal), source)


class MarketDataError(Exception):
    """Источник не смог вернуть данные. ``code`` попадает в error_code строки"""

    def __init__(self, code: RowErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


class MarketDataProvider(Protocol):
    """Реализует Андрей: адаптер Мосбиржи

    ``provider``: какой Provider ставится в снимках; None у временной заглушки;
    снимок этого источника за дату оценки, уже сохранённый в БД, повторно не запрашивается
    Методы бросают ``MarketDataError`` с кодами INSTRUMENT_NOT_FOUND (выпуска нет),
    MISSING_MARKET_DATA (нет цены или обязательного поля на дату),
    INCOMPATIBLE_MARKET_DATA (чужой ISIN, дата позже, неизвестные единицы или валюта)
    или PROVIDER_UNAVAILABLE (сеть, таймаут, ответ не того формата)
    """

    provider: Provider | None

    async def get_instrument(self, isin: str) -> InstrumentInfo: ...

    async def get_snapshot(self, isin: str, as_of: date) -> MarketSnapshot:
        """Последние данные на дату ``as_of`` или раньше; фактическая дата в ``data_as_of``"""
        ...


class FxRateProvider(Protocol):
    """Реализует курсы ЦБ РФ. ``source``: значение fx_rates.source, None у заглушки

    Бросает ``MarketDataError`` с кодом MISSING_FX_RATE (ЦБ не устанавливал курс этой
    валюты), FX_PROVIDER_UNAVAILABLE (источник не ответил) или INCOMPATIBLE_MARKET_DATA
    """

    source: str | None

    async def get_rate_to_rub(self, currency: str, on: date) -> FxRate: ...


# --------------------------------------------------------------------------
# Расчёт (вход и выход risk_core)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dv01Input:
    """Всё, что нужно для расчёта одной сделки. Собирает backend"""

    quantity: int  # количество бумаг, != 0; < 0 означает шорт
    nominal: Decimal
    price_pct: Decimal  # чистая котировка, % от номинала
    accrued_interest: Decimal  # НКД на одну бумагу, в валюте номинала
    fx_rate: Decimal  # к рублю; для RUB = 1
    duration_mod: Decimal | None
    convexity: Decimal | None
    coupon_type: CouponType
    coupon_frequency: int | None


@dataclass(frozen=True, slots=True)
class Dv01Result:
    """Выход risk_core. Всё в рублях, без округления"""

    # quantity * (nominal * price_pct / 100 + accrued_interest) * fx_rate
    current_fair_value: Decimal
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
