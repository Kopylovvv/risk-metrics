"""Общие зависимости роутеров: БД, сессия по cookie, доступ к своему набору, CSRF"""

import hashlib
import hmac
import secrets
import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import contracts, errors
from app.config import allowed_origins, cbonds_credentials
from app.db import CalculationRun, Workspace
from app.db.session import get_session
from app.market_data import CbondsMarketData
from app.services.workspaces import hash_session_key
from app.stubs.risk_core import calc_dv01
from app.stubs.sources import NoFxRateSource, NoMarketDataSource

SESSION_COOKIE = "rm_session"
CSRF_HEADER = "X-CSRF-Token"

Db = Annotated[AsyncSession, Depends(get_session)]
# cookie не показываем в /docs: браузер сам отправляет её после POST /workspaces
SessionKey = Annotated[str | None, Cookie(alias=SESSION_COOKIE, include_in_schema=False)]


def new_session_key() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_for(session_key: str) -> str:
    """CSRF-токен выводится из ключа сессии: хранить его в БД не нужно

    Чужой сайт не может прочитать HttpOnly-cookie, значит, не может и посчитать токен
    """
    return hmac.new(session_key.encode(), b"csrf", hashlib.sha256).hexdigest()


def _belongs(workspace: Workspace | None, session_key: str) -> bool:
    """Набор существует и принадлежит этой сессии (сравнение за постоянное время)"""
    return workspace is not None and hmac.compare_digest(
        workspace.session_key_hash, hash_session_key(session_key)
    )


async def own_workspace(
    workspace_id: uuid.UUID, db: Db, session_key: SessionKey = None
) -> Workspace:
    """Набор из пути, только если он принадлежит сессии из cookie"""
    if not session_key:
        raise errors.session_required()
    workspace = await db.get(Workspace, workspace_id)
    if not _belongs(workspace, session_key):
        raise errors.workspace_not_found()
    # чтение закончено: не держим транзакцию открытой (дальше могут быть внешние запросы)
    await db.commit()
    return workspace


async def own_workspace_for_change(
    request: Request,
    workspace: Annotated[Workspace, Depends(own_workspace)],
    session_key: SessionKey = None,
    csrf_token: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
) -> Workspace:
    """Для изменяющих запросов: дополнительно Origin и X-CSRF-Token"""
    origin = request.headers.get("origin")
    if origin is not None and origin not in allowed_origins():
        raise errors.origin_not_allowed()
    if not csrf_token or not hmac.compare_digest(csrf_token, csrf_token_for(session_key or "")):
        raise errors.csrf_invalid()
    return workspace


async def own_calculation(
    calculation_id: uuid.UUID, db: Db, session_key: SessionKey = None
) -> CalculationRun:
    """Сохранённый расчёт, только если его набор принадлежит сессии из cookie"""
    if not session_key:
        raise errors.session_required()
    run = await db.get(CalculationRun, calculation_id)
    workspace = await db.get(Workspace, run.workspace_id) if run else None
    if not _belongs(workspace, session_key):
        raise errors.calculation_not_found()
    return run


# Точки подключения источников данных и расчётного ядра
# Сейчас временные реализации из app/stubs; адаптеры и risk_core подключаются здесь
def get_market_data() -> contracts.MarketDataProvider:
    credentials = cbonds_credentials()
    if credentials is None:  # нет доступа к Cbonds: расчёт берёт данные только из БД
        return NoMarketDataSource()
    return CbondsMarketData(*credentials)


def get_fx_rates() -> contracts.FxRateProvider:
    return NoFxRateSource()


def get_risk_calculator() -> contracts.RiskCalculator:
    return calc_dv01


OwnWorkspace = Annotated[Workspace, Depends(own_workspace)]
OwnWorkspaceForChange = Annotated[Workspace, Depends(own_workspace_for_change)]
OwnCalculation = Annotated[CalculationRun, Depends(own_calculation)]
MarketData = Annotated[contracts.MarketDataProvider, Depends(get_market_data)]
FxRates = Annotated[contracts.FxRateProvider, Depends(get_fx_rates)]
RiskCalculator = Annotated[contracts.RiskCalculator, Depends(get_risk_calculator)]
