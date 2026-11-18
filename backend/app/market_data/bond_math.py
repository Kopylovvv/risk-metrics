"""Модифицированная дюрация и выпуклость облигации по графику выплат

Нужна адаптеру Мосбиржи: ISS отдаёт дюрацию Маколея в днях (DURATION) и доходность
(EFFECTIVEYIELD), а модифицированной дюрации и выпуклости в ISS нет

Потоки CF_i через d_i дней после даты расчётов, доходность y (доля), база года B:
  t_i = d_i / B,   v_i = CF_i / (1 + y)^t_i,   P = Σ v_i
  дюрация Маколея в днях:   Σ d_i v_i / P     (сверяется с DURATION из ISS)
  в годах:                  D_mac = Σ t_i v_i / P
  модифицированная, годы:   D_mod = D_mac / (1 + y)
  выпуклость, годы²:        C = Σ t_i (t_i + 1) v_i / P / (1 + y)²

База года. ISS дисконтирует (1 + y)^(дней/365) у ОФЗ и (1 + y)^(дней/360) у части валютных
выпусков. Сверено 15.11.2026 на итогах 13.11 (расчёты 16.11):
* ОФЗ 26238: при B = 365 цена по потокам 548.209, у ISS 51.592% × 1000 + НКД 32.29 = 548.21;
  дюрация 2447.2 дня, у ISS 2447
* ГазКЗ-34Д (номинал USD): при B = 360 цена 967.512, у ISS 96.32% × 1000 + 4.31 = 967.51;
  дюрация 2038.7 дня, у ISS 2038; при B = 365 было бы 974.283 и 2041.4
В ответе ISS базы нет. Берётся та, при которой цена по потокам ближе к грязной цене, по
которой биржа считала доходность (PRICE % номинала + НКД на дату расчётов): разница цен между
базами в несколько рублей, а по дюрации, округлённой биржей до дня, у коротких выпусков
базы не различить. Без цены база выбирается по близости дюрации к DURATION.
Затем дюрация по потокам сверяется с DURATION: не совпала, значит, потоки не те, по
которым считала биржа (оферта, неизвестные купоны, другой график), и результат None,
а не выдуманное число

Арифметика на Decimal; степень с дробным показателем Decimal считает с точностью контекста
(28 значащих цифр), отдельно ничего не округляется
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

YEAR_BASES = (365, 360)
# допуск сверки с DURATION из ISS: 2 дня или 0.1% (дата расчётов T+1 без учёта праздников)
DURATION_TOLERANCE_DAYS = Decimal(2)
DURATION_TOLERANCE_SHARE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class CashFlow:
    on: date
    amount: Decimal  # на одну бумагу, в валюте номинала


@dataclass(frozen=True, slots=True)
class BondRisk:
    duration_mod: Decimal  # годы
    convexity: Decimal  # годы²
    duration_days: Decimal  # дюрация Маколея в днях, для сверки с ISS
    present_value: Decimal  # грязная цена по потокам, на одну бумагу
    year_basis: int


def bond_risk(
    flows: Iterable[CashFlow], settle: date, yield_pct: Decimal, year_basis: int
) -> BondRisk | None:
    """Дюрация и выпуклость по потокам после даты расчётов; None, если считать не из чего"""
    y = yield_pct / 100
    if y <= -1:
        return None
    base = Decimal(year_basis)
    pv = weighted_days = weighted_years = weighted_convexity = Decimal(0)
    for flow in flows:
        days = (flow.on - settle).days
        if days <= 0 or flow.amount == 0:  # уже выплачено к дате расчётов
            continue
        t = Decimal(days) / base
        v = flow.amount / (1 + y) ** t
        pv += v
        weighted_days += days * v
        weighted_years += t * v
        weighted_convexity += t * (t + 1) * v
    if pv <= 0:
        return None
    return BondRisk(
        duration_mod=weighted_years / pv / (1 + y),
        convexity=weighted_convexity / pv / (1 + y) ** 2,
        duration_days=weighted_days / pv,
        present_value=pv,
        year_basis=year_basis,
    )


def bond_risk_matching(
    flows: Iterable[CashFlow],
    settle: date,
    yield_pct: Decimal,
    iss_duration_days: Decimal,
    iss_dirty_price: Decimal | None = None,
) -> BondRisk | None:
    """Дюрация и выпуклость при базе года биржи, сверенные с её дюрацией

    None: потоков нет или расхождение с DURATION из ISS больше допуска
    """
    flows = list(flows)
    candidates = [
        risk
        for base in YEAR_BASES
        if (risk := bond_risk(flows, settle, yield_pct, base)) is not None
    ]
    if not candidates or iss_duration_days <= 0:
        return None
    if iss_dirty_price is not None and iss_dirty_price > 0:
        best = min(candidates, key=lambda r: abs(r.present_value - iss_dirty_price))
    else:
        best = min(candidates, key=lambda r: abs(r.duration_days - iss_duration_days))
    tolerance = max(DURATION_TOLERANCE_DAYS, iss_duration_days * DURATION_TOLERANCE_SHARE)
    if abs(best.duration_days - iss_duration_days) > tolerance:
        return None
    return best
