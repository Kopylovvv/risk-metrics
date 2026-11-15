# Диагностика и мониторинг backend (S3-11)

Два канала, у каждого своя задача:

| | Логи | Метрики |
|---|---|---|
| Что | одно событие одного запроса | агрегаты по всем запросам |
| Где | stdout контейнера `backend`, JSON по строке | `GET /metrics` (формат Prometheus) |
| Связь | общий `request_id` у всех событий запроса | нет идентификаторов, только метки ниже |
| Для чего | разобрать конкретную ошибку | дашборд, тренды, алерты |

## Идентификатор запроса

* Каждый ответ API несёт заголовок `X-Request-ID`; тело любой ошибки содержит то же
  значение в поле `request_id`
* Если клиент прислал свой `X-Request-ID` вида `[A-Za-z0-9._-]{8,64}`, он сохраняется,
  иначе создаётся новый (32 hex-символа)
* Найти все события запроса: `docker compose logs backend | grep '"request_id": "<id>"'`

## События в логах

Общие поля: `ts`, `level`, `logger`, `event`, `request_id`, `stage` (если событие внутри
этапа расчёта), `exception` (трассировка, если есть). Уровень задаёт `LOG_LEVEL`
(по умолчанию `INFO`)

| event | Поля | Когда |
|---|---|---|
| `http.request` | `method`, `route` (шаблон), `status`, `duration_ms`, `error_code`, `error_type` | конец каждого запроса |
| `calc.stage` | `stage` (`read`, `sources`, `write`), `outcome` (`ok`/`error`), `duration_ms`, `error_type` | конец этапа расчёта |
| `source.request` | `source` (`moex`, `cbr`, `test`, `demo`, `none`), `operation` (`instrument`, `snapshot`, `fx_rate`), `outcome` (`ok` или код ошибки), `duration_ms`, `isin` или `currency`, `error_type` | каждый запрос к источнику |
| `calc.finished` | `status`, `duration_ms`, `rows`, `failed_rows`, `calculation_id`, `error_type` | конец расчёта |
| `source.crash`, `unhandled`, `db.unavailable` | `error_type`, `exception` | сбой адаптера, непредвиденная ошибка, нет БД |

`error_type`: код контракта (`PROVIDER_UNAVAILABLE`, `MISSING_FX_RATE`...) или имя класса
исключения (`TimeoutError`). В логи не пишутся cookie, CSRF-токен, заголовки, тело
запроса, id набора и сделки; в тексте исключений маскируются пароль в адресе БД и пары
`password=`, `token=`, `secret=`, `cookie=`, `session=`. Журнал доступа uvicorn выключен:
он писал сырой путь с id набора, вместо него `http.request`

Пример одного расчёта (сокращено):

```json
{"event": "calc.stage", "request_id": "9f2c…", "stage": "read", "outcome": "ok", "duration_ms": 4.1}
{"event": "source.request", "request_id": "9f2c…", "stage": "sources", "source": "cbr", "operation": "fx_rate", "outcome": "FX_PROVIDER_UNAVAILABLE", "error_type": "TimeoutError", "currency": "USD", "duration_ms": 5001.3}
{"event": "calc.stage", "request_id": "9f2c…", "stage": "sources", "outcome": "ok", "duration_ms": 5003.0}
{"event": "calc.stage", "request_id": "9f2c…", "stage": "write", "outcome": "ok", "duration_ms": 9.8}
{"event": "calc.finished", "request_id": "9f2c…", "status": "partial", "rows": 2, "failed_rows": 1, "duration_ms": 5017.2}
{"event": "http.request", "request_id": "9f2c…", "method": "POST", "route": "/api/v1/workspaces/{workspace_id}/calculations", "status": 201, "duration_ms": 5020.4}
```

## Метрики

Адрес: `http://backend:8000/metrics` внутри сети compose (с хоста `127.0.0.1:8000/metrics`).
Через nginx фронта не проксируется и наружу не открыт. Сам `/metrics` в метрики не попадает

