"""Расчёт DV01 по набору и чтение сохранённого результата"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import AfterValidator

from app.api.convert import calculation_out, workspace_out
from app.api.deps import (
    Db,
    FxRates,
    MarketData,
    OwnCalculation,
    OwnWorkspace,
    OwnWorkspaceForChange,
    RiskCalculator,
)
from app.errors import error_responses
from app.schemas import (
    CalculationListOut,
    CalculationOut,
    CalculationRequest,
    CalculationSummaryOut,
    RestoreRequest,
    SourceDiffOut,
    WorkspaceOut,
)
from app.services import calculation as service
from app.services import history
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
    summary="Сравнить сохранённый расчёт с данными на сегодня",
    description=(
        "Для каждой бумаги: снимок и курс, по которым считали, рядом с тем, что источник "
        "отвечает сейчас на дату `as_of`. Если источник не ответил, справа последнее "
        "сохранённое в БД (`current_origin = db`). `changed` перечисляет отличающиеся поля. "
        "`saved_dv01` и `current_dv01`: DV01 того же количества по старым и новым данным, "
        "`dv01_change` их разница; то же для итога (`saved_total`, `current_total`, "
        "`total_change`). Ничего не сохраняет: сохранённый расчёт и история не меняются"
    ),
    responses=error_responses(401, 404, 422, 503),
)
async def source_diff(
    run: OwnCalculation,
    db: Db,
    market: MarketData,
    fx: FxRates,
    calc: RiskCalculator,
    as_of: AsOf = None,
):
    return await compare_with_source(db, run, as_of or date.today(), market, fx, calc)


@router.get(
    "/api/v1/workspaces/{workspace_id}/calculations",
    response_model=CalculationListOut,
    summary="История расчётов набора",
    description=(
        "Расчёты набора, новые сверху: дата оценки, статус, итог, число позиций и ошибок. "
        "`is_current = true`, если набор не меняли после расчёта. Полный результат: "
        "`GET /api/v1/calculations/{id}`. Сохранённые расчёты не меняются"
    ),
    responses=error_responses(401, 404, 422, 503),
)
async def list_calculations(
    workspace: OwnWorkspace,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100, description="Сколько расчётов вернуть")] = 20,
    offset: Annotated[int, Query(ge=0, description="Сколько самых новых пропустить")] = 0,
):
    items, total = await history.list_calculations(db, workspace, limit, offset)
    return CalculationListOut(
        items=[
            CalculationSummaryOut(
                id=i["run"].id,
                workspace_revision=i["run"].workspace_revision,
                valuation_date=i["run"].valuation_date,
                status=i["run"].status,
                total_value=i["run"].total_value,
                methodology_version=i["run"].methodology_version,
                positions=i["positions"],
                failed_positions=i["failed_positions"],
                is_current=i["is_current"],
                created_at=i["run"].created_at,
                completed_at=i["run"].completed_at,
            )
            for i in items
        ],
        total=total,
    )


@router.post(
    "/api/v1/workspaces/{workspace_id}/restore",
    response_model=WorkspaceOut,
    summary="Взять прошлый расчёт за основу",
    description=(
        "Сделки набора заменяются входами выбранного расчёта (ISIN и количество) одной "
        "транзакцией, revision растёт на 1. Сам расчёт и вся история не меняются; чтобы "
        "получить результат по восстановленному составу, нужен новый расчёт"
    ),
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def restore(body: RestoreRequest, workspace: OwnWorkspaceForChange, db: Db):
    await history.restore_from_calculation(
        db, workspace, body.calculation_id, body.expected_revision
    )
    return await workspace_out(db, workspace)
