"""Сделки и набор сделок (workspaces, trades)"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import Isin, Quantity
from app.schemas.instruments import InstrumentOut


class TradeCreate(BaseModel):
    """Добавить одну сделку в набор"""

    isin: Isin
    quantity: Quantity
    expected_revision: int = Field(ge=0, description="revision набора, который видит клиент")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"isin": "RU000A1038V6", "quantity": 100, "expected_revision": 0},
                {"isin": "RU000A1038V6", "quantity": -100, "expected_revision": 1},
            ]
        }
    }


class TradeUpdate(BaseModel):
    """Изменить количество бумаг или ISIN. Передаются только меняемые поля"""

    isin: Isin | None = None
    quantity: Quantity | None = None
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def _something_to_change(self) -> "TradeUpdate":
        if self.isin is None and self.quantity is None:
            raise ValueError("Укажите isin и/или quantity")
        return self

    model_config = {"json_schema_extra": {"examples": [{"quantity": 250, "expected_revision": 1}]}}


class TradeOut(BaseModel):
    id: UUID
    isin: str
    quantity: int
    instrument: InstrumentOut | None = Field(
        None, description="null, если инструмент не найден ни в одном источнике"
    )
    created_at: datetime
    updated_at: datetime


class WorkspaceOut(BaseModel):
    """Набор сделок текущей сессии. Возвращается после любого изменения набора"""

    id: UUID
    revision: int = Field(description="Растёт на 1 при каждом изменении набора")
    trades: list[TradeOut]
    last_calculation_id: UUID | None = Field(
        None,
        description=(
            "Последний расчёт этого набора (GET /api/v1/calculations/{id}); null, если не считали."
            " Нужен, чтобы после перезагрузки страницы показать результат"
        ),
    )
    created_at: datetime
    updated_at: datetime


class WorkspaceCreated(WorkspaceOut):
    csrf_token: str = Field(
        description="Передавать в заголовке X-CSRF-Token во всех изменяющих запросах"
    )
