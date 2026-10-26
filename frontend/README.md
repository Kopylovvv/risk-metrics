# Frontend risk-metrics

React + TypeScript + Vite. Интерфейс работает с backend API или с контрольными ответами.

## Локальный запуск

Требуются Node.js и npm. Сборка проверена на Node.js 26.8.2.

Из папки frontend:

```bash
npm ci
cp .env.example .env
npm run dev
```

Шаблон .env включает `VITE_USE_MOCKS=true`: контрольные данные и расчёт выполняются в браузере, обращения к настоящему backend не выполняются.

Для работы с API установите в .env:

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api
VITE_USE_MOCKS=false
```

Backend должен быть запущен, например через Compose из корня репозитория.
Откройте frontend по http://localhost:5173. Для cookie используйте одинаковое имя хоста у frontend и backend: localhost с localhost либо 127.0.0.1 с 127.0.0.1. После изменения .env перезапустите Vite. Адрес frontend должен быть разрешён в backend ALLOWED_ORIGINS.

Для тестовой VM предварительно откройте SSH-туннель к API по инструкции задачи #19. Открытие API на localhost через туннель не разворачивает frontend на сервере: контейнеризация и общий запуск относятся к #17.

## Проверки

```bash
npm run lint
npm run build
```

`build` выполняет проверку типов и создаёт статику в dist/.
Сценарий src/tests/verify_task3.ts проверяет валидацию и контрольные ответы. Браузерную проверку с настоящим backend фиксируйте отдельно; контрольные данные не подтверждают доступность внешних источников или финансовую корректность методики.
