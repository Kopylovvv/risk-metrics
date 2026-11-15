"""Запуск расчёта DV01 и результат"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.enums import Provider, RowErrorCode, RowStatus, RunStatus
from app.schemas.common import DecimalStr


class CalculationRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    valuation_date: date | None = Field(
        None, description="Дата оценки, не позже сегодняшней; по умолчанию сегодня"
    )

    @field_validator("valuation_date")
    @classmethod
    def _not_in_future(cls, value: date | None) -> date | None:
        # рыночных данных из будущего не бывает: такой расчёт ничего не значит
        if value is not None and value > date.today():
            raise ValueError("Дата оценки не может быть позже сегодняшней")
        return value

    model_config = {
        "json_schema_extra": {
            "examples": [{"expected_revision": 3, "valuation_date": "2026-10-12"}]
        }
    }


class CalculationRowOut(BaseModel):
    """Результат по одной сделке. При ошибке числовые поля = null, не 0"""

    trade_id: UUID | None = Field(description="null, если сделку уже удалили")
    isin: str
    quantity: int
    status: RowStatus
    error_code: RowErrorCode | None = None
    # происхождение данных: что и на какую дату подставлено в формулу
    provider: Provider | None = Field(
        None, description="Источник цены, НКД, дюрации: moex; test для контрольного набора"
    )
    data_as_of: date | None = Field(None, description="Дата торгов, к которой относятся данные")
    currency: str | None = Field(None, description="Валюта номинала, ISO 4217")
    settlement_currency: str | None = Field(
        None, description="Валюта расчётов по сделкам; может отличаться от валюты номинала"
    )
    fx_source: str | None = Field(
        None, description="Источник курса: cbr или demo; null для рублёвой бумаги (курс = 1)"
    )
    fx_rate_date: date | None = Field(
        None, description="Дата, на которую установлен курс; null для рублёвой бумаги"
    )
    nominal_used: DecimalStr | None = None
    price_pct_used: DecimalStr | None = Field(None, description="Чистая котировка, % от номинала")
    accrued_interest_used: DecimalStr | None = Field(
        None, description="НКД на одну бумагу в валюте номинала; входит в CFV"
    )
    fx_rate_used: DecimalStr | None = Field(
        None, description="Рублей за одну единицу валюты номинала; 1 для рублёвой бумаги"
    )
    current_fair_value: DecimalStr | None = Field(None, description="CFV по цене с НКД, RUB")
    duration_used: DecimalStr | None = None
    convexity_used: DecimalStr | None = None
    metric_value: DecimalStr | None = Field(None, description="Значение метрики (DV01), RUB")


class CalculationOut(BaseModel):
    id: UUID
    workspace_id: UUID
    workspace_revision: int
    measure_code: str = "DV01"
    methodology_version: str
    valuation_date: date
    status: RunStatus
    total_value: DecimalStr | None = Field(
        description="Σ metric_value по сделкам, RUB. Только при status = success, иначе null"
    )
    currency: str = "RUB"
    results: list[CalculationRowOut]
    created_at: datetime
    completed_at: datetime | None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "7a1c2f0e-1b7d-4c55-9a43-0f3f2d1e9b10",
                    "workspace_id": "3f9b8c2a-5d4e-4f1a-8b6c-2e7d9a0c1b23",
                    "workspace_revision": 3,
                    "measure_code": "DV01",
                    "methodology_version": "dv01-v2",
                    "valuation_date": "2026-10-12",
                    "status": "partial",
                    "total_value": None,
                    "currency": "RUB",
                    "results": [
                        {
                            "trade_id": "b2a4c6e8-0000-4000-8000-000000000001",
                            "isin": "RU000A1038V6",
                            "quantity": 100,
                            "status": "success",
                            "error_code": None,
                            "provider": "moex",
                            "data_as_of": "2026-10-12",
                            "currency": "RUB",
                            "settlement_currency": "RUB",
                            "fx_source": None,
                            "fx_rate_date": None,
                            "nominal_used": "1000",
                            "price_pct_used": "95",
                            "accrued_interest_used": "12.5",
                            "fx_rate_used": "1",
                            "current_fair_value": "96250",
                            "duration_used": "3",
                            "convexity_used": "12",
                            "metric_value": "-28.869225",
                        },
                        {
                            "trade_id": "b2a4c6e8-0000-4000-8000-000000000003",
                            "isin": "RU000A105A95",
                            "quantity": -10,
                            "status": "success",
                            "error_code": None,
                            "provider": "moex",
                            "data_as_of": "2026-10-12",
                            "currency": "USD",
                            "settlement_currency": "RUB",
                            "fx_source": "cbr",
                            "fx_rate_date": "2026-10-12",
                            "nominal_used": "1000",
                            "price_pct_used": "98.5",
                            "accrued_interest_used": "10.25",
                            "fx_rate_used": "92.5",
                            "current_fair_value": "-920606.25",
                            "duration_used": "4.2",
                            "convexity_used": "25",
                            "metric_value": "386.53954921875",
                        },
                        {
                            "trade_id": "b2a4c6e8-0000-4000-8000-000000000002",
                            "isin": "RU000A0JX0J2",
                            "quantity": 50,
                            "status": "failed",
                            "error_code": "MISSING_MARKET_DATA",
                            "provider": None,
                            "data_as_of": None,
                            "currency": None,
                            "settlement_currency": None,
                            "fx_source": None,
                            "fx_rate_date": None,
                            "nominal_used": None,
                            "price_pct_used": None,
                            "accrued_interest_used": None,
                            "fx_rate_used": None,
                            "current_fair_value": None,
                            "duration_used": None,
                            "convexity_used": None,
                            "metric_value": None,
                        },
                    ],
                    "created_at": "2026-10-12T10:00:00Z",
                    "completed_at": "2026-10-12T10:00:01Z",
                }
            ]
        }
    }
