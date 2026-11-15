"""Расчёт DV01 по набору и чтение сохранённого результата"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import AfterValidator

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
from app.schemas import CalculationOut, CalculationRequest, SourceDiffOut
from app.services import calculation as service
from app.services.source_diff import compare_with_source

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


def _not_in_future(value: date | None) -> date | None:
    if value is not None and value > date.today():
        raise ValueError("Дата не может быть позже сегодняшней")
    return value


AsOf = Annotated[
    date | None,
    Query(description="На какую дату спросить источник; по умолчанию сегодня"),
    AfterValidator(_not_in_future),
]


@router.get(
    "/api/v1/calculations/{calculation_id}/source-diff",
    response_model=SourceDiffOut,
    summary="Сохранённые данные расчёта и новый ответ источника",
    description=(
        "Для каждой бумаги: снимок и курс, по которым считали, рядом с тем, что источник "
        "отвечает сейчас на дату `as_of`. Если источник не ответил, справа последнее "
        "сохранённое в БД (`current_origin = db`). `changed` перечисляет отличающиеся поля. "
        "Ничего не сохраняет: сохранённый расчёт не меняется"
    ),
    responses=error_responses(401, 404, 422, 503),
)
async def source_diff(
    run: OwnCalculation, db: Db, market: MarketData, fx: FxRates, as_of: AsOf = None
):
    return await compare_with_source(db, run, as_of or date.today(), market, fx)
