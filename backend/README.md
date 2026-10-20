# Backend — модуль расчёта DV01

Этап: модели API, контракт с модулями данных и расчёта, схема БД с миграциями. Роутеров пока нет

## Структура

```
app/
  config.py        адрес PostgreSQL из окружения / .env в корне репозитория
  contracts.py     общий интерфейс backend ↔ market_data / risk_core (согласовать с Андреем)
  schemas/         Pydantic-модели запросов и ответов API
  db/
    base.py        Base и правила именования ограничений
    models.py      7 таблиц схемы v4 (SQLAlchemy 2)
    session.py     асинхронное подключение приложения (psycopg 3), get_session
migrations/        Alembic: env.py и версии схемы
  versions/20261020_0001_initial_schema_v4.py
tests/             проверки схемы и ограничений на настоящем PostgreSQL
alembic.ini
```

## Запуск БД и миграций

1. Поднять PostgreSQL из `compose.yaml` в корне репозитория (ветка Александра
   `infra/docker-environment`): `cp .env.example .env`, заполнить пароль,
   `docker compose up -d --wait`. База доступна на `localhost:5433`
2. Установить зависимости и применить миграции (из папки `backend`):

```bash
python -m venv .venv && .venv\Scripts\activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
```

Адрес БД: `DATABASE_URL` (например `postgresql+psycopg://user:pass@localhost:5433/risk_metrics`)
или переменные `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`
(по умолчанию `localhost`), `POSTGRES_PORT` (по умолчанию `5433`). Файл `.env` из корня
репозитория подхватывается автоматически

Полезные команды: `alembic current`, `alembic downgrade base`, `alembic upgrade head --sql`
(показать SQL без подключения), `alembic check` (модели совпадают с миграциями)

## Docker-образ backend

`Dockerfile` в папке `backend` собирает образ с кодом и миграциями (Python 3.13).
Сервис в `compose.yaml` пока не добавлен — это задача окружения

```bash
# из корня репозитория
docker build -t risk-metrics-backend backend
# применить миграции к базе из compose (она проброшена на порт 5433 компьютера)
docker run --rm --env-file .env -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT=5433 risk-metrics-backend
```

Тесты в контейнере (стадия `test`, база из compose должна быть запущена):

```bash
docker build --target test -t risk-metrics-backend-test backend
docker run --rm --env-file .env -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT=5433 risk-metrics-backend-test
```

Обычная сборка без `--target` стадию `test` пропускает: в рабочий образ тесты и pytest не попадают

Внутри сети compose адрес базы будет `POSTGRES_HOST=db`, `POSTGRES_PORT=5432`.
Пока нет `app/main.py`, команда образа по умолчанию — `alembic upgrade head`

## Тесты

```bash
pytest
```

Тесты создают отдельную пустую базу `<POSTGRES_DB>_test`, ставят в неё схему миграциями
и удаляют после прогона. Пользователю БД нужно право CREATE DATABASE (у пользователя из
compose оно есть). Проверяется: схема и enum-ы, совпадение моделей и миграций,
downgrade/upgrade, точное сохранение входов и результатов, все ограничения ниже

## Схема БД (v4) и ограничения

Таблицы: `workspaces`, `instruments`, `trades`, `market_data_snapshots`, `fx_rates`,
`calculation_runs`, `calculation_results`. Enum-ы: `provider`, `coupon_type`,
`run_status`, `row_status`

- Уникальность: `instruments.isin`, `workspaces.session_key_hash`,
  снимок `(instrument_id, data_as_of, provider)` — `uq_snapshot_per_day`,
  курс `(currency, rate_date, source)`
- Внешние ключи по схеме; `calculation_results.trade_id` — `ON DELETE SET NULL`
  (история расчёта остаётся в `input_*`)
- CHECK сверх DBML (правила модели):
  - `trades.quantity <> 0`, `calculation_results.input_quantity <> 0`
  - `workspaces.revision >= 0`
  - `nominal > 0`, `price_pct > 0`, `coupon_frequency > 0`, `rate_to_rub > 0`
  - `calculation_runs`: `total_value` заполнен тогда и только тогда, когда `status = success`
  - `calculation_results`: `success` → есть CFV и `metric_value`, нет `error_code`;
    `failed` → есть `error_code`, `metric_value IS NULL` (не 0)
- Деньги и коэффициенты — `NUMERIC` без ограничения точности, количество — `numeric(20,0)`

## Правила моделей API

- `quantity` — целое ≠ 0, |quantity| ≤ 10^15, отрицательное = шорт
- ISIN — 12 символов: 2 буквы страны, 9 букв/цифр, контрольная цифра (алгоритм Луна)
- деньги в JSON — десятичные строки; нет значения → `null`, не 0
- изменения набора передают `expected_revision`
- результат строки — `metric_value` (DV01, ₽), итог — `total_value` (как в БД)
- статус расчёта: `success` (есть `total_value`), `partial` / `failed` (`total_value = null`)
- ошибка: `{"code", "message", "details"}`
