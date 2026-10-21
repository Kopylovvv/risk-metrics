"""Проверка Compose на отдельной временной базе; рабочая база не используется."""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-port", type=int, default=18000)
    parser.add_argument("--db-port", type=int, default=15433)
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
        COOKIE_SECURE="false",
        ALLOWED_ORIGINS=f"http://127.0.0.1:{args.backend_port}",
    )
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    base = f"http://127.0.0.1:{args.backend_port}"

    def request(path, body=None, csrf=None):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with client.open(req, timeout=10) as response:
            return json.load(response)

    with tempfile.TemporaryDirectory(prefix=project) as temp:
        empty_env = Path(temp) / ".env"
        empty_env.touch()
        command = [
            "docker", "compose", "--project-name", project,
            "--env-file", str(empty_env), "-f", str(root / "compose.yaml"),
        ]

        def compose(*arguments, check=True):
            return subprocess.run(command + list(arguments), cwd=root, env=env, check=check)

        try:
            compose("config", "--quiet")
            compose("up", "--build", "--detach", "--wait", "--wait-timeout", "120")
            assert request("/health/live") == {"status": "ok"}
            assert request("/health/ready") == {"status": "ok", "database": "ok"}
            assert "/api/v1/workspaces" in request("/openapi.json")["paths"]
            workspace = request("/api/v1/workspaces", {})
            path = f"/api/v1/workspaces/{workspace['id']}"
            saved = request(
                path + "/trades",
                {"isin": "RU000A1038V6", "quantity": 17, "expected_revision": workspace["revision"]},
                workspace["csrf_token"],
            )
            assert saved["trades"][0]["quantity"] == 17
            print("OK: API, сессия, миграции и запись сделки в PostgreSQL", flush=True)

            compose("down")
            compose("up", "--detach", "--wait", "--wait-timeout", "120")
            restored = request(path)
            assert restored == saved, "Сохранённая сделка изменилась после пересоздания контейнеров"
            print("OK: данные и сессия сохранились после down/up", flush=True)

            compose("stop", "db")
            try:
                request("/health/ready")
            except urllib.error.HTTPError as error:
                assert error.code == 503
            else:
                raise AssertionError("Readiness должен вернуть 503 при недоступной БД")
            assert request("/health/live") == {"status": "ok"}
            print("OK: readiness видит отказ БД, процесс API остаётся жив", flush=True)

            compose("down")
            failure = Path(temp) / "failure.yaml"
            failure.write_text(
                'services:\n  migrate:\n    command: ["python", "-c", "raise SystemExit(23)"]\n',
                encoding="utf-8",
            )
            failed = compose(
                "-f", str(failure),
                "up", "--detach", "--wait", "--wait-timeout", "120", check=False,
            )
            assert failed.returncode != 0, "Ошибка миграции должна прервать запуск"
            exit_code = subprocess.check_output(
                command + ["ps", "--all", "--format", "{{.ExitCode}}", "migrate"],
                cwd=root, env=env, text=True,
            ).strip()
            assert exit_code == "23", "Должна сработать именно контрольная ошибка миграции"
            running = subprocess.check_output(
                command + ["ps", "--status", "running", "--services"],
                cwd=root, env=env, text=True,
            ).splitlines()
            assert "backend" not in running, "API не должен стартовать после ошибки миграции"
            print("OK: ошибка миграции блокирует запуск API", flush=True)
        except BaseException:
            compose("logs", "--no-color", "--tail=60", check=False)
            raise
        finally:
            # Только уникальный проект этого теста; рабочий risk-metrics не затрагивается.
            compose("down", "--volumes", "--remove-orphans")
    print("Compose: все проверки пройдены; временные контейнеры и база удалены.", flush=True)


if __name__ == "__main__":
    main()
