"""Общие значения предметной области для БД, API и контракта; сам модуль ни от чего не зависит"""

from enum import StrEnum


class Provider(StrEnum):
    """Источник рыночных данных (enum provider в БД)"""

    CBONDS = "cbonds"
    RUDATA = "rudata"
    TEST = "test"  # контрольный набор, только для тестового окружения


class CouponType(StrEnum):
    """Тип купона (enum coupon_type в БД)"""

    FIXED = "fixed"
    FLOATING = "floating"  # Duration = 1 / coupon_frequency, Convexity = 0
    ZERO = "zero"


class RunStatus(StrEnum):
    """Статус расчёта по набору (enum run_status в БД)"""

    SUCCESS = "success"  # все строки посчитаны, есть итог
    PARTIAL = "partial"  # часть строк с ошибкой, итога нет
    FAILED = "failed"  # ни одна строка не посчитана, итога нет


class RowStatus(StrEnum):
    """Статус расчёта одной сделки (enum row_status в БД)"""

    SUCCESS = "success"
    FAILED = "failed"


class RowErrorCode(StrEnum):
    """Коды ошибок по одной сделке (calculation_results.error_code)"""

    INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INCOMPATIBLE_MARKET_DATA = "INCOMPATIBLE_MARKET_DATA"
    MISSING_FX_RATE = "MISSING_FX_RATE"
