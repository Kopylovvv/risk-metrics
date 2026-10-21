"""Базовый класс моделей SQLAlchemy"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Одинаковые имена ограничений и индексов в моделях и миграциях (иначе Postgres даст свои)
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """От него наследуются все таблицы; Base.metadata использует Alembic"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
