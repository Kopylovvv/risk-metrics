# Образ backend. Стадии:
#   base:    Python 3.13, зависимости, код и миграции
#   test:    base + pytest и тесты; собирается только с --target test
#   runtime: рабочий образ (последняя стадия, собирается по умолчанию)

FROM python:3.13-slim AS base

# не писать .pyc и сразу выводить логи
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# сначала только зависимости: этот слой кэшируется, пока requirements.txt не меняется
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# запуск не от root
RUN useradd --create-home --uid 1000 appuser


# Тесты: запускаются при старте контейнера, нужна запущенная база
FROM base AS test

COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests

USER appuser
# без кэша pytest: у appuser нет прав на запись в /app
CMD ["pytest", "-p", "no:cacheprovider"]


# Рабочий образ
FROM base AS runtime

USER appuser

EXPOSE 8000

# API; миграции запускаются отдельной командой: docker run ... risk-metrics-backend alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
