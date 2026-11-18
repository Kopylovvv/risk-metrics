"""Live acceptance on the authorized HTTPS demo; creates hypothetical test workspaces."""

import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.request
from decimal import Decimal

BASE = sys.argv[1].rstrip("/")
assert BASE.startswith("https://"), "HTTPS is required"
DAY = "2026-11-13"
report = []


def ok(message):
    report.append(message)
    print("OK:", message, flush=True)


class Visitor:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf = None

    def request(self, path, body=None, method=None, token=True, expected=200):
        headers = {"Origin": BASE, "Content-Type": "application/json"}
        if token and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(
            BASE + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method=method,
        )
        try:
            with self.http.open(req, timeout=120) as r:
                status, raw, response_headers = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            status, raw, response_headers = e.code, e.read(), e.headers
        assert status == expected, (path.split("?")[0], status, expected)
        return json.loads(raw) if "json" in response_headers.get(
            "Content-Type", ""
        ) else raw.decode(), response_headers

    def open(self):
        ws, h = self.request("/api/v1/workspaces", {}, expected=201)
        self.csrf = ws["csrf_token"]
        return ws, h


a, b = Visitor(), Visitor()
page, _ = a.request("/")
assert isinstance(page, str) and "/assets/" in page
assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
for asset in assets:
    a.request(asset)
ok("HTTPS HTML and built assets load")
wa, headers = a.open()
cookie = headers.get("Set-Cookie", "").lower()
assert all(s in cookie for s in ("secure", "httponly", "samesite=lax"))
wb, _ = b.open()
assert wa["id"] != wb["id"]
ok("Secure HttpOnly SameSite=Lax session; two independent workspaces")
path = "/api/v1/workspaces/" + wa["id"]
for isin, quantity in [("RU000A1038V6", 100), ("RU000A105A95", -10)]:
    wa, _ = a.request(
        path + "/trades",
        {"isin": isin, "quantity": quantity, "expected_revision": wa["revision"]},
        expected=201,
    )
calc, _ = a.request(
    path + "/calculations",
    {"expected_revision": wa["revision"], "valuation_date": DAY},
    expected=201,
)
assert calc["status"] == "success", [
    (x["isin"], x.get("error_code")) for x in calc["results"]
]
rows = {x["isin"]: x for x in calc["results"]}
assert all(x["provider"] == "moex" for x in rows.values())
assert rows["RU000A105A95"]["fx_source"] == "cbr"
assert rows["RU000A105A95"]["currency"] == "USD"
assert rows["RU000A105A95"]["settlement_currency"] == "RUB"
for x in rows.values():
    print(
        "DATA:",
        x["isin"],
        "MOEX",
        x["data_as_of"],
        "DV01",
        x["metric_value"],
        "FX",
        x.get("fx_source"),
        x.get("fx_rate_date"),
        flush=True,
    )
print("TOTAL:", calc["total_value"], flush=True)
assert abs(Decimal(calc["total_value"]) - Decimal("383.4813548556457260983")) < Decimal(
    "1e-9"
), "Live historical result differs from recorded control: investigate sources"
ok("Live MOEX + CBR on 2026-11-13 match recorded control within 1e-9 RUB")
b.request(path, expected=404)
a.request(
    path + "/trades",
    {"isin": "RU000A1038V6", "quantity": 1, "expected_revision": wa["revision"]},
    token=False,
    expected=403,
)
a.request(
    path + "/trades",
    {"isin": "RU000A1038V6", "quantity": 1, "expected_revision": 0},
    expected=409,
)
ok("Isolation 404, missing CSRF 403, stale revision 409")
wa, _ = a.request(
    path + "/trades",
    {"isin": "US0378331005", "quantity": 1, "expected_revision": wa["revision"]},
    expected=201,
)
unknown = next(x for x in wa["trades"] if x["isin"] == "US0378331005")
partial, _ = a.request(
    path + "/calculations",
    {"expected_revision": wa["revision"], "valuation_date": DAY},
    expected=201,
)
assert partial["status"] == "partial" and partial["total_value"] is None
err = next(x for x in partial["results"] if x["isin"] == "US0378331005")
assert err["metric_value"] is None and err["error_code"] == "INSTRUMENT_NOT_FOUND"
ok("Unknown instrument produces row error and null total, not zero")
first = next(x for x in wa["trades"] if x["isin"] == "RU000A1038V6")
wa, _ = a.request(
    path + "/trades/" + first["id"],
    {"quantity": 75, "expected_revision": wa["revision"]},
    method="PATCH",
)
old, _ = a.request("/api/v1/calculations/" + calc["id"])
assert old == calc
wa, _ = a.request(
    path + "/trades/" + unknown["id"] + "?expected_revision=" + str(wa["revision"]),
    method="DELETE",
)
restored, _ = a.request(path)
assert restored == wa and len(wa["trades"]) == 2
ok("Create/update/delete/reload work; saved historical calculation is unchanged")
metrics, h = a.request("/metrics")
assert "risk_http_requests_total" not in metrics
ok("Public frontend does not proxy internal Prometheus metrics")
print("PASS: live HTTPS integration acceptance", flush=True)
