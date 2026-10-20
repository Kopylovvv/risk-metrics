"""Таблицы БД по схеме v4. CHECK-ограничения закрепляют правила модели прямо в БД"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import CHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.contracts import CouponType, Provider
from app.db.base import Base
from app.schemas.calculations import RowStatus, RunStatus


def pg_enum(enum_cls: type[Enum], name: str) -> SAEnum:
    """Enum PostgreSQL, хранящий значения ('cbonds'), а не имена ('CBONDS')"""
    return SAEnum(
        enum_cls, name=name, values_callable=lambda e: [m.value for m in e], validate_strings=True
    )


class QuantityType(TypeDecorator):
    """numeric(20,0) в БД, int в Python"""

    impl = Numeric(20, 0)
    cache_ok = True

    def process_bind_param(self, value: int | None, dialect) -> Decimal | None:
        return None if value is None else Decimal(value)

    def process_result_value(self, value: Decimal | None, dialect) -> int | None:
        return None if value is None else int(value)


# Время ставит сама БД (now()), updated_at обновляется при изменении записи через ORM
def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Workspace(Base):
    """Набор гипотетических сделок одной анонимной сессии"""

    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint("revision >= 0", name="revision_non_negative"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # хэш ключа из cookie сессии; сам ключ в БД не храним
    session_key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # +1 при каждом изменении набора; по нему ловим конфликт 409
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    trades: Mapped[list["Trade"]] = relationship(back_populates="workspace")


class Instrument(Base):
    """Справочник облигаций"""

    __tablename__ = "instruments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    isin: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    instrument_type: Mapped[str] = mapped_column(String(24), nullable=False)  # например bond
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)  # валюта номинала, ISO 4217
    created_at: Mapped[datetime] = created_at_column()


class Trade(Base):
    """Гипотетическая сделка: ISIN и количество бумаг"""

    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        Index(None, "workspace_id"),
        Index(None, "instrument_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    # NULL, если бумага не найдена в источниках: сделка всё равно сохраняется
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instruments.id"))
    isin: Mapped[str] = mapped_column(String(12), nullable=False)  # как ввёл пользователь
    quantity: Mapped[int] = mapped_column(QuantityType, nullable=False)  # < 0 — шорт
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    workspace: Mapped[Workspace] = relationship(back_populates="trades")
    instrument: Mapped[Instrument | None] = relationship()


class MarketDataSnapshot(Base):
    """Рыночные параметры инструмента на дату от одного источника"""

    __tablename__ = "market_data_snapshots"
    __table_args__ = (
        # один снимок на инструмент, день и источник
        UniqueConstraint("instrument_id", "data_as_of", "provider", name="uq_snapshot_per_day"),
        CheckConstraint("nominal > 0", name="nominal_positive"),
        CheckConstraint("price_pct > 0", name="price_pct_positive"),
        CheckConstraint("coupon_frequency > 0", name="coupon_frequency_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    provider: Mapped[Provider] = mapped_column(pg_enum(Provider, "provider"), nullable=False)
    data_as_of: Mapped[date] = mapped_column(Date, nullable=False)  # на какую дату данные
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    # параметры могут прийти неполными: NULL, а при расчёте строка получит ошибку
    nominal: Mapped[Decimal | None] = mapped_column(Numeric)  # номинал одной бумаги
    price_pct: Mapped[Decimal | None] = mapped_column(Numeric)  # % от номинала
    accrued_interest: Mapped[Decimal | None] = mapped_column(Numeric)  # НКД, в CFV не входит
    duration_mod: Mapped[Decimal | None] = mapped_column(Numeric)  # модифицированная дюрация
    convexity: Mapped[Decimal | None] = mapped_column(Numeric)  # выпуклость
    coupon_type: Mapped[CouponType] = mapped_column(
        pg_enum(CouponType, "coupon_type"), nullable=False
    )
    coupon_frequency: Mapped[int | None] = mapped_column(Integer)  # выплат в год


class FxRate(Base):
    """Курс валюты к рублю на дату"""

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint(
            "currency", "rate_date", "source", name="uq_fx_rates_currency_rate_date_source"
        ),
        CheckConstraint("rate_to_rub > 0", name="rate_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate_to_rub: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)  # например cbr
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalculationRun(Base):
    """Один запуск расчёта по набору"""

    __tablename__ = "calculation_runs"
    __table_args__ = (
        # итог есть только у успешного расчёта, у partial/failed — NULL
        CheckConstraint(
            "(status = 'success') = (total_value IS NOT NULL)", name="total_only_on_success"
        ),
        Index(None, "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    workspace_revision: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # набор на момент расчёта
    measure_code: Mapped[str] = mapped_column(String(32), nullable=False)  # DV01
    methodology_version: Mapped[str] = mapped_column(String(32), nullable=False)  # dv01-v1
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RunStatus] = mapped_column(pg_enum(RunStatus, "run_status"), nullable=False)
    total_value: Mapped[Decimal | None] = mapped_column(Numeric)  # Σ metric_value, RUB
    created_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["CalculationResult"]] = relationship(back_populates="run")


class CalculationResult(Base):
    """Результат по одной сделке с копией входов и использованных параметров"""

    __tablename__ = "calculation_results"
    __table_args__ = (
        CheckConstraint("input_quantity <> 0", name="input_quantity_non_zero"),
        # success: есть CFV и значение, нет кода ошибки; failed: есть код, значение NULL (не 0)
        CheckConstraint(
            "(status = 'success' AND error_code IS NULL"
            " AND current_fair_value IS NOT NULL AND metric_value IS NOT NULL)"
            " OR (status = 'failed' AND error_code IS NOT NULL AND metric_value IS NULL)",
            name="status_matches_values",
        ),
        Index(None, "run_id"),
        Index(None, "trade_id"),
        Index(None, "market_data_snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calculation_runs.id"), nullable=False)
    # NULL после удаления сделки; история остаётся в input_*
    trade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"))
    market_data_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_data_snapshots.id")
    )
    fx_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fx_rates.id")
    )  # NULL для рублёвой бумаги
    # копия входов сделки на момент расчёта: старый результат не меняется задним числом
    input_isin: Mapped[str] = mapped_column(String(12), nullable=False)
    input_quantity: Mapped[int] = mapped_column(QuantityType, nullable=False)
    # параметры, фактически подставленные в формулу
    nominal_used: Mapped[Decimal | None] = mapped_column(Numeric)
    price_pct_used: Mapped[Decimal | None] = mapped_column(Numeric)
    fx_rate_used: Mapped[Decimal | None] = mapped_column(Numeric)
    current_fair_value: Mapped[Decimal | None] = mapped_column(Numeric)  # CFV, RUB
    duration_used: Mapped[Decimal | None] = mapped_column(Numeric)  # floating: 1 / частота
    convexity_used: Mapped[Decimal | None] = mapped_column(Numeric)  # floating: 0
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric)  # DV01, RUB
    status: Mapped[RowStatus] = mapped_column(pg_enum(RowStatus, "row_status"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))

    run: Mapped[CalculationRun] = relationship(back_populates="results")
