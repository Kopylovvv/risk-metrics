"""Карточка инструмента (показывается после ввода ISIN)"""

from datetime import date

from pydantic import BaseModel, Field

from app.enums import CouponType, Provider
from app.schemas.common import DecimalStr


class InstrumentOut(BaseModel):
    isin: str
    name: str | None
    instrument_type: str
    currency: str = Field(description="Валюта номинала, ISO 4217")
    nominal: DecimalStr | None = Field(None, description="Номинал одной бумаги")
    price_pct: DecimalStr | None = Field(None, description="Котировка, % от номинала")
    coupon_type: CouponType | None = None
    coupon_frequency: int | None = Field(None, description="Купонных выплат в год")
    data_as_of: date | None = Field(None, description="Дата рыночных данных")
    provider: Provider | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "isin": "RU000A1038V6",
                    "name": "ДЕМО ОФЗ, фиксированный купон",
                    "instrument_type": "bond",
                    "currency": "RUB",
                    "nominal": "1000",
                    "price_pct": "95",
                    "coupon_type": "fixed",
                    "coupon_frequency": 2,
                    "data_as_of": "2026-10-12",
                    "provider": "cbonds",
                }
            ]
        }
    }
