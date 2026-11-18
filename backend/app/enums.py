"""Общие значения предметной области для БД, API и контракта; сам модуль ни от чего не зависит"""

from enum import StrEnum


class Provider(StrEnum):
    """Источник рыночных данных (enum provider в БД)"""

    MOEX = "moex"  # Московская биржа: источник параметров облигаций для новых расчётов
    TEST = "test"  # контрольный набор, только для тестового окружения
    # больше не используются в расчёте, остаются для чтения сохранённых снимков
    CBONDS = "cbonds"
    RUDATA = "rudata"


# Источники для новых расчётов в порядке предпочтения: снимки Cbonds и RuData не берутся
ACTIVE_PROVIDERS = (Provider.MOEX, Provider.TEST)


class FxSource(StrEnum):
    """Источник курса валюты (fx_rates.source)"""

    CBR = "cbr"  # официальный курс ЦБ РФ
    DEMO = "demo"  # демо-данные


# Источники курсов в порядке предпочтения
ACTIVE_FX_SOURCES = (FxSource.CBR, FxSource.DEMO)


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
    MISSING_FX_RATE = "MISSING_FX_RATE"  # источник курсов ответил, но курса нет
    FX_PROVIDER_UNAVAILABLE = "FX_PROVIDER_UNAVAILABLE"  # источник курсов не ответил
