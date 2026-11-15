"""Проверка обновления приложения: данные двух посетителей до и после (#64)

Сценарий из docs/deployment.md «Обновление проверенной версии»:
1. старая версия запущена: python scripts/upgrade_check.py before http://127.0.0.1:8000
   (два набора, сделки и расчёт; cookie и ответы сохраняются в upgrade_state.json)
2. обновление: git pull, docker compose up --build -d --wait (migrate → alembic upgrade head)
3. новая версия: python scripts/upgrade_check.py after http://127.0.0.1:8000
   тем же cookie читаются наборы, сделки и старые расчёты, сравниваются со шагом 1;
   устаревший revision даёт 409, чужой набор 404

Файл upgrade_state.json содержит cookie сессий: удалить после проверки, в отчёт не класть
Код выхода 0: всё совпало
"""

import json
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

# cookie сессий: во временной папке, а не в репозитории
STATE = (
    sys.argv[3] if len(sys.argv) > 3 else str(Path(tempfile.gettempdir()) / "upgrade_state.json")
)
FIXED, USD = "RU000A1038V6", "XS0191754729"
DAY = date.today().isoformat()  # before и after запускаются в один день


def client(base, cookie=None):
    c = httpx.Client(base_url=base, timeout=30)
    if cookie:
        c.headers["Cookie"] = f"rm_session={cookie}"
    return c


def before(base):
    state = {}
    for name, trades in (("A", [(FIXED, 100), (USD, -10)]), ("B", [(FIXED, -5)])):
        c = client(base)
        ws = c.post("/api/v1/workspaces").json()
        h = {"X-CSRF-Token": ws["csrf_token"]}
        rev = 0
        for isin, q in trades:
            r = c.post(
                f"/api/v1/workspaces/{ws['id']}/trades",
                json={"isin": isin, "quantity": q, "expected_revision": rev},
                headers=h,
            )
            rev = r.json()["revision"]
        calc = c.post(
            f"/api/v1/workspaces/{ws['id']}/calculations",
            json={"expected_revision": rev, "valuation_date": DAY},
            headers=h,
        ).json()
        state[name] = {
            "cookie": c.cookies.get("rm_session"),
            "csrf": ws["csrf_token"],
            "ws": c.get(f"/api/v1/workspaces/{ws['id']}").json(),
            "calc": calc,
        }
        print(name, ws["id"][:8], "revision", rev, "calc", calc["status"], calc["total_value"])
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)


def after(base):
    state = json.load(open(STATE))
    ok = True

    def check(cond, what):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'OK' if cond else 'ОШИБКА'}] {what}")

    for name, s in state.items():
        c = client(base, s["cookie"])
        ws_id = s["ws"]["id"]
        ws = c.post("/api/v1/workspaces").json()
        check(
            ws["id"] == ws_id and ws["revision"] == s["ws"]["revision"],
            f"{name}: тот же набор {ws_id[:8]}, revision {ws['revision']}",
        )
        check(
            [(t["isin"], t["quantity"]) for t in ws["trades"]]
            == [(t["isin"], t["quantity"]) for t in s["ws"]["trades"]],
            f"{name}: сделки {[(t['isin'], t['quantity']) for t in ws['trades']]}",
        )
        check(ws["csrf_token"] == s["csrf"], f"{name}: CSRF-токен прежний")
        old = c.get(f"/api/v1/calculations/{s['calc']['id']}").json()
        same = (
            all(
                {k: v for k, v in new.items() if k in was} == was
                for new, was in zip(old["results"], s["calc"]["results"], strict=True)
            )
            and old["total_value"] == s["calc"]["total_value"]
        )
        check(
            same,
            f"{name}: старый расчёт {old['status']} {old['total_value']}, прежние поля строк те же",
        )
        added = sorted(set(old["results"][0]) - set(s["calc"]["results"][0]))
        print(f"      новые поля строки: {added}")
        h = {"X-CSRF-Token": s["csrf"]}
        r = c.post(
            f"/api/v1/workspaces/{ws_id}/trades",
            json={"isin": FIXED, "quantity": 1, "expected_revision": 0},
            headers=h,
        )
        check(r.status_code == 409, f"{name}: старый revision после обновления → {r.status_code}")
        new = c.post(
            f"/api/v1/workspaces/{ws_id}/calculations",
            json={"expected_revision": ws["revision"], "valuation_date": DAY},
            headers=h,
        ).json()
        # пока адаптеры Мосбиржи и ЦБ не подключены, новый расчёт идёт по тем же данным из БД
        same_total = new["total_value"] is not None and Decimal(new["total_value"]) == Decimal(
            old["total_value"] or "NaN"
        )
        check(
            new["status"] == old["status"] and same_total,
            f"{name}: новый расчёт {new['status']} {new['total_value']}",
        )
        other = "B" if name == "A" else "A"
        r = c.get(f"/api/v1/workspaces/{state[other]['ws']['id']}")
        check(r.status_code == 404, f"{name}: чужой набор {other} → {r.status_code}")
    print("Итог:", "всё совпало" if ok else "есть расхождения")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(before(sys.argv[2]) if sys.argv[1] == "before" else after(sys.argv[2]))
