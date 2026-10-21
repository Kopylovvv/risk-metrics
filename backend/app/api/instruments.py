"""Карточка облигации по ISIN; пока только из БД, без запроса к Cbonds/RuData"""

from fastapi import APIRouter
from pydantic import TypeAdapter, ValidationError

from app import errors
from app.api.convert import instrument_out
from app.api.deps import Db
from app.errors import error_responses
from app.schemas import InstrumentOut, Isin
from app.services.workspaces import find_instrument, latest_snapshots

router = APIRouter(prefix="/api/v1/instruments", tags=["Инструменты"])

# та же проверка ISIN, что и в теле запроса сделки
_isin = TypeAdapter(Isin)


@router.get(
    "/{isin}",
    response_model=InstrumentOut,
    summary="Найти облигацию по ISIN",
    responses=error_responses(404, 422, 503),
)
async def get_instrument(isin: str, db: Db):
    try:
        isin = _isin.validate_python(isin)
    except ValidationError as exc:
        message = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise errors.validation_error(message, field="path.isin") from exc
    instrument = await find_instrument(db, isin)
    if instrument is None:
        raise errors.instrument_not_found()
    snapshots = await latest_snapshots(db, [instrument.id])
    return instrument_out(instrument, snapshots.get(instrument.id))
