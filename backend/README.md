# Backend: модуль расчёта DV01

Готово: схема БД с миграциями, API набора сделок (создание, чтение, изменение, удаление),
карточка инструмента из БД. Расчёт DV01 будет следующим шагом (S2-11)

## Структура

```
app/
  main.py          приложение FastAPI, CORS, подключение роутеров
  config.py        настройки из окружения / .env в корне репозитория
  errors.py        единый формат ошибок {code, message, details}
  enums.py         общие перечисления: источник, тип купона, статусы, коды ошибок строк
  contracts.py     общий интерфейс backend ↔ market_data / risk_core
  schemas/         Pydantic-модели запросов и ответов API
  api/             роутеры: workspaces, trades, instruments, health; в deps.py сессия и CSRF
  services/        работа с наборами и сделками в БД, проверка revision
  db/              модели SQLAlchemy (схема v4) и подключение
migrations/        Alembic: 0001 схема v4, 0002 короткие имена внешних ключей
tests/             тесты на настоящем PostgreSQL: схема, ограничения, API
```

## Запуск

1. База: в корне репозитория `cp .env.example .env`, вписать пароль,
   `docker compose up -d --wait` (PostgreSQL на `localhost:5433`)
2. В папке `backend`:

```bash
python -m venv .venv && .venv\Scripts\activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Контракт и проверка запросов: http://localhost:8000/docs

## API

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/v1/workspaces` | открыть модуль: cookie сессии + `csrf_token`; с существующей cookie вернёт тот же набор (200) |
| GET | `/api/v1/workspaces/{id}` | набор, `revision`, сделки с карточками инструментов |
| POST | `/api/v1/workspaces/{id}/trades` | добавить сделку `{isin, quantity, expected_revision}` |
| PATCH | `/api/v1/workspaces/{id}/trades/{trade_id}` | изменить `isin` и/или `quantity` (+ `expected_revision`) |
| DELETE | `/api/v1/workspaces/{id}/trades/{trade_id}?expected_revision=N` | удалить сделку |
| GET | `/api/v1/instruments/{isin}` | карточка облигации из БД |
| GET | `/health/live`, `/health/ready` | процесс жив / база доступна |

Изменения набора возвращают весь набор с новым `revision`

Правила:

- `quantity`: целое ≠ 0, |quantity| ≤ 10^15, отрицательное = шорт
- ISIN: 12 символов (2 буквы страны, 9 букв или цифр, контрольная цифра по алгоритму Луна);
  пробелы и регистр нормализуются
- ISIN, которого нет в справочнике, сохраняется: `instrument = null`
- деньги в JSON передаются десятичными строками; нет значения → `null`, не 0
- набор доступен только своей сессии (HttpOnly-cookie `rm_session`)
- изменяющие запросы: заголовок `X-CSRF-Token` и, если браузер прислал `Origin`,
  он должен быть из `ALLOWED_ORIGINS`

Ошибки (`{"code", "message", "details"}`):

| HTTP | code | когда |
|---|---|---|
| 401 | `SESSION_REQUIRED` | нет cookie сессии |
| 403 | `CSRF_TOKEN_INVALID`, `ORIGIN_NOT_ALLOWED` | нет/неверный токен, чужой сайт |
| 404 | `WORKSPACE_NOT_FOUND` | набора нет или он чужой |
| 404 | `TRADE_NOT_FOUND`, `INSTRUMENT_NOT_FOUND` | сделки нет в этом наборе / бумаги нет в справочнике |
| 409 | `REVISION_CONFLICT` | набор уже изменён: обновить и повторить |
| 422 | `VALIDATION_ERROR` | неверный ввод; в `details[].field` указано поле |
| 404, 405 | `NOT_FOUND`, `METHOD_NOT_ALLOWED` | нет такого адреса / метода |
| 503 | `DATABASE_UNAVAILABLE` | нет соединения с PostgreSQL |
| 500 | `INTERNAL_ERROR` | непредвиденная ошибка (подробности только в логе сервера) |

### Для фронта (Егор)

- при открытии страницы `POST /api/v1/workspaces` с `credentials: "include"`,
  запомнить `id` и `csrf_token`
- во всех POST/PATCH/DELETE передавать заголовок `X-CSRF-Token` и `expected_revision` из последнего ответа
- проще всего проксировать `/api` через Vite (`server.proxy`), тогда cookie работает без CORS;
  прямые запросы с `localhost:5173` тоже разрешены

## Настройки (переменные окружения или `.env` в корне)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATABASE_URL` | не задан | полный адрес БД; если задан, главнее остальных |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | из `.env` | как в `compose.yaml` |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `localhost`, `5433` | внутри сети compose: `db`, `5432` |
| `ALLOWED_ORIGINS` | `localhost`/`127.0.0.1` на портах 5173 и 8000 | откуда можно слать изменения |
| `COOKIE_SECURE` | `false` | `true` на сервере с HTTPS |

Полезные команды Alembic: `alembic current`, `alembic downgrade base`,
`alembic upgrade head --sql` (SQL без подключения), `alembic check` (модели = миграции)

## Docker-образ backend

```bash
# из корня репозитория
docker build -t risk-metrics-backend backend
# миграции
docker run --rm --env-file .env -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT=5433 risk-metrics-backend alembic upgrade head
# API на http://localhost:8000
docker run --rm -p 8000:8000 --env-file .env -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT=5433 risk-metrics-backend
```

Тесты в контейнере (стадия `test`, база должна быть запущена):

```bash
docker build --target test -t risk-metrics-backend-test backend
docker run --rm --env-file .env -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT=5433 risk-metrics-backend-test
```

## Тесты

```bash
pytest
```

Создают отдельную базу `<POSTGRES_DB>_test`, ставят схему миграциями и удаляют её после
прогона (нужно право CREATE DATABASE, у пользователя из compose оно есть). Проверяется:

- схема, enum-ы, совпадение моделей и миграций, downgrade/upgrade
- ограничения БД и точное сохранение входов и результатов
- API: CRUD сделок с записью в PostgreSQL, проверка ISIN и количества, примеры из `/docs`,
  409 при устаревшем `revision` (в том числе параллельные запросы), 401/403/404 для
  чужих наборов, без cookie, без CSRF-токена и с чужого Origin

## Схема БД (v4) и ограничения

Таблицы: `workspaces`, `instruments`, `trades`, `market_data_snapshots`, `fx_rates`,
`calculation_runs`, `calculation_results`. Enum-ы: `provider`, `coupon_type`,
`run_status`, `row_status`

- Уникальность: `instruments.isin`, `workspaces.session_key_hash`,
  снимок `(instrument_id, data_as_of, provider)` (`uq_snapshot_per_day`),
  курс `(currency, rate_date, source)`
- Внешние ключи по схеме; у `calculation_results.trade_id` стоит `ON DELETE SET NULL`
  (история расчёта остаётся в `input_*`)
- CHECK сверх DBML (правила модели):
  - `trades.quantity <> 0`, `calculation_results.input_quantity <> 0`
  - `workspaces.revision >= 0`
  - `nominal > 0`, `price_pct > 0`, `coupon_frequency > 0`, `rate_to_rub > 0`
  - `calculation_runs`: `total_value` заполнен тогда и только тогда, когда `status = success`
  - `calculation_results`: `success` → есть CFV и `metric_value`, нет `error_code`;
    `failed` → есть `error_code`, `metric_value IS NULL` (не 0)
- Деньги и коэффициенты хранятся в `NUMERIC` без ограничения точности, количество в `numeric(20,0)`
