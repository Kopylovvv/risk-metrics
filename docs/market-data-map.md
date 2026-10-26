# Карта данных источника (Cbonds)

Задача #14. Какие поля нужны расчёту DV01, откуда они берутся, в каких единицах и что
делается, если поля нет. Код: `backend/app/market_data/cbonds.py`, контракт:
`MarketSnapshot` в `backend/app/contracts.py`.

## Поля расчёта

| Поле `MarketSnapshot` | Метод Cbonds | Поле Cbonds | Единицы / смысл | Обязательно | Если нет |
|---|---|---|---|---|---|
| `currency` | `get_emissions` | `currency_name` | ISO 4217 (`RUB`, `USD`; `RUR` → `RUB`) | да | `INCOMPATIBLE_MARKET_DATA` |
| `nominal` | `get_emissions` | `nominal_price`; у амортизируемых (`bond_type = 1`) `outstanding_nominal_price` | номинал одной бумаги в валюте номинала; у евробондов — минимальный лот | да | строка расчёта `MISSING_MARKET_DATA` |
| `price_pct` | `get_tradings_new` | `indicative_price` | чистая цена, % от номинала | да | `MISSING_MARKET_DATA` (берётся последняя дата с ценой) |
| `accrued_interest` | `get_tradings_new`, иначе `get_aci` | `aci` | НКД на одну бумагу (у евробондов — на лот), в валюте номинала | да | `MISSING_MARKET_DATA` |
| `duration_mod` | `get_tradings_new` | `dur_mod` | модифицированная дюрация к погашению, годы | для fixed | `MISSING_MARKET_DATA` |
| `convexity` | `get_tradings_new` | `convexity` | выпуклость; по реальным данным годы² (в документации не указано) | для fixed | `MISSING_MARKET_DATA` |
| `coupon_type` | `get_emissions` | `floating_rate` | `1` → floating, иначе fixed | да | fixed |
| `coupon_frequency` | `get_emissions` | `cupon_period` | выплат купона в год | для floating | `MISSING_MARKET_DATA` у floating |
| `data_as_of` | `get_tradings_new` | `date` | дата итогов торгов, не позже даты оценки | да | — |
| название (справочник) | `get_emissions` | `document_rus` / `document_eng` | название выпуска | нет | `null` |

Курс валюты к рублю — отдельный источник (`FxRateProvider`), предложен ЦБ РФ, решение у тимлида.

## Правила

- Числа из ответа переводятся в `Decimal` без потери знаков (приходят строками).
- Нет значения у источника → `None`, никогда не 0: ошибку строки ставит расчёт.
- Значения, которых не бывает (номинал или цена ≤ 0, `NaN`, чужой ISIN, дата позже даты
  оценки, валюта не ISO-код) → `INCOMPATIBLE_MARKET_DATA` до формулы.
- Таймаут, сеть, HTTP 401/403/500, ответ не того формата → `PROVIDER_UNAVAILABLE`.
- Евробонды: `nominal_price` и `aci` оба считаются от минимального лота, поэтому одна
  «бумага» в сделке = один лот, CFV согласована.

## Доступ (по факту, 26.10.2026)

- Полный доступ: ключа нет (API платный), запрос куратору без ответа с 24.10.2026.
- Публичный демо-доступ Cbonds (`https://cbonds.ru/api/catalog/`): запросы проходят,
  формат полей сверен с реальным ответом (`python -m app.market_data.check --list`).
  Демо открывает параметры 14 выпусков и торги по другим бумагам — полный снимок по одной
  бумаге собрать нельзя. **Блокер:** нужен полноценный ключ.
- RuData: доступа нет, адаптер не реализован.
- Логин и пароль хранятся только в `.env` (исключён из Git), в коде и логах их нет.

## Открытые вопросы эксперту

1. Единицы выпуклости Cbonds (по данным — годы²).
2. Какую торговую площадку брать, если по бумаге их несколько (сейчас — самая свежая дата
   с индикативной ценой).
3. Курс валюты: ЦБ РФ или другой источник.
