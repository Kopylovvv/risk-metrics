"""Контрольная приёмка второго показа (#32): полный цикл через HTTP API запущенного backend

Сценарий из README «Показ: backend по шагам», каждое число сверяется с ручным расчётом.

Подготовка (из папки backend): alembic upgrade head; python -m app.stubs.demo_data;
uvicorn app.main:app --reload (на Windows с --reload) или docker compose up
Запуск:  python scripts/acceptance.py [http://127.0.0.1:8000]
Код выхода 0: всё совпало; 1: есть расхождения (они перечислены)
"""

import subprocess
import sys
from datetime import date
from decimal import Decimal

import httpx

FIXED, FLOATER, USD_BOND = "RU000A1038V6", "RU000A105A95", "XS0191754729"
NO_DURATION, UNKNOWN = "RU000A0JX0J2", "US0378331005"

# Ручной расчёт по демо-данным (app/stubs/demo_data.py), методика dv01-v2:
# CFV = q × (номинал × цена/100 + НКД) × курс;  DV01 = CFV × (−D·0.0001 + ½·C·0.0001²)
EXPECTED = {
    # 100 × (950 + 12.5) = 96 250;  96 250 × (−0.0003 + 0.00000006) = −28.869225
    FIXED: (Decimal("96250"), Decimal("-28.869225")),
    # floating: D = 1/4, C = 0;  299 × (1000 + 5) = 300 495;  × −0.000025 = −7.512375
    FLOATER: (Decimal("300495"), Decimal("-7.512375")),
    # −10 × (985 + 10.25) × 92.5 = −920 606.25;  × (−0.00042 + 0.000000125) = +386.53954921875
    USD_BOND: (Decimal("-920606.25"), Decimal("386.53954921875")),
}
# итог после удаления ошибочных сделок: −28.869225 − 7.512375 + 386.53954921875
EXPECTED_TOTAL = Decimal("350.15794921875")

problems: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"  [{'OK' if ok else 'ОШИБКА'}] {what}")
    if not ok:
        problems.append(what)


def git_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "неизвестно"


def main(base_url: str) -> int:
    today = date.today()
    print(f"Приёмка: {base_url}, коммит {git_version()}, дата оценки {today}")
    with httpx.Client(base_url=base_url, timeout=30) as api:
        ready = api.get("/health/ready").json()
        check(ready.get("database") == "ok", f"backend и БД доступны: {ready}")

        ws = api.post("/api/v1/workspaces").json()
        ws_id, headers = ws["id"], {"X-CSRF-Token": ws["csrf_token"]}
        trades_url = f"/api/v1/workspaces/{ws_id}/trades"

        print("\n1. Пять сделок, расчёт → partial")
        revision, trade_ids = ws["revision"], {}
        for isin, quantity in [
            (FIXED, 100),
            (FLOATER, 299),
            (USD_BOND, -10),
            (NO_DURATION, 50),
            (UNKNOWN, 5),
        ]:
            r = api.post(
                trades_url,
                json={"isin": isin, "quantity": quantity, "expected_revision": revision},
                headers=headers,
            )
            body = r.json()
            revision = body["revision"]
            trade_ids[isin] = next(t["id"] for t in body["trades"] if t["isin"] == isin)
        calc = api.post(
            f"/api/v1/workspaces/{ws_id}/calculations",
            json={"expected_revision": revision},
            headers=headers,
        ).json()
        rows = {row["isin"]: row for row in calc["results"]}
        check(calc["status"] == "partial", f"статус {calc['status']} (ожидался partial)")
        check(calc["total_value"] is None, "итога нет, пока есть ошибочные строки")
        for isin, (cfv, dv01) in EXPECTED.items():
            got_cfv, got = (
                Decimal(rows[isin]["current_fair_value"]),
                Decimal(rows[isin]["metric_value"]),
            )
            check(
                got_cfv == cfv and got == dv01,
                f"{isin}: CFV {got_cfv}, DV01 {got} (ожидалось {cfv}, {dv01})",
            )
        check(
            rows[NO_DURATION]["error_code"] == "MISSING_MARKET_DATA"
            and rows[NO_DURATION]["metric_value"] is None,
            f"{NO_DURATION}: {rows[NO_DURATION]['error_code']}, значение null, не 0",
        )
        # без доступа к источнику PROVIDER_UNAVAILABLE, с доступом к Cbonds INSTRUMENT_NOT_FOUND
        check(
            rows[UNKNOWN]["error_code"] in ("PROVIDER_UNAVAILABLE", "INSTRUMENT_NOT_FOUND"),
            f"{UNKNOWN}: {rows[UNKNOWN]['error_code']}",
        )
        old_id = calc["id"]

        print("\n2. Удалить ошибочные сделки, расчёт → success")
        for isin in (NO_DURATION, UNKNOWN):
            revision = api.delete(
                f"{trades_url}/{trade_ids[isin]}",
                params={"expected_revision": revision},
                headers=headers,
            ).json()["revision"]
        calc = api.post(
            f"/api/v1/workspaces/{ws_id}/calculations",
            json={"expected_revision": revision},
            headers=headers,
        ).json()
        total = Decimal(calc["total_value"]) if calc["total_value"] is not None else None
        check(calc["status"] == "success", f"статус {calc['status']}")
        check(total == EXPECTED_TOTAL, f"итог {total} (ожидалось {EXPECTED_TOTAL})")

        print("\n3. История: правка сделки не меняет старый расчёт")
        revision = api.patch(
            f"{trades_url}/{trade_ids[FIXED]}",
            json={"quantity": 200, "expected_revision": revision},
            headers=headers,
        ).json()["revision"]
        old = api.get(f"/api/v1/calculations/{old_id}").json()
        old_fixed = next(row for row in old["results"] if row["isin"] == FIXED)
        check(old_fixed["quantity"] == 100, f"в старом расчёте количество {old_fixed['quantity']}")
        check(Decimal(old_fixed["metric_value"]) == EXPECTED[FIXED][1], "старый DV01 не изменился")

        print("\n4. Конфликт версий: устаревший revision → 409")
        r = api.post(
            trades_url,
            json={"isin": FIXED, "quantity": 1, "expected_revision": 0},
            headers=headers,
        )
        check(r.status_code == 409, f"HTTP {r.status_code} {r.json().get('code')}")

    print(f"\nИтог: {'всё совпало' if not problems else f'расхождений: {len(problems)}'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
