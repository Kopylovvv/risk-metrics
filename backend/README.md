# Backend — модуль расчёта DV01

Этап: модели данных API и контракт с модулями данных и расчёта. Роутеров пока нет

## Структура

```
app/
  contracts.py     общий интерфейс backend ↔ market_data / risk_core (согласовать с Андреем)
  schemas/         Pydantic-модели запросов и ответов API
    common.py      ISIN (формат + контрольная цифра), Quantity, DecimalStr, ErrorOut
    trades.py      TradeCreate, TradeUpdate, TradeOut, WorkspaceOut, WorkspaceCreated
    instruments.py InstrumentOut
    calculations.py CalculationRequest, CalculationOut, CalculationRowOut, статусы
```

## Правила, заложенные в модели

- `quantity` — целое ≠ 0, |quantity| ≤ 10^15, отрицательное = шорт
- ISIN — 12 символов: 2 буквы страны, 9 букв/цифр, контрольная цифра (алгоритм Луна)
- деньги в JSON — десятичные строки; нет значения → `null`, не 0
- изменения набора передают `expected_revision`
- результат строки — `metric_value` (DV01, ₽), итог — `total_value` (как в БД)
- статус расчёта: `success` (есть `total_value`), `partial` / `failed` (`total_value = null`)
- ошибка: `{"code", "message", "details"}`