| Имя | Тип | Единицы | Метки |
|---|---|---|---|
| `risk_http_requests_total` | counter | запросы | `method`, `route`, `status` |
| `risk_http_request_duration_seconds` | histogram | секунды | `method`, `route` |
| `risk_calculations_total` | counter | расчёты | `status` |
| `risk_calculation_duration_seconds` | histogram | секунды | `status` |
| `risk_calculation_stage_duration_seconds` | histogram | секунды | `stage` |
| `risk_calculation_rows_total` | counter | строки | `status`, `error_code` |
| `risk_source_requests_total` | counter | запросы | `source`, `operation`, `outcome` |
| `risk_source_request_duration_seconds` | histogram | секунды | `source`, `operation` |

Корзины гистограмм: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30 с

Значения меток (список закрытый, кардинальность ограничена):

| Метка | Значения |
|---|---|
| `method` | `GET`, `POST`, `PATCH`, `DELETE`, `PUT`, `OPTIONS`, `HEAD`, иначе `OTHER` |
| `route` | шаблон маршрута FastAPI (`/api/v1/workspaces/{workspace_id}/trades`), `/docs`, `/openapi.json`; любой неизвестный путь `unmatched` |
| `status` (HTTP) | код ответа: `200`, `201`, `401`, `404`, `409`, `422`, `500`, `503`... |
| `status` (расчёт) | `success`, `partial`, `failed`; без записи результата: `conflict` (устарел `expected_revision`), `empty` (нет сделок), `error` (сбой) |
| `stage` | `read`, `sources`, `write` (только успешные этапы) |
| `status`, `error_code` (строки) | `success`/`failed`; `none` или код из `RowErrorCode` |
| `source` | `moex`, `cbr`, `test`, `demo`, `none` (временная заглушка) |
| `operation` | `instrument`, `snapshot`, `fx_rate` |
| `outcome` | `ok` или код из `RowErrorCode` |

В метках никогда нет ISIN, id набора, сделки или расчёта, cookie, токенов и сырого пути

## Подключение (Александр)

Prometheus, `scrape_configs`:

```yaml
- job_name: risk-backend
  metrics_path: /metrics
  scrape_interval: 15s
  static_configs:
    - targets: ["backend:8000"]
```

Панели дашборда (PromQL):

| Панель | Запрос |
|---|---|
| Запросы в секунду по маршруту | `sum by (route) (rate(risk_http_requests_total[5m]))` |
| Доля ошибок 5xx | `sum(rate(risk_http_requests_total{status=~"5.."}[5m])) / sum(rate(risk_http_requests_total[5m]))` |
| p95 длительности запроса | `histogram_quantile(0.95, sum by (le, route) (rate(risk_http_request_duration_seconds_bucket[5m])))` |
| Расчёты по статусам | `sum by (status) (increase(risk_calculations_total[1h]))` |
| p95 длительности расчёта | `histogram_quantile(0.95, sum by (le) (rate(risk_calculation_duration_seconds_bucket[5m])))` |
| Медленный этап | `histogram_quantile(0.95, sum by (le, stage) (rate(risk_calculation_stage_duration_seconds_bucket[5m])))` |
| Ошибки строк | `sum by (error_code) (increase(risk_calculation_rows_total{status="failed"}[1h]))` |
| Доступность источников | `sum by (source, outcome) (rate(risk_source_requests_total[5m]))` |
| p95 ответа источника | `histogram_quantile(0.95, sum by (le, source) (rate(risk_source_request_duration_seconds_bucket[5m])))` |

Пример алерта: источник отвечает ошибкой больше половины запросов за 10 минут:
`sum by (source) (rate(risk_source_requests_total{outcome!="ok"}[10m])) / sum by (source) (rate(risk_source_requests_total[10m])) > 0.5`

Метрики живут в памяти процесса: после перезапуска контейнера счётчики начинаются с нуля
(`rate`/`increase` это учитывают). Backend запускается одним процессом uvicorn; при
нескольких воркерах понадобится режим multiprocess у `prometheus-client`
