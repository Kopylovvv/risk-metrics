# Backend: модуль расчёта DV01

Готово: схема БД с миграциями, API набора сделок (создание, чтение, изменение, удаление),
карточка инструмента, расчёт DV01 с сохранением результата, расчётное ядро `app/risk_core`.
Источники новых расчётов: параметры облигаций с Мосбиржи (ISS), курсы валют ЦБ РФ (решение
команды от 14.11.2026, см. «Источники данных»); адаптеры в `app/market_data`, подключены в
`app/api/deps.py`. С `EXTERNAL_SOURCES=off` (тесты, работа без сети) вместо них временные
источники из `app/stubs`, и расчёт берёт данные, уже сохранённые в БД. Cbonds и RuData в
рабочем сценарии не используются

## Структура

```
app/
  main.py          приложение FastAPI, CORS, подключение роутеров
  config.py        настройки из окружения / .env в корне репозитория
  errors.py        единый формат ошибок {code, message, details, request_id}
  observability.py request_id, JSON-логи событий, метрики Prometheus (/metrics)
  enums.py         общие перечисления: источник, тип купона, статусы, коды ошибок строк
  contracts.py     общий интерфейс backend ↔ market_data / risk_core
  schemas/         Pydantic-модели запросов и ответов API
  api/             роутеры: workspaces, trades, calculations, instruments, health;
                   в deps.py сессия, CSRF и точки подключения источников и ядра
  services/        наборы и сделки (workspaces.py), путь расчёта (calculation.py),
                   чтение справочника, снимков и курсов (market.py)
  risk_core/       расчётное ядро: формула DV01 одной сделки, без сети и БД
  market_data/     адаптеры Мосбиржи (moex.py) и ЦБ РФ (cbr.py), дюрация и выпуклость по
                   графику выплат (bond_math.py), проверка живых запросов (live_check.py);
                   адаптер Cbonds остался, в расчёте не используется
  stubs/           источники без данных (EXTERNAL_SOURCES=off), демо-данные
  db/              модели SQLAlchemy (схема v4) и подключение
migrations/        Alembic: 0001 схема v4, 0002 имена внешних ключей, 0003 НКД в результате,
                   0004 источник moex и валюта расчётов в снимке,
                   0005 запрет UPDATE сохранённых снимков, курсов и результатов,
                   0006 признак запасных данных в строке результата
tests/             тесты на настоящем PostgreSQL: схема, ограничения, API; адаптеры на
                   записанных ответах ISS и ЦБ (tests/fixtures), без сети
```

## Запуск через Compose

Из корня репозитория: заполнить `.env` по `.env.example` и выполнить
`docker compose up --build -d --wait`. Compose запускает БД, выполняет миграции
отдельным сервисом `migrate`, затем запускает backend. API: http://localhost:8000/docs.
Подробные настройки, логи и проверка окружения: [корневой README](../README.md).

## Запуск Python на компьютере

1. База: в корне репозитория `cp .env.example .env`, вписать пароль,
   `docker compose up -d --wait db` (PostgreSQL на `localhost:5433`)
2. В папке `backend`:

```bash
python -m venv .venv && .venv\Scripts\activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.stubs.demo_data      # демо-облигации и курс USD на сегодня (по желанию)
uvicorn app.main:app --reload
```

На Windows запускать с `--reload`: без него uvicorn берёт ProactorEventLoop, с которым
psycopg в async-режиме не работает, и `/health/ready` отвечает `database: unavailable`

Контракт и проверка запросов: http://localhost:8000/docs

