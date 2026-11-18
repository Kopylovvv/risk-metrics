"""DV01 одной сделки (методика dv01-v2)

    CFV  = quantity × (nominal × price_pct / 100 + accrued_interest) × fx_rate
    DV01 = CFV × (−D × 1bp + ½ × C × 1bp²),  1bp = 0.0001

Правила:
* знак: лонг (quantity > 0) при росте ставки на 1 bp дешевеет, DV01 < 0; шорт, DV01 > 0
* floating: D = 1 / coupon_frequency, C = 0 (правило ТЗ); дюрация и выпуклость источника
  в этом случае не используются
* fixed и zero: D и C как отдал источник (модифицированная дюрация в годах)
* округления нет: Decimal с точностью контекста (28 значащих цифр), округляет только вывод
* итог по нескольким сделкам (Σ DV01) считает backend, здесь одна сделка

Пример вызова:

    calc_dv01(Dv01Input(quantity=100, nominal=Decimal(1000), price_pct=Decimal(95),
                        accrued_interest=Decimal("12.5"), fx_rate=Decimal(1),
                        duration_mod=Decimal(3), convexity=Decimal(12),
                        coupon_type=CouponType.FIXED, coupon_frequency=2))
    → CFV 96250, DV01 −28.869225

Ошибки (``CalculationInputError``, код попадает в error_code строки):
* MISSING_MARKET_DATA: у fixed/zero нет дюрации или выпуклости, у floating нет частоты купона
* INCOMPATIBLE_MARKET_DATA: значение, которого не бывает: номинал, котировка или курс ≤ 0,
  НКД < 0, частота купона ≤ 0, дюрация < 0
quantity = 0 запрещён контрактом и БД, поэтому это ``ValueError``, а не ошибка строки
"""

from decimal import Decimal
from typing import NoReturn

from app.contracts import CalculationInputError, Dv01Input, Dv01Result
from app.enums import CouponType, RowErrorCode

BASIS_POINT = Decimal("0.0001")


def calc_dv01(data: Dv01Input) -> Dv01Result:
    if data.quantity == 0:
        raise ValueError("quantity = 0: у сделки нет позиции")
    _check_positive(nominal=data.nominal, price_pct=data.price_pct, fx_rate=data.fx_rate)
    if data.accrued_interest < 0:
        _incompatible("НКД < 0")

    duration, convexity = _duration_and_convexity(data)
    # цена одной бумаги с НКД в валюте номинала, затем в рубли
    dirty_price = data.nominal * data.price_pct / Decimal(100) + data.accrued_interest
    cfv = Decimal(data.quantity) * dirty_price * data.fx_rate
    dv01 = cfv * (-duration * BASIS_POINT + Decimal("0.5") * convexity * BASIS_POINT**2)
    return Dv01Result(
        current_fair_value=cfv, duration_used=duration, convexity_used=convexity, dv01=dv01
    )


def _duration_and_convexity(data: Dv01Input) -> tuple[Decimal, Decimal]:
    """D и C, которые идут в формулу: по правилу ТЗ для floating, иначе от источника"""
    if data.coupon_type == CouponType.FLOATING:
        if data.coupon_frequency is None:
            _missing("нет частоты купона у floating")
        if data.coupon_frequency <= 0:
            _incompatible("частота купона ≤ 0")
        return Decimal(1) / Decimal(data.coupon_frequency), Decimal(0)

    if data.duration_mod is None or data.convexity is None:
        _missing("нет дюрации или выпуклости")
    if data.duration_mod < 0:
        _incompatible("дюрация < 0")
    # выпуклость бывает отрицательной (например, у бумаг с офертой), её не проверяем
    return data.duration_mod, data.convexity


def _check_positive(**values: Decimal) -> None:
    for name, value in values.items():
        if value <= 0:
            _incompatible(f"{name} ≤ 0")


def _missing(message: str) -> NoReturn:
    raise CalculationInputError(RowErrorCode.MISSING_MARKET_DATA, message)


def _incompatible(message: str) -> NoReturn:
    raise CalculationInputError(RowErrorCode.INCOMPATIBLE_MARKET_DATA, message)
