"""Расчёт DV01 по набору и чтение сохранённого результата"""

from datetime import date

from fastapi import APIRouter, status

from app.api.convert import calculation_out
from app.api.deps import (
    Db,
    FxRates,
    MarketData,
    OwnCalculation,
    OwnWorkspaceForChange,
    RiskCalculator,
)
from app.errors import error_responses
from app.schemas import CalculationOut, CalculationRequest
from app.services import calculation as service

router = APIRouter(tags=["Расчёт DV01"])


@router.post(
    "/api/v1/workspaces/{workspace_id}/calculations",
    status_code=status.HTTP_201_CREATED,
    response_model=CalculationOut,
    summary="Рассчитать DV01 по набору и сохранить результат",
    description=(
        "Сделка без нужных данных получает `status = failed` и `error_code`, её числа `null`. "
        "`total_value` (сумма DV01 в рублях) заполнен только при `status = success`"
    ),
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def calculate(
    body: CalculationRequest,
    workspace: OwnWorkspaceForChange,
    db: Db,
    market: MarketData,
    fx: FxRates,
    calc: RiskCalculator,
):
    run_id = await service.run_calculation(
        db,
        workspace,
        body.expected_revision,
        body.valuation_date or date.today(),
        market,
        fx,
        calc,
    )
    run, results = await service.get_calculation(db, run_id)
    return calculation_out(run, results)


@router.get(
    "/api/v1/calculations/{calculation_id}",
    response_model=CalculationOut,
    summary="Сохранённый результат расчёта",
    description="Строки содержат входы на момент расчёта: правка сделки позже их не меняет",
    responses=error_responses(401, 404, 422, 503),
)
async def get_calculation(run: OwnCalculation, db: Db):
    _, results = await service.get_calculation(db, run.id)
    return calculation_out(run, results)