## API

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/v1/workspaces` | открыть модуль: cookie сессии + `csrf_token`; с существующей cookie вернёт тот же набор (200) |
| GET | `/api/v1/workspaces/{id}` | набор, `revision`, сделки с карточками инструментов, `last_calculation_id` |
| POST | `/api/v1/workspaces/{id}/trades` | добавить сделку `{isin, quantity, expected_revision}` |
| PATCH | `/api/v1/workspaces/{id}/trades/{trade_id}` | изменить `isin` и/или `quantity` (+ `expected_revision`) |
| DELETE | `/api/v1/workspaces/{id}/trades/{trade_id}?expected_revision=N` | удалить сделку |
| POST | `/api/v1/workspaces/{id}/calculations` | расчёт DV01 `{expected_revision, valuation_date?}` (дата не позже сегодня), результат сохраняется |
| GET | `/api/v1/calculations/{id}` | сохранённый расчёт с входами на его момент |
| GET | `/metrics` | метрики Prometheus, только внутри сети (см. `docs/monitoring.md`) |
| GET | `/api/v1/calculations/{id}/source-diff?as_of=` | сохранённые снимок и курс рядом с новым ответом источника |
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
| 404 | `TRADE_NOT_FOUND`, `CALCULATION_NOT_FOUND`, `INSTRUMENT_NOT_FOUND` | сделки нет в наборе / расчёт чужой или не найден / бумаги нет в справочнике |
| 409 | `REVISION_CONFLICT` | набор уже изменён: обновить и повторить |
| 422 | `VALIDATION_ERROR` | неверный ввод; в `details[].field` указано поле |
| 422 | `WORKSPACE_EMPTY` | расчёт пустого набора |
| 404, 405 | `NOT_FOUND`, `METHOD_NOT_ALLOWED` | нет такого адреса / метода |
| 503 | `DATABASE_UNAVAILABLE` | нет соединения с PostgreSQL |
| 500 | `INTERNAL_ERROR` | непредвиденная ошибка (подробности только в логе сервера) |

## Расчёт DV01

`POST /api/v1/workspaces/{id}/calculations` проходит три шага, чтобы не держать транзакцию
открытой, пока ждём внешние источники:

1. Короткое чтение: проверка `expected_revision`, сделки набора, справочник, последние
   снимки (дата данных не позже даты оценки) и курсы из БД
2. Без транзакции: недостающее у источников. Если снимка подключённого источника за дату
   оценки нет, спрашиваем источник; не ответил, но в БД есть более ранний снимок, считаем
   по нему (его дата в `data_as_of`) и ставим в строке `market_data_fallback = true`.
   То же для курсов валют (дата в `fx_rate_date`, признак `fx_rate_fallback`).
   Снимки Cbonds и RuData в новые расчёты не попадают; за одну дату снимок Мосбиржи важнее
   контрольного набора (`test`), курс ЦБ (`cbr`) важнее демо (`demo`)
3. Короткая запись: набор блокируется и `revision` проверяется ещё раз (изменили во время
   расчёта → 409, ничего не сохраняется), новые инструменты, снимки и курсы, запуск
   `calculation_runs` и строки `calculation_results`

Формула (методика `dv01-v2`): CFV = количество × (номинал × котировка/100 + НКД) × курс
(RUB = 1), то есть по цене с НКД; НКД на одну бумагу в валюте номинала, нет НКД → ошибка строки;
DV01 = CFV × (−D·1bp + ½·C·1bp²); плавающий купон: D = 1/частота выплат, C = 0.
Все суммы в рублях, без округления

Каждая строка результата хранит копию входов (`input_isin`, `input_quantity`) и
подставленные параметры (`*_used`, снимок, курс), поэтому старый расчёт не меняется после
правки или удаления сделки. Статус: `success` (все строки посчитаны, есть `total_value`),
`partial` (часть строк с ошибкой), `failed` (ни одной); при ошибках `total_value = null`

Ошибки строк:

| Код | Когда |
|---|---|
| `INSTRUMENT_NOT_FOUND` | выпуска нет у источника |
| `MISSING_MARKET_DATA` | нет цены, номинала, НКД, дюрации или выпуклости; у плавающего купона нет частоты |
| `INCOMPATIBLE_MARKET_DATA` | чужой ISIN или дата позже даты оценки; валюта снимка не совпадает со справочником; номинал, котировка или курс ≤ 0, НКД < 0, частота ≤ 0, дюрация < 0 |
| `PROVIDER_UNAVAILABLE` | источник рыночных данных не ответил, а сохранённого снимка нет |
| `MISSING_FX_RATE` | источник курсов ответил, но курса валюты нет |
| `FX_PROVIDER_UNAVAILABLE` | источник курсов не ответил, а сохранённого курса нет |

Источники и ядро подключаются в `app/api/deps.py` (`get_market_data`, `get_fx_rates`,
`get_risk_calculator`) по протоколам из `contracts.py`. Ядро `app/risk_core` (правила и
пример вызова в docstring `dv01.py`), источники `MoexMarketData` и `CbrFxRates`
(`app/market_data`). С `EXTERNAL_SOURCES=off` рыночные данные отвечают
`PROVIDER_UNAVAILABLE`, курсы `FX_PROVIDER_UNAVAILABLE`, и API считает по данным из БД
(например, после `python -m app.stubs.demo_data`)

## Источники данных: Мосбиржа и ЦБ РФ

Контракт для адаптеров (подробно в docstring `MarketSnapshot` и `FxRate` в `contracts.py`):

| Поле | Значение |
|---|---|
| `provider` | `moex`; у адаптера одноимённый атрибут, по нему сохранённый снимок дня не запрашивается повторно |
| `currency` | валюта номинала (у Мосбиржи `FACEUNIT`); курс ЦБ берётся для неё |
| `settlement_currency` | валюта расчётов (`CURRENCYID`, `SUR` → `RUB`), может отличаться от валюты номинала |
| `price_pct` | чистая цена в % от номинала (95, а не 0.95); единицы по описанию поля, не по величине |
| `accrued_interest` | НКД на бумагу в валюте номинала; если источник дал его в валюте расчётов, адаптер переводит или отвечает `INCOMPATIBLE_MARKET_DATA` |
| `data_as_of` | дата торгов, к которой относятся цена и НКД |
| `FxRate.rate_to_rub` | рублей за одну единицу валюты; курс ЦБ «за 100 единиц» приводится через `FxRate.per_unit` |
| `FxRate.rate_date` | дата, на которую ЦБ установил курс; `source = cbr` |

Валюта расчётов на формулу не влияет: цена в % от номинала, НКД в валюте номинала, значит
CFV = количество × (номинал × цена/100 + НКД) × курс ЦБ валюты номинала

Адаптеры: `app/market_data/moex.py` и `cbr.py`, какие поля ISS и ЦБ во что переводятся,
в [`docs/market-data-map.md`](../docs/market-data-map.md). Проверка живых запросов (нужен
интернет): `python -m app.market_data.live_check` (ОФЗ 26238 и ГазКЗ-34Д на сегодня) или
`python -m app.market_data.live_check RU000A1038V6 2026-11-13`

Происхождение данных в строке результата (`GET /api/v1/calculations/{id}`): `provider` и
`data_as_of` (цена, НКД, дюрация), `currency` и `settlement_currency`, `fx_source` и
`fx_rate_date` (у рублёвой бумаги `null`, курс 1). Контрольный набор виден как `test`,
демо-курс как `demo`. Новые загрузки не меняют сохранённые расчёты: строка ссылается на свой
снимок и курс, а снимки разных источников и дат хранятся рядом. С миграции 0005 это
проверяет и БД: UPDATE снимков, курсов и запусков запрещён, у строки результата можно
поменять только `trade_id` (его обнуляет удаление сделки)

Запасные данные: `market_data_fallback` и `fx_rate_fallback` равны `true`, если источник не
ответил и расчёт взял ранее сохранённый снимок или курс (их даты в `data_as_of` и
`fx_rate_date`). Повторное использование снимка за дату оценки без запроса к источнику
запасными данными не считается. В расчётах до миграции 0006 оба поля `null`

Сравнение с новым ответом источника: `GET /api/v1/calculations/{id}/source-diff?as_of=ДАТА`
(по умолчанию сегодня). По каждой бумаге `saved` и `saved_fx` (по чему считали),
`current` и `current_fx` (что источник отвечает сейчас), `changed` (отличающиеся поля,
курс с префиксом `fx_`). Источник не ответил: справа последнее сохранённое в БД
(`current_origin = db`) и код в `current_error`. Ответ ничего не записывает

### Для фронта (Егор)

- при открытии страницы `POST /api/v1/workspaces` с `credentials: "include"`,
  запомнить `id` и `csrf_token`; если `last_calculation_id` не `null`, показать этот
  результат через `GET /api/v1/calculations/{id}` (так результат не теряется после перезагрузки)
- во всех POST/PATCH/DELETE передавать заголовок `X-CSRF-Token` и `expected_revision` из последнего ответа
- проще всего проксировать `/api` через Vite (`server.proxy`), тогда cookie работает без CORS;
  прямые запросы с `localhost:5173` тоже разрешены
- в строке результата есть происхождение данных: `provider`, `data_as_of`, `currency`,
  `settlement_currency`, `fx_source`, `fx_rate_date`; новый код ошибки `FX_PROVIDER_UNAVAILABLE`
  (источник курсов не ответил), `MISSING_FX_RATE` теперь значит «курса этой валюты нет»
- `market_data_fallback` / `fx_rate_fallback` = `true`: источник не ответил, использованы
  сохранённые данные; показать предупреждение с датой (`data_as_of` / `fx_rate_date`)
- у ошибок есть `request_id` (он же в заголовке `X-Request-ID`): его можно показать
  пользователю вместе с сообщением, по нему ищется запрос в логах
- `GET /api/v1/calculations/{id}/source-diff`: сохранённые данные расчёта рядом с новым
  ответом источника, список `changed` можно подсветить

## Настройки (переменные окружения или `.env` в корне)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATABASE_URL` | не задан | полный адрес БД; если задан, главнее остальных |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | из `.env` | как в `compose.yaml` |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `127.0.0.1`, `5433` | внутри сети compose: `db`, `5432` |
| `ALLOWED_ORIGINS` | `localhost`/`127.0.0.1` на портах 5173 и 8000 | откуда можно слать изменения |
| `COOKIE_SECURE` | `false` | `true` на сервере с HTTPS |
| `LOG_LEVEL` | `INFO` | уровень логов (`DEBUG`, `WARNING`...) |
| `EXTERNAL_SOURCES` | `on` | `off`: не ходить в Мосбиржу и ЦБ, считать по данным из БД (так в тестах) |

