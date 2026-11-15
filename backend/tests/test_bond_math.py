"""Дюрация и выпуклость по графику выплат (bond_math) и единицы котировки (units)

Ожидаемые числа посчитаны вручную по формулам из докстринга bond_math.py
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.contracts import Dv01Input, MarketDataError
from app.enums import CouponType, RowErrorCode
from app.market_data.bond_math import CashFlow, bond_risk, bond_risk_matching
from app.market_data.units import PRICE_FRACTION, PRICE_PERCENT, price_to_pct
from app.risk_core import calc_dv01

SETTLE = date(2026, 11, 16)


def q12(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1e-12"))


def test_zero_coupon_two_years():
    """Один поток через 730 дней (2 года при базе 365), y = 10%:
    D_mod = 2 / 1.1 = 1.818181..., C = 2 × 3 / 1.1² = 4.958677..., P = 1000 / 1.21"""
    flows = [CashFlow(SETTLE + timedelta(days=730), Decimal(1000))]
    risk = bond_risk(flows, SETTLE, Decimal(10), 365)
    assert q12(risk.duration_mod) == Decimal("1.818181818182")
    assert q12(risk.convexity) == Decimal("4.958677685950")
    assert q12(risk.present_value) == Decimal("826.446280991736")
    assert q12(risk.duration_days) == Decimal(730)


def test_year_basis_changes_time_in_years():
    """Тот же поток через 720 дней при базе 360 тоже 2 года: те же D_mod и C"""
    flows = [CashFlow(SETTLE + timedelta(days=720), Decimal(1000))]
    risk = bond_risk(flows, SETTLE, Decimal(10), 360)
    assert q12(risk.duration_mod) == Decimal("1.818181818182")
    assert risk.year_basis == 360


def test_flows_on_or_before_settlement_are_ignored():
    flows = [
        CashFlow(SETTLE - timedelta(days=1), Decimal(35)),
        CashFlow(SETTLE, Decimal(35)),
        CashFlow(SETTLE + timedelta(days=730), Decimal(1000)),
    ]
    assert q12(bond_risk(flows, SETTLE, Decimal(10), 365).duration_mod) == Decimal("1.818181818182")


@pytest.mark.parametrize(
    ("flows", "yield_pct"),
    [
        ([], Decimal(10)),
        ([CashFlow(SETTLE - timedelta(days=10), Decimal(1000))], Decimal(10)),
        ([CashFlow(SETTLE + timedelta(days=365), Decimal(1000))], Decimal(-100)),
    ],
)
def test_nothing_to_compute_gives_none(flows, yield_pct):
    assert bond_risk(flows, SETTLE, yield_pct, 365) is None
    assert bond_risk_matching(flows, SETTLE, yield_pct, Decimal(365)) is None


def two_year_annual_bond() -> list[CashFlow]:
    """Купон 100 через год и 100 + 1000 через два года"""
    return [
        CashFlow(SETTLE + timedelta(days=365), Decimal(100)),
        CashFlow(SETTLE + timedelta(days=730), Decimal(1100)),
    ]


def test_matching_picks_basis_by_dirty_price():
    flows = two_year_annual_bond()
    at_365 = bond_risk(flows, SETTLE, Decimal(10), 365)
    at_360 = bond_risk(flows, SETTLE, Decimal(10), 360)
    assert q12(at_365.present_value) == Decimal("1000.000000000000")  # купон = доходности
    days = at_365.duration_days.to_integral_value()
    by_price_360 = bond_risk_matching(flows, SETTLE, Decimal(10), days, at_360.present_value)
    by_price_365 = bond_risk_matching(flows, SETTLE, Decimal(10), days, Decimal(1000))
    assert (by_price_360.year_basis, by_price_365.year_basis) == (360, 365)


def test_matching_without_price_picks_basis_by_duration():
    flows = two_year_annual_bond()
    at_360 = bond_risk(flows, SETTLE, Decimal(10), 360)
    risk = bond_risk_matching(flows, SETTLE, Decimal(10), at_360.duration_days)
    assert risk.year_basis == 360


def test_duration_far_from_iss_gives_none():
    """Дюрация по потокам 696.7 дня; биржа насчитала 600: потоки не те, числа не выдумываем"""
    flows = two_year_annual_bond()
    assert bond_risk_matching(flows, SETTLE, Decimal(10), Decimal(600), Decimal(1000)) is None
    # в пределах допуска: 2 дня
    days = bond_risk(flows, SETTLE, Decimal(10), 365).duration_days
    assert bond_risk_matching(flows, SETTLE, Decimal(10), days + 2, Decimal(1000)) is not None
    assert bond_risk_matching(flows, SETTLE, Decimal(10), days + 3, Decimal(1000)) is None


# --- единицы котировки ---------------------------------------------------------------------


def test_percent_and_fraction_give_same_price_and_dv01():
    """95% номинала: 95 в процентах и 0.95 в долях. После нормализации одно и то же,
    деление на 100 делает только risk_core: 100 × (1000 × 0.95 + 12.5) = 96 250"""
    as_percent = price_to_pct(Decimal(95), PRICE_PERCENT)
    as_fraction = price_to_pct(Decimal("0.95"), PRICE_FRACTION)
    assert as_percent == as_fraction == Decimal(95)

    def dv01(price_pct: Decimal):
        return calc_dv01(
            Dv01Input(
                quantity=100,
                nominal=Decimal(1000),
                price_pct=price_pct,
                accrued_interest=Decimal("12.5"),
                fx_rate=Decimal(1),
                duration_mod=Decimal(3),
                convexity=Decimal(12),
                coupon_type=CouponType.FIXED,
                coupon_frequency=2,
            )
        )

    a, b = dv01(as_percent), dv01(as_fraction)
    assert a.current_fair_value == b.current_fair_value == Decimal(96250)
    assert a.dv01 == b.dv01 == Decimal("-28.869225")


def test_unknown_price_unit_is_incompatible():
    with pytest.raises(MarketDataError) as exc:
        price_to_pct(Decimal(95), "basis_points")
    assert exc.value.code == RowErrorCode.INCOMPATIBLE_MARKET_DATA
