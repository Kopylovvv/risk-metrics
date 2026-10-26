"""Расчётное ядро риск-метрик (Андрей)

Только арифметика на Decimal: без сети, БД, текущей даты и глобального состояния.
Входы и выходы описаны в app/contracts.py; подключается в app/api/deps.py
"""

from app.risk_core.dv01 import BASIS_POINT, calc_dv01

__all__ = ["BASIS_POINT", "calc_dv01"]