## Диагностика и метрики

Подробно, с перечнем метрик и запросами для дашборда: [`docs/monitoring.md`](../docs/monitoring.md)

* Логи: stdout, JSON по строке на событие (`http.request`, `calc.stage`, `source.request`,
  `calc.finished`). Все события одного запроса связаны `request_id`; он же в заголовке
  ответа `X-Request-ID` и в поле `request_id` тела ошибки
* Метрики: `GET /metrics`, только агрегаты: HTTP-запросы и расчёты (количество, статусы,
  длительность), этапы расчёта, запросы к источникам. Метки ограничены шаблоном маршрута,
  методом, кодом и статусами; ISIN, id набора и сделки, cookie и токенов в них нет

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
- история: после изменения и удаления сделок, нового снимка и нового расчёта старый
  результат совпадает строка в строку, формула по сохранённым параметрам даёт ровно
  сохранённый DV01, использованные снимки и курсы удалить нельзя
- расчёт: контрольный запрос от сделок до сохранённого результата (лонг, шорт, флоатер,
  валютная бумага, итог), история после изменения и удаления сделки, ошибки строк и
  статусы partial/failed, данные от источника сохраняются и переиспользуются, запасной
  старый снимок, отсутствие открытой транзакции во время внешнего запроса (по
  `pg_stat_activity`), 409 при изменении набора во время расчёта, доступ к чужим расчётам

