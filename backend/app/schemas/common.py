"""Общие типы API: ISIN, деньги, формат ошибки"""

import re
from decimal import Decimal
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def isin_check_digit_ok(isin: str) -> bool:
    """Контрольная цифра ISIN: буквы -> числа (A=10 … Z=35), затем алгоритм Луна"""
    digits = "".join(str(int(ch, 36)) for ch in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(isin[-1])


def _normalize_isin(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def _validate_isin(value: str) -> str:
    if not _ISIN_RE.match(value):
        raise ValueError("ISIN: 12 символов (2 буквы страны, 9 букв или цифр, контрольная цифра)")
    if not isin_check_digit_ok(value):
        raise ValueError("ISIN: неверная контрольная цифра")
    return value


Isin = Annotated[
    str,
    BeforeValidator(_normalize_isin),
    AfterValidator(_validate_isin),
    Field(examples=["RU000A1038V6"]),
]

# Деньги и коэффициенты в JSON передаются десятичной строкой, без потери точности
DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "decimal", "examples": ["-28.4943"]}),
]


def _non_zero(value: int) -> int:
    if value == 0:
        raise ValueError("Количество бумаг не может быть 0")
    return value


# Предел точных целых в JavaScript ~9·10^15; в БД numeric(20,0) с запасом
QUANTITY_LIMIT = 10**15

Quantity = Annotated[
    int,
    AfterValidator(_non_zero),
    Field(
        strict=True,
        ge=-QUANTITY_LIMIT,
        le=QUANTITY_LIMIT,
        description="Количество бумаг, целое, ≠ 0; отрицательное означает короткую позицию (шорт)",
        examples=[100, -100],
    ),
]


class ErrorDetail(BaseModel):
    field: str | None = Field(None, description="Путь к полю, например body.isin")
    message: str


class ErrorOut(BaseModel):
    """Единый формат ошибки для всех кодов 4xx/5xx"""

    code: str = Field(description="Машинный код ошибки", examples=["REVISION_CONFLICT"])
    message: str = Field(description="Сообщение для пользователя")
    details: list[ErrorDetail] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "code": "REVISION_CONFLICT",
                    "message": "Набор сделок изменился. Обновите данные и повторите.",
                    "details": [],
                }
            ]
        }
    }
