"""Расчётное ядро без БД и сети: контрольные примеры, правило floating, ошибки входов

Ожидаемые значения посчитаны вручную (арифметика в комментариях), а не той же функцией
"""

import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import CalculationInputError, Dv01Input
from app.enums import CouponType, RowErrorCode
from app.risk_core import BASIS_POINT, calc_dv01

# Облигация с фиксированным купоном: те же параметры, что в тестах API (RU000A1038V6)
FIXED = Dv01Input(
    quantity=100,
    nominal=Decimal(1000),
    price_pct=Decimal(95),
    accrued_interest=Decimal("12.5"),
    fx_rate=Decimal(1),
    duration_mod=Decimal(3),
    convexity=Decimal(12),
    coupon_type=CouponType.FIXED,
    coupon_frequency=2,
)


# --- контрольные примеры ----------------------------------------------------------


def test_fixed_coupon_control_example():
    # CFV = 100 × (1000 × 95 / 100 + 12.5) = 100 × 962.5 = 96 250
    # множитель = −3 × 0.0001 + ½ × 12 × 0.0001² = −0.0003 + 0.00000006 = −0.00029994
    # DV01 = 96 250 × −0.00029994 = −28.869225
    result = calc_dv01(FIXED)
    assert result.current_fair_value == Decimal("96250")
    assert result.duration_used == Decimal(3)
    assert result.convexity_used == Decimal(12)
    assert result.dv01 == Decimal("-28.869225")


def test_short_position_flips_sign():
    result = calc_dv01(replace(FIXED, quantity=-100))
    assert result.current_fair_value == Decimal("-96250")
    assert result.dv01 == Decimal("28.869225")


def test_floating_coupon_uses_tz_rule():
    # D = 1 / 4 = 0.25, C = 0; дюрация и выпуклость источника игнорируются
    # CFV = 299 × (1000 × 100 / 100 + 5) = 299 × 1005 = 300 495
    # DV01 = 300 495 × (−0.25 × 0.0001) = −7.512375
    floater = replace(
        FIXED,
        quantity=299,
        price_pct=Decimal(100),
        accrued_interest=Decimal(5),
        duration_mod=Decimal(7),
        convexity=Decimal(50),
        coupon_type=CouponType.FLOATING,
        coupon_frequency=4,
    )
    result = calc_dv01(floater)
    assert result.duration_used == Decimal("0.25")
    assert result.convexity_used == Decimal(0)
    assert result.current_fair_value == Decimal("300495")
    assert result.dv01 == Decimal("-7.512375")


def test_floating_coupon_without_duration_from_source():
    floater = replace(FIXED, duration_mod=None, convexity=None, coupon_type=CouponType.FLOATING)
    # D = 1 / 2 = 0.5; DV01 = 96 250 × (−0.5 × 0.0001) = −4.8125
    assert calc_dv01(floater).dv01 == Decimal("-4.8125")


def test_foreign_currency_bond():
    # CFV = −10 × (1000 × 98.5 / 100 + 10.25) × 92.5 = −10 × 995.25 × 92.5 = −920 606.25
    # множитель = −4.2 × 0.0001 + ½ × 25 × 0.0001² = −0.00042 + 0.000000125 = −0.000419875
    # DV01 = −920 606.25 × −0.000419875 = 386.53954921875
    usd = replace(
        FIXED,
        quantity=-10,
        price_pct=Decimal("98.5"),
        accrued_interest=Decimal("10.25"),
        fx_rate=Decimal("92.5"),
        duration_mod=Decimal("4.2"),
        convexity=Decimal(25),
    )
    result = calc_dv01(usd)
    assert result.current_fair_value == Decimal("-920606.25")
    assert result.dv01 == Decimal("386.53954921875")


def test_zero_coupon_uses_source_duration():
    zero = replace(FIXED, coupon_type=CouponType.ZERO, coupon_frequency=None)
    assert calc_dv01(zero).dv01 == Decimal("-28.869225")


def test_zero_accrued_interest_is_valid():
    # купон только что выплачен: CFV = 100 × 950 = 95 000
    assert calc_dv01(replace(FIXED, accrued_interest=Decimal(0))).current_fair_value == 95000


def test_negative_convexity_is_allowed():
    # множитель = −3 × 0.0001 + ½ × (−12) × 0.0001² = −0.00030006
    result = calc_dv01(replace(FIXED, convexity=Decimal(-12)))
    assert result.dv01 == Decimal("96250") * Decimal("-0.00030006")


def test_no_rounding():
    # значения с длинной дробной частью сохраняются целиком
    odd = replace(FIXED, quantity=7, price_pct=Decimal("99.123456789"), fx_rate=Decimal("1.0001"))
    result = calc_dv01(odd)
    dirty = Decimal(1000) * Decimal("99.123456789") / 100 + Decimal("12.5")
    cfv = 7 * dirty * Decimal("1.0001")
    assert result.current_fair_value == cfv
    assert result.dv01 == cfv * (-3 * BASIS_POINT + Decimal("0.5") * 12 * BASIS_POINT**2)


def test_dv01_is_linear_in_quantity():
    one = calc_dv01(replace(FIXED, quantity=1)).dv01
    assert calc_dv01(replace(FIXED, quantity=1000)).dv01 == one * 1000


# --- ошибки входов ------------------------------------------------------------------


@pytest.mark.parametrize("field", ["duration_mod", "convexity"])
@pytest.mark.parametrize("coupon_type", [CouponType.FIXED, CouponType.ZERO])
def test_missing_duration_or_convexity(field, coupon_type):
    data = replace(FIXED, coupon_type=coupon_type, **{field: None})
    with pytest.raises(CalculationInputError) as exc:
        calc_dv01(data)
    assert exc.value.code == RowErrorCode.MISSING_MARKET_DATA


def test_floating_without_frequency():
    with pytest.raises(CalculationInputError) as exc:
        calc_dv01(replace(FIXED, coupon_type=CouponType.FLOATING, coupon_frequency=None))
    assert exc.value.code == RowErrorCode.MISSING_MARKET_DATA


@pytest.mark.parametrize(
    "changes",
    [
        {"nominal": Decimal(0)},
        {"nominal": Decimal(-1000)},
        {"price_pct": Decimal(0)},
        {"price_pct": Decimal("-95")},
        {"fx_rate": Decimal(0)},
        {"accrued_interest": Decimal("-0.01")},
        {"duration_mod": Decimal("-3")},
        {"coupon_type": CouponType.FLOATING, "coupon_frequency": 0},
        {"coupon_type": CouponType.FLOATING, "coupon_frequency": -4},
    ],
)
def test_impossible_values(changes):
    with pytest.raises(CalculationInputError) as exc:
        calc_dv01(replace(FIXED, **changes))
    assert exc.value.code == RowErrorCode.INCOMPATIBLE_MARKET_DATA


def test_zero_quantity_breaks_contract():
    with pytest.raises(ValueError):
        calc_dv01(replace(FIXED, quantity=0))


# --- независимость от сети и БД ---------------------------------------------------------


def test_module_imports_only_arithmetic():
    """Ядро импортирует только decimal/typing и общие контракт и перечисления"""
    allowed = {"decimal", "typing", "app.contracts", "app.enums", "app.risk_core.dv01"}
    package = Path(__file__).resolve().parents[1] / "app" / "risk_core"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed, f"{path.name}: {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in allowed, f"{path.name}: {alias.name}"