## CI

Workflow `.github/workflows/backend.yml` запускается на push и Pull Request, если менялись
`backend/`, `compose.yaml` или сам workflow. Шаги: Ruff (`check` и `format --check`),
PostgreSQL 18.6 из `compose.yaml`, миграции на пустой базе (`upgrade head`, `alembic check`,
`downgrade base`, снова `upgrade head`), все тесты (`pytest`, отчёт JUnit сохраняется
артефактом `backend-pytest-report`), сборка Docker-образа. Версия Ruff зафиксирована в
`requirements-dev.txt`, чтобы форматирование не менялось от версии к версии

## Показ: backend по шагам

1. При ручном запуске Python в корне: `docker compose up -d --wait db`; в `backend`: `alembic upgrade head`,
   `python -m app.stubs.demo_data`, `uvicorn app.main:app --reload` (или Docker-образ, см. выше)
2. http://localhost:8000/docs:
   - `POST /api/v1/workspaces` → `id` и `csrf_token` (вставить токен в заголовок `X-CSRF-Token`)
   - `POST .../trades` по одной: `RU000A1038V6` 100, `RU000A105A95` 299, `XS0191754729` -10,
     `RU000A0JX0J2` 50 (нет дюрации), `US0378331005` 5 (нет в справочнике)
   - `POST .../calculations` с последним `expected_revision` → `partial`: три строки
     посчитаны (−28.8692, −7.5124, +386.5395), две с кодом ошибки, итога нет
   - удалить две ошибочные сделки и посчитать снова → `success` и `total_value`
   - изменить количество сделки и открыть `GET /api/v1/calculations/{id}` старого расчёта:
     в нём прежние количества
