"""Проверка Compose на отдельной временной базе; рабочая база не используется."""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-port", type=int, default=18000)
    parser.add_argument("--db-port", type=int, default=15433)
    parser.add_argument("--frontend-port", type=int, default=18080)
    parser.add_argument(
        "--keep", action="store_true", help="Оставить временное окружение для проверки браузером"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    project = "risk-metrics-smoke-" + secrets.token_hex(4)
    env = os.environ.copy()
    env.update(
        POSTGRES_DB="risk_metrics_smoke",
        POSTGRES_USER="risk_metrics_smoke",
        POSTGRES_PASSWORD=secrets.token_hex(24),
        POSTGRES_PUBLISHED_PORT=str(args.db_port),
        BACKEND_PORT=str(args.backend_port),
        FRONTEND_PORT=str(args.frontend_port),
        COOKIE_SECURE="false",
        ALLOWED_ORIGINS=f"http://127.0.0.1:{args.backend_port}",
        CBONDS_LOGIN="",
        CBONDS_PASSWORD="",
        CBONDS_URL="",
    )
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    base = f"http://127.0.0.1:{args.frontend_port}"

    def request(path, body=None, csrf=None):
        headers = {"Content-Type": "application/json", "Origin": base}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with client.open(req, timeout=10) as response:
            return json.load(response)

    with tempfile.TemporaryDirectory(prefix=project) as temp:
        keep = False
        empty_env = Path(temp) / ".env"
        empty_env.touch()
        command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(empty_env),
            "-f",
            str(root / "compose.yaml"),
        ]

        def compose(*arguments, check=True):
            return subprocess.run(command + list(arguments), cwd=root, env=env, check=check)

        def expected_error(path, code, body=None, csrf=None):
            try:
                request(path, body, csrf)
            except urllib.error.HTTPError as error:
                assert error.code == code, (path, error.code)
                return
            raise AssertionError(f"{path}: ожидался HTTP {code}")

        try:
            compose("config", "--quiet")
            compose("up", "--build", "--detach", "--wait", "--wait-timeout", "120")
            with client.open(base + "/", timeout=10) as response:
                page = response.read().decode()
                assert "text/html" in response.headers["Content-Type"]
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
            assert assets, "В HTML нет собранных ресурсов Vite"
            for asset in assets:
                with client.open(base + asset, timeout=10) as response:
                    assert response.status == 200 and response.read()
            with client.open(base + "/control-route", timeout=10) as response:
                assert response.read().decode() == page, "SPA fallback не работает"
            expected_error("/assets/missing.js", 404)
            expected_error("/api/v1/missing", 404)
            print("OK: Nginx отдаёт HTML и ресурсы, SPA и ошибки маршрутов", flush=True)
            assert request("/health/live") == {"status": "ok"}
            assert request("/health/ready") == {"status": "ok", "database": "ok"}
            workspace = request("/api/v1/workspaces", {})
            path = f"/api/v1/workspaces/{workspace['id']}"
            expected_error(
                path + "/trades",
                403,
                {
                    "isin": "RU000A1038V6",
                    "quantity": 17,
                    "expected_revision": workspace["revision"],
                },
            )
            saved = request(
                path + "/trades",
                {
                    "isin": "RU000A1038V6",
                    "quantity": 17,
                    "expected_revision": workspace["revision"],
                },
                workspace["csrf_token"],
            )
            assert saved["trades"][0]["quantity"] == 17
            print(
                "OK: API через Nginx, Origin, CSRF, сессия и запись сделки в PostgreSQL", flush=True
            )

            # Frontend остаётся работающим при пересоздании backend.
            compose("up", "--detach", "--no-deps", "--force-recreate", "--wait", "backend")
            deadline = time.monotonic() + 20
            while True:
                try:
                    assert request(path) == saved
                    break
                except (urllib.error.URLError, AssertionError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            print("OK: Nginx работает после пересоздания backend", flush=True)

            compose("down")
            compose("up", "--detach", "--wait", "--wait-timeout", "120")
            restored = request(path)
            assert restored == saved, "Сохранённая сделка изменилась после пересоздания контейнеров"
            print("OK: данные и сессия сохранились после down/up", flush=True)

            compose("stop", "db")
            expected_error("/health/ready", 503)
            assert request("/health/live") == {"status": "ok"}
            print("OK: readiness видит отказ БД, процесс API остаётся жив", flush=True)

            compose("down")
            failure = Path(temp) / "failure.yaml"
            failure.write_text(
                'services:\n  migrate:\n    command: ["python", "-c", "raise SystemExit(23)"]\n',
                encoding="utf-8",
            )
            failed = compose(
                "-f",
                str(failure),
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                "120",
                check=False,
            )
            assert failed.returncode != 0, "Ошибка миграции должна прервать запуск"
            exit_code = subprocess.check_output(
                command + ["ps", "--all", "--format", "{{.ExitCode}}", "migrate"],
                cwd=root,
                env=env,
                text=True,
            ).strip()
            assert exit_code == "23", "Должна сработать именно контрольная ошибка миграции"
            running = subprocess.check_output(
                command + ["ps", "--status", "running", "--services"],
                cwd=root,
                env=env,
                text=True,
            ).splitlines()
            assert not {"backend", "frontend"}.intersection(running), (
                "API и frontend не должны стартовать после ошибки миграции"
            )
            print("OK: ошибка миграции блокирует запуск backend и frontend", flush=True)
            if args.keep:
                compose("up", "--detach", "--wait", "--wait-timeout", "120")
                keep = True
                print(f"Окружение для браузера: {base}; проект {project}", flush=True)
        except BaseException:
            compose("logs", "--no-color", "--tail=60", check=False)
            raise
        finally:
            # Только уникальный проект этого теста; рабочий risk-metrics не затрагивается.
            if not keep:
                compose("down", "--volumes", "--remove-orphans")
    print(
        "Compose: все проверки пройдены."
        + (
            " Временное окружение оставлено (--keep)."
            if args.keep
            else " Временные контейнеры и база удалены."
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
