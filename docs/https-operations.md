# HTTPS стенда

Домен владельца — mhimf.net, поддомен — teahorses.mhimf.net.
В Timeweb установлена A-запись158.160.170.45, TTL600. Стоимость домена не менялась.

На VM входящий443 обслуживает существующий Caddy2.11.9. Его прежний сайт сохранён.
Конфигурация: `/opt/personal-torrserver/Caddyfile`; данные сертификатов — volume
`personal-torrserver_caddy_data`. Домен приложения имеет отдельный блок:

```caddyfile
teahorses.mhimf.net {
    tls {
        issuer acme {
            disable_http_challenge
        }
    }
    reverse_proxy risk-metrics-frontend-1:8080
}
```

Выдача и продление используют TLS-ALPN на443; порт80 обслуживает Nginx приложения
и переводит запросы для этого домена на HTTPS. Caddy подключён к внешней сети
`risk-metrics_default`; подключение сохранено в его Compose, чтобы переживать
пересоздание. Перед редактированием сохранены копии Caddyfile и Compose.

Серверный .env/override задаёт точный Origin https://teahorses.mhimf.net и
COOKIE_SECURE=true. Nginx сохраняет переданную Caddy схему HTTPS. Прямые порты API
и PostgreSQL остаются на loopback; мониторинг доступен через SSH-туннель.

Проверка: открыть домен, пройти `scripts/check_https.py`, проверить сертификат
и логи Caddy. Продление автоматическое; первая выдача и получение расписания
продления подтверждены18.11.2026, сертификат действует до16.02.2027.
Фактическое будущее продление проверяется ближе к сроку, не выдаётся за уже случившееся.
