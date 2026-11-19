"""История расчётов набора и восстановление состава из прошлого расчёта"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import RunStatus
from app.schemas.common import DecimalStr


class CalculationSummaryOut(BaseModel):
    """Один расчёт в списке истории; полный результат: GET /api/v1/calculations/{id}"""

    id: UUID
    workspace_revision: int = Field(description="revision набора, для которого посчитан расчёт")
    valuation_date: date
    status: RunStatus
    total_value: DecimalStr | None = Field(
        description="Σ DV01, RUB; только при status = success, иначе null"
    )
    currency: str = "RUB"
    methodology_version: str
    positions: int = Field(description="Сколько сделок было в расчёте")
    failed_positions: int = Field(description="Сколько из них с ошибкой")
    is_current: bool = Field(
        description="true: набор не меняли после этого расчёта (revision совпадает)"
    )
    created_at: datetime
    completed_at: datetime | None


class CalculationListOut(BaseModel):
    items: list[CalculationSummaryOut] = Field(description="Новые сверху")
    total: int = Field(description="Всего расчётов у набора")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "items": [
                        {
                            "id": "7a1c2f0e-1b7d-4c55-9a43-0f3f2d1e9b10",
                            "workspace_revision": 4,
                            "valuation_date": "2026-11-13",
                            "status": "success",
                            "total_value": "383.4813548557",
                            "currency": "RUB",
                            "methodology_version": "dv01-v2",
                            "positions": 2,
                            "failed_positions": 0,
                            "is_current": True,
                            "created_at": "2026-11-19T13:14:00Z",
                            "completed_at": "2026-11-19T13:14:01Z",
                        }
                    ],
                    "total": 3,
                }
            ]
        }
    }


class RestoreRequest(BaseModel):
    """Взять прошлый расчёт за основу: сделки набора заменяются его входами"""

    calculation_id: UUID = Field(description="Расчёт этого же набора")
    expected_revision: int = Field(ge=0, description="revision набора, который видит клиент")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"calculation_id": "7a1c2f0e-1b7d-4c55-9a43-0f3f2d1e9b10", "expected_revision": 5}
            ]
        }
    }