3. Данные в БД: `docker compose exec -T db psql -U risk_metrics -d risk_metrics < backend/scripts/show_db.sql`
   (наборы, сделки, снимки и курсы, запуски расчёта, строки с входами и DV01)

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

## Адаптер Cbonds (`app/market_data`, в расчёте не используется)

С 14.11.2026 Cbonds и RuData убраны из рабочего сценария: `deps.py` их не подключает, а
сохранённые снимки этих источников остаются только для чтения старых расчётов. Код
адаптера и тесты оставлены как есть

Адаптер `CbondsMarketData` реализует `MarketDataProvider`: методы web-сервиса
`get_emissions` (валюта, номинал, плавающая ставка, выплат в год), `get_tradings_new`
(индикативная цена, НКД, модифицированная дюрация, выпуклость) и `get_aci` (НКД, если в
итогах торгов его нет). Правила нормализации и
коды ошибок описаны в docstring `app/market_data/cbonds.py`

Для ручной проверки берёт `CBONDS_LOGIN` и `CBONDS_PASSWORD` из `.env` (`CBONDS_URL` по
умолчанию `https://ws2.cbonds.info/services/json/`). Публичный демо-доступ указан на
https://cbonds.ru/api/catalog/

Проверка реального запроса (логин и пароль не печатаются):

```bash
python -m app.market_data.check --list                     # какие бумаги видит логин
python -m app.market_data.check US900123AT75 2026-10-23    # снимок по бумаге на дату
```

Тесты `tests/test_market_data_cbonds.py` работают без сети на подставных ответах,
собранных по спецификации Cbonds (`openapi.yaml`); формат полей сверен с реальным ответом
демо-доступа (`--list`). Открытые вопросы: единицы выпуклости в документации не указаны,
по реальным значениям годы² (подтвердить у эксперта); выбор торговой площадки, когда их
несколько (сейчас самая свежая дата с индикативной ценой); RuData: доступа нет
