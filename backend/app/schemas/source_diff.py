"""Сохранённые данные расчёта рядом с новым ответом источника"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import CouponType, Provider, RowErrorCode
from app.schemas.common import DecimalStr

Origin = Literal["source", "db"]


class SnapshotData(BaseModel):
    """Параметры облигации от одного источника на дату"""

    provider: Provider
    data_as_of: date = Field(description="Дата торгов, к которой относятся данные")
    retrieved_at: datetime = Field(description="Когда данные получены от источника")
    currency: str = Field(description="Валюта номинала")
    settlement_currency: str | None = Field(None, description="Валюта расчётов по сделкам")
    nominal: DecimalStr | None
    price_pct: DecimalStr | None = Field(description="Чистая котировка, % от номинала")
    accrued_interest: DecimalStr | None = Field(description="НКД на бумагу в валюте номинала")
    duration_mod: DecimalStr | None
    convexity: DecimalStr | None
    coupon_type: CouponType
    coupon_frequency: int | None


class FxData(BaseModel):
    """Курс валюты номинала к рублю"""

    source: str = Field(description="cbr или demo")
    rate_date: date = Field(description="Дата, на которую установлен курс")
    rate_to_rub: DecimalStr = Field(description="Рублей за одну единицу валюты")


class SourceDiffRow(BaseModel):
    """Одна бумага расчёта: что сохранено и что источник отвечает сейчас"""

    isin: str
    saved: SnapshotData | None = Field(description="Снимок, по которому считали; null при ошибке")
    current: SnapshotData | None = Field(description="Новый ответ; null, если его нет")
    current_origin: Origin | None = Field(
        None,
        description="source: ответ источника сейчас; db: источник не ответил, "
        "взят последний сохранённый снимок",
    )
    current_error: RowErrorCode | None = Field(None, description="Почему источник не ответил")
    saved_fx: FxData | None = Field(None, description="Курс расчёта; null для рублёвой бумаги")
    current_fx: FxData | None = None
    current_fx_origin: Origin | None = None
    current_fx_error: RowErrorCode | None = None
    changed: list[str] = Field(
        description="Поля, которые отличаются; курс с префиксом fx_ (fx_rate_to_rub). "
        "Время получения retrieved_at не сравнивается"
    )


class SourceDiffOut(BaseModel):
    calculation_id: UUID
    valuation_date: date = Field(description="Дата оценки сохранённого расчёта")
    compared_as_of: date = Field(description="На какую дату запрошен новый ответ")
    rows: list[SourceDiffRow]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "calculation_id": "7a1c2f0e-1b7d-4c55-9a43-0f3f2d1e9b10",
                    "valuation_date": "2026-10-12",
                    "compared_as_of": "2026-10-13",
                    "rows": [
                        {
                            "isin": "RU000A105A95",
                            "saved": {
                                "provider": "moex",
                                "data_as_of": "2026-10-12",
                                "retrieved_at": "2026-10-12T10:00:00Z",
                                "currency": "USD",
                                "settlement_currency": "RUB",
                                "nominal": "1000",
                                "price_pct": "98.5",
                                "accrued_interest": "10.25",
                                "duration_mod": "4.2",
                                "convexity": "25",
                                "coupon_type": "fixed",
                                "coupon_frequency": 2,
                            },
                            "current": {
                                "provider": "moex",
                                "data_as_of": "2026-10-13",
                                "retrieved_at": "2026-10-13T09:00:00Z",
                                "currency": "USD",
                                "settlement_currency": "RUB",
                                "nominal": "1000",
                                "price_pct": "99.1",
                                "accrued_interest": "10.5",
                                "duration_mod": "4.2",
                                "convexity": "25",
                                "coupon_type": "fixed",
                                "coupon_frequency": 2,
                            },
                            "current_origin": "source",
                            "current_error": None,
                            "saved_fx": {
                                "source": "cbr",
                                "rate_date": "2026-10-12",
                                "rate_to_rub": "92.5",
                            },
                            "current_fx": {
                                "source": "cbr",
                                "rate_date": "2026-10-13",
                                "rate_to_rub": "93.1",
                            },
                            "current_fx_origin": "source",
                            "current_fx_error": None,
                            "changed": [
                                "data_as_of",
                                "price_pct",
                                "accrued_interest",
                                "fx_rate_date",
                                "fx_rate_to_rub",
                            ],
                        }
                    ],
                }
            ]
        }
    }
