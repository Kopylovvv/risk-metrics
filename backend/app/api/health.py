"""Служебные проверки для Docker и мониторинга"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import Db

router = APIRouter(prefix="/health", tags=["Служебное"])


@router.get("/live", summary="Процесс жив")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Готов к работе: база доступна")
async def ready(db: Db):
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})
    return {"status": "ok", "database": "ok"}
