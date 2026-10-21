"""Временная реализация расчётного ядра до появления модуля risk_core

Сигнатура RiskCalculator из contracts.py: чистая функция, без сети и БД
"""

from decimal import Decimal

from app.contracts import CalculationInputError, Dv01Input, Dv01Result
from app.enums import CouponType, RowErrorCode

BASIS_POINT = Decimal("0.0001")


def calc_dv01(data: Dv01Input) -> Dv01Result:
    if data.coupon_type == CouponType.FLOATING:
        # правило ТЗ для плавающего купона
        if not data.coupon_frequency:
            raise CalculationInputError(RowErrorCode.MISSING_MARKET_DATA, "нет частоты купона")
        duration = Decimal(1) / Decimal(data.coupon_frequency)
        convexity = Decimal(0)
    else:
        if data.duration_mod is None or data.convexity is None:
            raise CalculationInputError(
                RowErrorCode.MISSING_MARKET_DATA, "нет дюрации или выпуклости"
            )
        duration, convexity = data.duration_mod, data.convexity

    # CFV = количество × номинал × котировка/100 × курс; НКД не входит
    cfv = Decimal(data.quantity) * data.nominal * data.price_pct / Decimal(100) * data.fx_rate
    dv01 = cfv * (-duration * BASIS_POINT + Decimal("0.5") * convexity * BASIS_POINT**2)
    return Dv01Result(
        current_fair_value=cfv, duration_used=duration, convexity_used=convexity, dv01=dv01
    )
