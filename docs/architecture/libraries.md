# Перечень библиотек и компонентов

> Файл генерирует скрипт `backend/app/tools/list_libraries.py`, вручную не правится.
> Обновить: `cd backend && .venv/bin/python -m app.tools.list_libraries`; проверить актуальность — с флагом `--check`.

Все сторонние библиотеки и компоненты «Альмы»: Python-пакеты backend и трёх сервисов, npm-пакеты интерфейса, образы контейнеров, шрифты и действия CI. Версии взяты из lock-файлов, по которым собираются образы (`uv sync --frozen`, `npm ci`), и из манифестов развёртывания.

## Сводка

| Группа | Всего | Состав | Источник |
|---|---|---|---|
| Python-пакеты | 95 | в образах — 76, для разработки и тестов — 12, только для других версий Python и ОС — 7 | `backend/uv.lock`, `services/notification/uv.lock`, `services/lms-stub/uv.lock`, `services/cms-stub/uv.lock`, `services/llm-gateway/uv.lock` |
| npm-пакеты | 397 | без признака `dev` — 186, с признаком `dev` (только сборка) — 135, платформенные бинарные сборки — 76 | `frontend/package-lock.json` |
| Образы контейнеров | 20 | собственные — 6, сторонние — 14 | Dockerfile, `deploy/`, `.github/workflows/` |
| Действия GitHub Actions | 3 | сборка и проверки в CI | CI |
| Шрифты | 1 | PDF отчётов | `backend/app/modules` |

Python-пакеты считаются парами «пакет — версия»: если проекты закрепили разные версии одного пакета, в перечне две строки. Интерфейс собирается в статические файлы, поэтому npm-пакеты нужны только при сборке; в браузер попадает код пакетов без признака `dev`, которые импортирует приложение. Признак `dev` ставит npm в `package-lock.json`: пакет из `devDependencies` может его не иметь, если он нужен другому пакету без этого признака (так у `vite` — peer-зависимости `@tailwindcss/vite`).

### Лицензии

| Лицензия | Python | npm | Образы |
|---|---|---|---|
| MIT | 45 | 322 | 1 |
| Apache-2.0 | 12 | 18 | 4 |
| ISC | — | 28 | — |
| BSD-3-Clause | 17 | 5 | 1 |
| MPL-2.0 | 1 | 12 | — |
| BSD-2-Clause | 1 | 6 | 1 |
| PSF-2.0 | 3 | — | 1 |
| 0BSD | — | 3 | — |
| AGPL-3.0 | — | — | 3 |
| Apache-2.0 OR BSD-2-Clause | 1 | — | — |
| Apache-2.0 OR BSD-3-Clause | 1 | — | — |
| BSD | 1 | — | — |
| BSD, Public Domain | 1 | — | — |
| BlueOak-1.0.0 | — | 1 | — |
| CC-BY-4.0 | — | 1 | — |
| LGPL-3.0-only | 1 | — | — |
| MIT AND PSF-2.0 | 1 | — | — |
| MIT OR Apache-2.0 | — | — | 1 |
| MIT-0 | 1 | — | — |
| MIT-CMU | 1 | — | — |
| MPL-2.0 AND (Apache-2.0 OR MIT) | 1 | — | — |
| OFL-1.1 | — | 1 | — |
| PostgreSQL | — | — | 1 |
| curl | — | — | 1 |

Пакеты и образы с copyleft-лицензиями, лицензиями шрифтов и без указанной лицензии. Все используются без изменений: библиотеки подключаются из реестров пакетов, образы запускаются как отдельные сервисы.

| Компонент | Версия | Лицензия | Где |
|---|---|---|---|
| certifi | 2026.7.22 | MPL-2.0 | Python: backend (транзитивная), notification (транзитивная), lms-stub (транзитивная), cms-stub (транзитивная), llm-gateway |
| fpdf2 | 2.8.9 | LGPL-3.0-only | Python: backend |
| orjson | 3.12.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | Python: backend |
| @fontsource-variable/inter | 5.3.0 | OFL-1.1 | npm (frontend) |
| caniuse-lite | 1.0.30001810 | CC-BY-4.0 | npm (frontend) |
| lightningcss | 1.32.0 | MPL-2.0 | npm (frontend) |
| lightningcss-* | 1.32.0 | MPL-2.0 | npm: платформенные сборки (11) |
| grafana/k6 | 0.57.0 | AGPL-3.0¹ | образ |
| quay.io/minio/mc | RELEASE.2025-08-13T08-35-41Z | AGPL-3.0¹ | образ |
| quay.io/minio/minio | RELEASE.2025-09-07T16-13-09Z | AGPL-3.0¹ | образ |

Лицензии с пометкой ¹ — справочные: пакета нет в окружении генератора (для образов и действий CI — лицензия основного ПО). Остальные лицензии Python взяты из метаданных установленных пакетов (`License-Expression`, `License`, классификаторы), npm — из `package-lock.json`.

## Python

Проекты: backend (`crm-backend`, `backend`), notification (`crm-notification`, `services/notification`), lms-stub (`lms-stub`, `services/lms-stub`), cms-stub (`cms-stub`, `services/cms-stub`), llm-gateway (`crm-llm-gateway`, `services/llm-gateway`). Маркеры зависимостей вычислены для платформы образов: Linux x86_64, CPython 3.12.

### Прямые зависимости — 27

| Пакет | Версия | Лицензия | Где | Назначение |
|---|---|---|---|---|
| aiogram | 3.31.0 | MIT¹ | notification | Telegram-бот (long-polling) |
| alembic | 1.20.0 | MIT | backend | миграции схемы БД |
| asyncpg | 0.31.0 | Apache-2.0 | backend | асинхронный драйвер PostgreSQL |
| certifi | 2026.7.22 | MPL-2.0 | backend (транзитивная), notification (транзитивная), lms-stub (транзитивная), cms-stub (транзитивная), llm-gateway | корневые сертификаты TLS |
| charset-normalizer | 3.5.1 | MIT | backend | определение кодировки импортируемых файлов |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | backend, notification (транзитивная) | шифрование ПДн (Fernet), подписи JWT |
| fastapi | 0.141.1 | MIT | backend, notification, lms-stub, cms-stub | веб-фреймворк REST API и Swagger UI |
| fastapi | 0.143.3 | MIT | llm-gateway | веб-фреймворк REST API и Swagger UI |
| fpdf2 | 2.8.9 | LGPL-3.0-only | backend | PDF отчётов с кириллицей |
| httpx | 0.28.1 | BSD-3-Clause | backend, notification, lms-stub, cms-stub, llm-gateway | HTTP-клиент: интеграции, Keycloak, notification-service |
| jinja2 | 3.1.6 | BSD-3-Clause¹ | notification | шаблоны текстов уведомлений |
| minio | 7.2.20 | Apache-2.0 | backend | клиент S3-хранилища MinIO |
| openpyxl | 3.1.5 | MIT | backend | чтение и запись XLSX |
| orjson | 3.12.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | backend | быстрая сериализация JSON |
| pydantic | 2.13.5 | MIT | backend, notification, lms-stub (транзитивная), cms-stub (транзитивная) | схемы запросов и ответов, валидация |
| pydantic | 2.14.1 | MIT | llm-gateway | схемы запросов и ответов, валидация |
| pydantic-settings | 2.15.0 | MIT | backend, notification | настройки из переменных окружения |
| pydantic-settings | 2.16.0 | MIT | llm-gateway | настройки из переменных окружения |
| pyjwt | 2.14.0 | MIT | backend, notification | проверка JWT Keycloak |
| python-multipart | 0.0.32 | Apache-2.0 | backend, lms-stub, cms-stub | загрузка файлов (multipart/form-data) |
| redis | 5.3.1 | MIT | notification | клиент KeyDB: кэш, pub/sub, идемпотентность |
| redis | 8.1.0 | MIT | backend | клиент KeyDB: кэш, pub/sub, идемпотентность |
| sqlalchemy | 2.0.54 | MIT | backend | ORM и построитель SQL (async) |
| sse-starlette | 3.4.11 | BSD-3-Clause | backend | Server-Sent Events |
| uvicorn | 0.53.0 | BSD-3-Clause | backend, notification, lms-stub, cms-stub | ASGI-сервер |
| uvicorn | 0.54.0 | BSD-3-Clause | llm-gateway | ASGI-сервер |
| xlrd | 2.0.2 | BSD | backend | чтение старого XLS (BIFF) |

### Транзитивные зависимости — 49

| Пакет | Версия | Лицензия | Где | Назначение |
|---|---|---|---|---|
| aiofiles | 25.1.0 | Apache-2.0¹ | notification | — |
| aiohappyeyeballs | 2.7.1 | PSF-2.0¹ | notification | — |
| aiohttp | 3.14.3 | Apache-2.0¹ | notification | HTTP-клиент aiogram |
| aiosignal | 1.4.0 | Apache-2.0¹ | notification | — |
| annotated-doc | 0.0.5 | MIT | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| annotated-types | 0.8.0 | MIT | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| anyio | 4.15.1 | MIT | backend, notification, lms-stub, cms-stub, llm-gateway | асинхронный ввод-вывод для Starlette и httpx |
| argon2-cffi | 25.1.0 | MIT | backend | зависимость клиента MinIO |
| argon2-cffi-bindings | 26.1.0 | MIT | backend | — |
| attrs | 26.1.0 | MIT¹ | notification | — |
| cffi | 2.1.1 | MIT-0 | backend, notification | — |
| click | 8.5.0 | BSD-3-Clause | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| defusedxml | 0.7.1 | PSF-2.0 | backend | безопасный разбор XML (fpdf2) |
| et-xmlfile | 2.0.0 | MIT | backend | потоковая запись XML для openpyxl |
| fonttools | 4.67.0 | MIT | backend | встраивание шрифтов в PDF (fpdf2) |
| frozenlist | 1.8.0 | Apache-2.0¹ | notification | — |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | backend | асинхронный режим SQLAlchemy |
| h11 | 0.16.0 | MIT | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| httpcore | 1.0.9 | BSD-3-Clause | backend, notification, lms-stub, cms-stub, llm-gateway | транспорт httpx |
| httptools | 0.8.0 | MIT | backend, notification, lms-stub, cms-stub | разбор HTTP для uvicorn |
| httptools | 0.9.0 | MIT | llm-gateway | разбор HTTP для uvicorn |
| idna | 3.19 | BSD-3-Clause | backend, notification, lms-stub, cms-stub | — |
| idna | 3.20 | BSD-3-Clause | llm-gateway | — |
| magic-filter | 1.0.12 | MIT¹ | notification | — |
| mako | 1.4.1 | MIT | backend | шаблоны миграций Alembic |
| markupsafe | 3.0.3 | BSD-3-Clause | backend, notification | экранирование в шаблонах |
| multidict | 6.8.0 | Apache-2.0¹ | notification | — |
| opentelemetry-api | 1.45.1 | Apache-2.0 | llm-gateway | — |
| pillow | 12.3.0 | MIT-CMU | backend | изображения в PDF (fpdf2) |
| propcache | 0.5.4 | Apache-2.0¹ | notification | — |
| pycparser | 3.0 | BSD-3-Clause | backend, notification | — |
| pycryptodome | 3.23.0 | BSD, Public Domain | backend | зависимость клиента MinIO |
| pydantic-core | 2.46.5 | MIT | backend, notification, lms-stub, cms-stub | ядро валидации Pydantic |
| pydantic-core | 2.50.1 | MIT | llm-gateway | ядро валидации Pydantic |
| python-dotenv | 1.2.3 | BSD-3-Clause | backend, notification, lms-stub, cms-stub | чтение .env в uvicorn |
| python-dotenv | 1.2.4 | BSD-3-Clause | llm-gateway | чтение .env в uvicorn |
| pyyaml | 6.0.3 | MIT | backend, notification, lms-stub, cms-stub, llm-gateway | конфигурация логирования uvicorn |
| starlette | 1.6.0 | BSD-3-Clause | backend, notification, lms-stub, cms-stub | ASGI-основа FastAPI: маршрутизация, middleware |
| starlette | 1.8.0 | BSD-3-Clause | llm-gateway | ASGI-основа FastAPI: маршрутизация, middleware |
| typing-extensions | 4.16.0 | PSF-2.0 | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| typing-inspection | 0.4.4 | MIT | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| urllib3 | 2.8.0 | MIT | backend | — |
| uvloop | 0.22.1 | MIT | backend, notification, lms-stub, cms-stub | быстрый цикл событий для uvicorn |
| uvloop | 0.23.0 | MIT | llm-gateway | быстрый цикл событий для uvicorn |
| watchfiles | 1.2.0 | MIT | backend, notification, lms-stub, cms-stub | перезапуск uvicorn при разработке |
| watchfiles | 1.3.0 | MIT | llm-gateway | перезапуск uvicorn при разработке |
| websockets | 17.1 | BSD-3-Clause | backend, notification, lms-stub, cms-stub | протокол WebSocket для uvicorn[standard] |
| websockets | 17.2 | BSD-3-Clause | llm-gateway | протокол WebSocket для uvicorn[standard] |
| yarl | 1.25.1 | Apache-2.0¹ | notification | — |

### Для разработки и тестов — 12

| Пакет | Версия | Лицензия | Где | Назначение |
|---|---|---|---|---|
| iniconfig | 2.3.0 | MIT | backend, notification, lms-stub, cms-stub | — |
| iniconfig | 2.3.1 | MIT | llm-gateway | — |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| pluggy | 1.6.0 | MIT | backend, notification, lms-stub, cms-stub | — |
| pluggy | 1.7.0 | MIT | llm-gateway | — |
| pygments | 2.21.0 | BSD-2-Clause | backend, notification, lms-stub, cms-stub, llm-gateway | — |
| pytest | 8.4.2 | MIT | lms-stub, cms-stub | тесты |
| pytest | 9.1.1 | MIT | backend, notification, llm-gateway | тесты |
| pytest-asyncio | 1.4.0 | Apache-2.0 | backend, notification, llm-gateway | асинхронные тесты |
| ruff | 0.16.7 | MIT | notification | линтер и форматтер |
| ruff | 0.16.8 | MIT | backend, lms-stub, cms-stub | линтер и форматтер |
| ruff | 0.17.1 | MIT | llm-gateway | линтер и форматтер |

### Не ставятся на Linux x86_64, CPython 3.12 — 7

Есть в lock-файлах для других версий Python и ОС; в образы не попадают.

| Пакет | Версия | Лицензия | Условие установки | Где |
|---|---|---|---|---|
| async-timeout | 5.0.1 | Apache-2.0¹ | python_full_version < '3.11.3' | backend, notification |
| backports-asyncio-runner | 1.2.0 | PSF-2.0¹ | python_full_version < '3.11' | backend, notification |
| colorama | 0.4.6 | BSD-3-Clause¹ | sys_platform == 'win32' | backend, notification, lms-stub, cms-stub, llm-gateway |
| exceptiongroup | 1.3.1 | MIT | python_full_version < '3.11' | backend, notification, lms-stub, cms-stub |
| fonttools | 4.65.0 | MIT | python_full_version < '3.11' | backend |
| tomli | 2.4.1 | MIT | python_full_version < '3.11' | backend, notification, lms-stub, cms-stub |
| websockets | 16.1.1 | BSD-3-Clause | python_full_version < '3.11' | backend, notification, lms-stub, cms-stub |

## npm (frontend)

Прямые зависимости — из `frontend/package.json`; транзитивные «сборки» — пакеты с признаком `dev` в `package-lock.json`, нужные только для сборки и проверки типов.

### Прямые зависимости (`dependencies`) — 48

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| @dnd-kit/core | 6.3.1 | MIT | перетаскивание карточек доски |
| @dnd-kit/sortable | 10.0.0 | MIT | сортировка перетаскиванием |
| @dnd-kit/utilities | 3.2.2 | MIT | утилиты dnd-kit |
| @fontsource-variable/inter | 5.3.0 | OFL-1.1 | шрифт Inter без CDN |
| @hookform/resolvers | 5.9.1 | MIT | валидация форм схемами zod |
| @radix-ui/react-alert-dialog | 1.1.23 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-avatar | 1.2.6 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-checkbox | 1.3.11 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-collapsible | 1.1.20 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-dialog | 1.1.23 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-dropdown-menu | 2.1.24 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-label | 2.1.15 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-popover | 1.1.23 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-progress | 1.1.16 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-radio-group | 1.4.7 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-scroll-area | 1.2.18 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-select | 2.3.7 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-separator | 1.1.15 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-slot | 1.3.3 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-switch | 1.3.7 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-tabs | 1.1.21 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-toggle-group | 1.1.19 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-tooltip | 1.2.16 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @tailwindcss/vite | 4.3.3 | MIT | подключение Tailwind к сборке Vite |
| @tanstack/react-query | 5.103.0 | MIT | запросы к API и кэш данных |
| @tanstack/react-table | 8.21.3 | MIT | таблицы-реестры с колонками и сортировкой |
| @xyflow/react | 12.11.6 | MIT | визуальный конструктор процессов (граф этапов) |
| class-variance-authority | 0.7.1 | Apache-2.0 | варианты компонентов (shadcn/ui) |
| clsx | 2.1.1 | MIT | сборка CSS-классов |
| cmdk | 1.1.1 | MIT | командная палитра и поиск |
| dayjs | 1.11.23 | MIT | даты и периоды |
| echarts | 5.6.0 | Apache-2.0 | графики аналитических панелей |
| echarts-for-react | 3.0.6 | MIT | обёртка ECharts для React |
| keycloak-js | 26.2.4 | Apache-2.0 | вход через Keycloak (OIDC + PKCE) |
| lucide-react | 1.48.0 | ISC | иконки |
| motion | 12.43.0 | MIT | анимации интерфейса |
| qrcode | 1.5.4 | MIT | QR-код привязки Telegram |
| react | 18.3.1 | MIT | библиотека интерфейса |
| react-day-picker | 9.14.0 | MIT | выбор дат и периодов |
| react-dom | 18.3.1 | MIT | отрисовка React в браузере |
| react-hook-form | 7.89.0 | MIT | формы |
| react-router-dom | 6.30.6 | MIT | маршрутизация экранов |
| sonner | 2.0.8 | MIT | всплывающие уведомления |
| tailwind-merge | 3.7.0 | MIT | слияние классов Tailwind |
| tailwindcss | 4.3.3 | MIT | CSS-утилиты и токены оформления |
| tw-animate-css | 1.4.0 | MIT | анимации для Tailwind |
| zod | 3.25.76 | MIT | схемы валидации данных |
| zustand | 5.0.15 | MIT | состояние интерфейса |

### Прямые зависимости сборки (`devDependencies`) — 9

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| @types/qrcode | 1.5.6 | MIT | типы qrcode |
| @types/react | 18.3.31 | MIT | типы React |
| @types/react-dom | 18.3.7 | MIT | типы React DOM |
| @typescript-eslint/parser | 8.71.0 | MIT | разбор TypeScript для ESLint |
| @vitejs/plugin-react | 4.7.0 | MIT | React в сборке Vite |
| eslint | 10.12.0 | MIT | проверка правил хуков React (npm run lint) |
| eslint-plugin-react-hooks | 7.1.1 | MIT | правила хуков React для ESLint |
| typescript | 5.8.3 | Apache-2.0 | проверка типов |
| vite | 6.4.3 | MIT | сборка и dev-сервер |

### Транзитивные зависимости — 137

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| @date-fns/tz | 1.5.0 | MIT | — |
| @dnd-kit/accessibility | 3.1.1 | MIT | — |
| @floating-ui/core | 1.8.0 | MIT | — |
| @floating-ui/dom | 1.8.0 | MIT | — |
| @floating-ui/react-dom | 2.1.9 | MIT | — |
| @floating-ui/utils | 0.2.12 | MIT | — |
| @jridgewell/gen-mapping | 0.3.13 | MIT | — |
| @jridgewell/remapping | 2.3.5 | MIT | — |
| @jridgewell/resolve-uri | 3.1.2 | MIT | — |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT | — |
| @jridgewell/trace-mapping | 0.3.31 | MIT | — |
| @radix-ui/number | 1.1.3 | MIT | — |
| @radix-ui/primitive | 1.1.7 | MIT | — |
| @radix-ui/react-arrow | 1.1.15 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-collection | 1.1.15 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-compose-refs | 1.1.5 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-context | 1.2.2 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-direction | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-dismissable-layer | 1.1.19 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-focus-guards | 1.1.6 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-focus-scope | 1.1.16 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-id | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-menu | 2.1.24 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-popper | 1.3.7 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-portal | 1.1.17 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-presence | 1.1.10 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-primitive | 2.1.10 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-roving-focus | 1.1.19 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-toggle | 1.1.18 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-callback-ref | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-controllable-state | 1.2.6 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-effect-event | 0.0.5 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-is-hydrated | 0.1.3 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-layout-effect | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-previous | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-rect | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-use-size | 1.1.4 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/react-visually-hidden | 1.2.11 | MIT | примитив интерфейса Radix (основа shadcn/ui) |
| @radix-ui/rect | 1.1.3 | MIT | — |
| @remix-run/router | 1.23.4 | MIT | — |
| @standard-schema/utils | 0.3.0 | MIT | — |
| @tabby_ai/hijri-converter | 1.0.5 | MIT | — |
| @tailwindcss/node | 4.3.3 | MIT | — |
| @tailwindcss/oxide | 4.3.3 | MIT | — |
| @tanstack/query-core | 5.103.0 | MIT | — |
| @tanstack/table-core | 8.21.3 | MIT | — |
| @types/d3-color | 3.1.3 | MIT | — |
| @types/d3-drag | 3.0.7 | MIT | — |
| @types/d3-interpolate | 3.0.4 | MIT | — |
| @types/d3-selection | 3.0.12 | MIT | — |
| @types/d3-transition | 3.0.9 | MIT | — |
| @types/d3-zoom | 3.0.8 | MIT | — |
| @types/estree | 1.0.9 | MIT | — |
| @xyflow/system | 0.0.82 | MIT | — |
| ajv | 8.20.0 | MIT | — |
| ansi-regex | 5.0.1 | MIT | — |
| ansi-styles | 4.3.0 | MIT | — |
| aria-hidden | 1.2.6 | MIT | — |
| camelcase | 5.3.1 | MIT | — |
| classcat | 5.0.5 | MIT | — |
| cliui | 6.0.0 | ISC | — |
| color-convert | 2.0.1 | MIT | — |
| color-name | 1.1.4 | MIT | — |
| d3-color | 3.1.0 | ISC | — |
| d3-dispatch | 3.0.1 | ISC | — |
| d3-drag | 3.0.0 | ISC | — |
| d3-ease | 3.0.1 | BSD-3-Clause | — |
| d3-interpolate | 3.0.1 | ISC | — |
| d3-selection | 3.0.0 | ISC | — |
| d3-timer | 3.0.1 | ISC | — |
| d3-transition | 3.0.1 | ISC | — |
| d3-zoom | 3.0.0 | ISC | — |
| date-fns | 4.4.0 | MIT | — |
| date-fns-jalali | 4.1.0-0 | MIT | — |
| decamelize | 1.2.0 | MIT | — |
| detect-libc | 2.1.2 | Apache-2.0 | — |
| detect-node-es | 1.1.0 | MIT | — |
| dijkstrajs | 1.0.3 | MIT | — |
| emoji-regex | 8.0.0 | MIT | — |
| enhanced-resolve | 5.25.1 | MIT | — |
| esbuild | 0.25.12 | MIT | — |
| fast-deep-equal | 3.1.3 | MIT | — |
| fast-uri | 3.1.8 | BSD-3-Clause | — |
| fdir | 6.5.0 | MIT | — |
| find-up | 4.1.0 | MIT | — |
| framer-motion | 12.43.0 | MIT | — |
| get-caller-file | 2.0.5 | ISC | — |
| get-nonce | 1.0.1 | MIT | — |
| graceful-fs | 4.2.11 | ISC | — |
| is-fullwidth-code-point | 3.0.0 | MIT | — |
| jiti | 2.7.0 | MIT | — |
| js-tokens | 4.0.0 | MIT | — |
| json-schema-traverse | 1.0.0 | MIT | — |
| lightningcss | 1.32.0 | MPL-2.0 | — |
| locate-path | 5.0.0 | MIT | — |
| loose-envify | 1.4.0 | MIT | — |
| magic-string | 0.30.21 | MIT | — |
| motion-dom | 12.43.0 | MIT | — |
| motion-utils | 12.39.0 | MIT | — |
| nanoid | 3.3.19 | MIT | — |
| p-limit | 2.3.0 | MIT | — |
| p-locate | 4.1.0 | MIT | — |
| p-try | 2.2.0 | MIT | — |
| path-exists | 4.0.0 | MIT | — |
| picocolors | 1.1.1 | ISC | — |
| picomatch | 4.0.7 | MIT | — |
| pngjs | 5.0.0 | MIT | — |
| postcss | 8.5.28 | MIT | — |
| react-remove-scroll | 2.7.2 | MIT | — |
| react-remove-scroll-bar | 2.3.8 | MIT | — |
| react-router | 6.30.6 | MIT | — |
| react-style-singleton | 2.2.3 | MIT | — |
| require-directory | 2.1.1 | MIT | — |
| require-from-string | 2.0.2 | MIT | — |
| require-main-filename | 2.0.0 | ISC | — |
| rollup | 4.63.3 | MIT | — |
| scheduler | 0.23.2 | MIT | — |
| set-blocking | 2.0.0 | ISC | — |
| size-sensor | 1.0.3 | ISC | — |
| source-map-js | 1.2.1 | BSD-3-Clause | — |
| string-width | 4.2.3 | MIT | — |
| strip-ansi | 6.0.1 | MIT | — |
| tapable | 2.3.3 | MIT | — |
| tinyglobby | 0.2.17 | MIT | — |
| tslib | 2.3.0 | 0BSD | — |
| tslib | 2.3.0 | 0BSD | — |
| tslib | 2.8.1 | 0BSD | — |
| use-callback-ref | 1.3.3 | MIT | — |
| use-sidecar | 1.1.3 | MIT | — |
| use-sync-external-store | 1.7.0 | MIT | — |
| which-module | 2.0.1 | ISC | — |
| wrap-ansi | 6.2.0 | MIT | — |
| y18n | 4.0.3 | ISC | — |
| yargs | 15.4.1 | MIT | — |
| yargs-parser | 18.1.3 | ISC | — |
| zrender | 5.6.1 | BSD-3-Clause | — |
| zustand | 4.5.7 | MIT | состояние интерфейса |

### Транзитивные зависимости сборки — 127

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| @babel/code-frame | 7.29.7 | MIT | — |
| @babel/compat-data | 7.29.7 | MIT | — |
| @babel/core | 7.29.7 | MIT | — |
| @babel/generator | 7.29.8 | MIT | — |
| @babel/helper-compilation-targets | 7.29.7 | MIT | — |
| @babel/helper-globals | 7.29.7 | MIT | — |
| @babel/helper-module-imports | 7.29.7 | MIT | — |
| @babel/helper-module-transforms | 7.29.7 | MIT | — |
| @babel/helper-plugin-utils | 7.29.7 | MIT | — |
| @babel/helper-string-parser | 7.29.7 | MIT | — |
| @babel/helper-validator-identifier | 7.29.7 | MIT | — |
| @babel/helper-validator-option | 7.29.7 | MIT | — |
| @babel/helpers | 7.29.7 | MIT | — |
| @babel/parser | 7.29.8 | MIT | — |
| @babel/plugin-transform-react-jsx-self | 7.29.7 | MIT | — |
| @babel/plugin-transform-react-jsx-source | 7.29.7 | MIT | — |
| @babel/template | 7.29.7 | MIT | — |
| @babel/traverse | 7.29.8 | MIT | — |
| @babel/types | 7.29.8 | MIT | — |
| @cacheable/memory | 2.2.0 | MIT | — |
| @cacheable/utils | 2.5.0 | MIT | — |
| @eslint-community/eslint-utils | 4.10.1 | MIT | — |
| @eslint-community/regexpp | 4.12.2 | MIT | — |
| @eslint/config-array | 0.23.5 | Apache-2.0 | — |
| @eslint/config-helpers | 0.7.0 | Apache-2.0 | — |
| @eslint/core | 1.2.1 | Apache-2.0 | — |
| @eslint/object-schema | 3.0.5 | Apache-2.0 | — |
| @eslint/plugin-kit | 0.7.3 | Apache-2.0 | — |
| @humanfs/core | 0.19.2 | Apache-2.0 | — |
| @humanfs/node | 0.16.8 | Apache-2.0 | — |
| @humanfs/types | 0.15.0 | Apache-2.0 | — |
| @humanwhocodes/module-importer | 1.0.1 | Apache-2.0 | — |
| @humanwhocodes/retry | 0.4.3 | Apache-2.0 | — |
| @keyv/bigmap | 1.3.1 | MIT | — |
| @keyv/serialize | 1.1.1 | MIT | — |
| @rolldown/pluginutils | 1.0.0-beta.27 | MIT | — |
| @types/babel__core | 7.20.5 | MIT | — |
| @types/babel__generator | 7.27.0 | MIT | — |
| @types/babel__template | 7.4.4 | MIT | — |
| @types/babel__traverse | 7.28.0 | MIT | — |
| @types/esrecurse | 4.3.1 | MIT | — |
| @types/json-schema | 7.0.15 | MIT | — |
| @types/node | 22.20.3 | MIT | — |
| @types/prop-types | 15.7.15 | MIT | — |
| @typescript-eslint/project-service | 8.71.0 | MIT | — |
| @typescript-eslint/scope-manager | 8.71.0 | MIT | — |
| @typescript-eslint/tsconfig-utils | 8.71.0 | MIT | — |
| @typescript-eslint/types | 8.71.0 | MIT | — |
| @typescript-eslint/typescript-estree | 8.71.0 | MIT | — |
| @typescript-eslint/visitor-keys | 8.71.0 | MIT | — |
| acorn | 8.19.0 | MIT | — |
| acorn-jsx | 5.3.2 | MIT | — |
| ajv | 6.15.0 | MIT | — |
| balanced-match | 4.0.4 | MIT | — |
| baseline-browser-mapping | 2.11.24 | Apache-2.0 | — |
| brace-expansion | 5.0.12 | MIT | — |
| browserslist | 4.29.0 | MIT | — |
| cacheable | 2.5.0 | MIT | — |
| caniuse-lite | 1.0.30001810 | CC-BY-4.0 | — |
| convert-source-map | 2.0.0 | MIT | — |
| cross-spawn | 7.0.6 | MIT | — |
| csstype | 3.2.3 | MIT | — |
| debug | 4.4.3 | MIT | — |
| deep-is | 0.1.4 | MIT | — |
| electron-to-chromium | 1.5.430 | ISC | — |
| escalade | 3.2.0 | MIT | — |
| escape-string-regexp | 4.0.0 | MIT | — |
| eslint-scope | 9.1.2 | BSD-2-Clause | — |
| eslint-visitor-keys | 3.4.3 | Apache-2.0 | — |
| eslint-visitor-keys | 5.0.1 | Apache-2.0 | — |
| espree | 11.2.0 | BSD-2-Clause | — |
| esquery | 1.7.0 | BSD-3-Clause | — |
| esrecurse | 4.3.0 | BSD-2-Clause | — |
| estraverse | 5.3.0 | BSD-2-Clause | — |
| esutils | 2.0.3 | BSD-2-Clause | — |
| fast-json-stable-stringify | 2.1.0 | MIT | — |
| fast-levenshtein | 2.0.6 | MIT | — |
| file-entry-cache | 11.1.5 | MIT | — |
| find-up | 5.0.0 | MIT | — |
| flat-cache | 6.1.23 | MIT | — |
| flatted | 3.4.4 | ISC | — |
| gensync | 1.0.0-beta.2 | MIT | — |
| glob-parent | 6.0.2 | ISC | — |
| hashery | 1.5.1 | MIT | — |
| hermes-estree | 0.25.1 | MIT | — |
| hermes-parser | 0.25.1 | MIT | — |
| hookified | 1.15.1 | MIT | — |
| hookified | 2.2.0 | MIT | — |
| ignore | 5.3.2 | MIT | — |
| imurmurhash | 0.1.4 | MIT | — |
| is-extglob | 2.1.1 | MIT | — |
| is-glob | 4.0.3 | MIT | — |
| isexe | 2.0.0 | ISC | — |
| jsesc | 3.1.0 | MIT | — |
| json-schema-traverse | 0.4.1 | MIT | — |
| json-stable-stringify-without-jsonify | 1.0.1 | MIT | — |
| json5 | 2.2.3 | MIT | — |
| keyv | 5.6.0 | MIT | — |
| levn | 0.4.1 | MIT | — |
| locate-path | 6.0.0 | MIT | — |
| lru-cache | 5.1.1 | ISC | — |
| minimatch | 10.2.6 | BlueOak-1.0.0 | — |
| ms | 2.1.3 | MIT | — |
| natural-compare | 1.4.0 | MIT | — |
| node-releases | 2.0.55 | MIT | — |
| optionator | 0.9.4 | MIT | — |
| p-limit | 3.1.0 | MIT | — |
| p-locate | 5.0.0 | MIT | — |
| path-key | 3.1.1 | MIT | — |
| prelude-ls | 1.2.1 | MIT | — |
| punycode | 2.3.1 | MIT | — |
| qified | 0.10.1 | MIT | — |
| react-refresh | 0.17.0 | MIT | — |
| semver | 6.3.1 | ISC | — |
| semver | 7.8.5 | ISC | — |
| shebang-command | 2.0.0 | MIT | — |
| shebang-regex | 3.0.0 | MIT | — |
| ts-api-utils | 2.5.0 | MIT | — |
| type-check | 0.4.0 | MIT | — |
| undici-types | 6.21.0 | MIT | — |
| update-browserslist-db | 1.3.3 | MIT | — |
| uri-js | 4.4.1 | BSD-2-Clause | — |
| which | 2.0.2 | ISC | — |
| word-wrap | 1.2.5 | MIT | — |
| yallist | 3.1.1 | ISC | — |
| yocto-queue | 0.1.0 | MIT | — |
| zod-validation-error | 4.0.2 | MIT | — |

### Платформенные бинарные сборки — 76

Необязательные пакеты с бинарниками инструментов сборки (esbuild, Rollup, Tailwind, Lightning CSS): npm ставит только сборку под текущую ОС и процессор.

| Пакет | Версия | Лицензия | ОС / процессор |
|---|---|---|---|
| @esbuild/aix-ppc64 | 0.25.12 | MIT | aix/ppc64 |
| @esbuild/android-arm | 0.25.12 | MIT | android/arm |
| @esbuild/android-arm64 | 0.25.12 | MIT | android/arm64 |
| @esbuild/android-x64 | 0.25.12 | MIT | android/x64 |
| @esbuild/darwin-arm64 | 0.25.12 | MIT | darwin/arm64 |
| @esbuild/darwin-x64 | 0.25.12 | MIT | darwin/x64 |
| @esbuild/freebsd-arm64 | 0.25.12 | MIT | freebsd/arm64 |
| @esbuild/freebsd-x64 | 0.25.12 | MIT | freebsd/x64 |
| @esbuild/linux-arm | 0.25.12 | MIT | linux/arm |
| @esbuild/linux-arm64 | 0.25.12 | MIT | linux/arm64 |
| @esbuild/linux-ia32 | 0.25.12 | MIT | linux/ia32 |
| @esbuild/linux-loong64 | 0.25.12 | MIT | linux/loong64 |
| @esbuild/linux-mips64el | 0.25.12 | MIT | linux/mips64el |
| @esbuild/linux-ppc64 | 0.25.12 | MIT | linux/ppc64 |
| @esbuild/linux-riscv64 | 0.25.12 | MIT | linux/riscv64 |
| @esbuild/linux-s390x | 0.25.12 | MIT | linux/s390x |
| @esbuild/linux-x64 | 0.25.12 | MIT | linux/x64 |
| @esbuild/netbsd-arm64 | 0.25.12 | MIT | netbsd/arm64 |
| @esbuild/netbsd-x64 | 0.25.12 | MIT | netbsd/x64 |
| @esbuild/openbsd-arm64 | 0.25.12 | MIT | openbsd/arm64 |
| @esbuild/openbsd-x64 | 0.25.12 | MIT | openbsd/x64 |
| @esbuild/openharmony-arm64 | 0.25.12 | MIT | openharmony/arm64 |
| @esbuild/sunos-x64 | 0.25.12 | MIT | sunos/x64 |
| @esbuild/win32-arm64 | 0.25.12 | MIT | win32/arm64 |
| @esbuild/win32-ia32 | 0.25.12 | MIT | win32/ia32 |
| @esbuild/win32-x64 | 0.25.12 | MIT | win32/x64 |
| @napi-rs/lzma-linux-x64-gnu | 1.5.1 | MIT | linux/x64 |
| @rollup/rollup-android-arm-eabi | 4.63.3 | MIT | android/arm |
| @rollup/rollup-android-arm64 | 4.63.3 | MIT | android/arm64 |
| @rollup/rollup-darwin-arm64 | 4.63.3 | MIT | darwin/arm64 |
| @rollup/rollup-darwin-x64 | 4.63.3 | MIT | darwin/x64 |
| @rollup/rollup-freebsd-arm64 | 4.63.3 | MIT | freebsd/arm64 |
| @rollup/rollup-freebsd-x64 | 4.63.3 | MIT | freebsd/x64 |
| @rollup/rollup-linux-arm-gnueabihf | 4.63.3 | MIT | linux/arm |
| @rollup/rollup-linux-arm-musleabihf | 4.63.3 | MIT | linux/arm |
| @rollup/rollup-linux-arm64-gnu | 4.63.3 | MIT | linux/arm64 |
| @rollup/rollup-linux-arm64-musl | 4.63.3 | MIT | linux/arm64 |
| @rollup/rollup-linux-loong64-gnu | 4.63.3 | MIT | linux/loong64 |
| @rollup/rollup-linux-loong64-musl | 4.63.3 | MIT | linux/loong64 |
| @rollup/rollup-linux-ppc64-gnu | 4.63.3 | MIT | linux/ppc64 |
| @rollup/rollup-linux-ppc64-musl | 4.63.3 | MIT | linux/ppc64 |
| @rollup/rollup-linux-riscv64-gnu | 4.63.3 | MIT | linux/riscv64 |
| @rollup/rollup-linux-riscv64-musl | 4.63.3 | MIT | linux/riscv64 |
| @rollup/rollup-linux-s390x-gnu | 4.63.3 | MIT | linux/s390x |
| @rollup/rollup-linux-x64-gnu | 4.63.3 | MIT | linux/x64 |
| @rollup/rollup-linux-x64-musl | 4.63.3 | MIT | linux/x64 |
| @rollup/rollup-openbsd-x64 | 4.63.3 | MIT | openbsd/x64 |
| @rollup/rollup-openharmony-arm64 | 4.63.3 | MIT | openharmony/arm64 |
| @rollup/rollup-win32-arm64-msvc | 4.63.3 | MIT | win32/arm64 |
| @rollup/rollup-win32-ia32-msvc | 4.63.3 | MIT | win32/ia32 |
| @rollup/rollup-win32-x64-gnu | 4.63.3 | MIT | win32/x64 |
| @rollup/rollup-win32-x64-msvc | 4.63.3 | MIT | win32/x64 |
| @tailwindcss/oxide-android-arm64 | 4.3.3 | MIT | android/arm64 |
| @tailwindcss/oxide-darwin-arm64 | 4.3.3 | MIT | darwin/arm64 |
| @tailwindcss/oxide-darwin-x64 | 4.3.3 | MIT | darwin/x64 |
| @tailwindcss/oxide-freebsd-x64 | 4.3.3 | MIT | freebsd/x64 |
| @tailwindcss/oxide-linux-arm-gnueabihf | 4.3.3 | MIT | linux/arm |
| @tailwindcss/oxide-linux-arm64-gnu | 4.3.3 | MIT | linux/arm64 |
| @tailwindcss/oxide-linux-arm64-musl | 4.3.3 | MIT | linux/arm64 |
| @tailwindcss/oxide-linux-x64-gnu | 4.3.3 | MIT | linux/x64 |
| @tailwindcss/oxide-linux-x64-musl | 4.3.3 | MIT | linux/x64 |
| @tailwindcss/oxide-wasm32-wasi | 4.3.3 | MIT | wasm32 |
| @tailwindcss/oxide-win32-arm64-msvc | 4.3.3 | MIT | win32/arm64 |
| @tailwindcss/oxide-win32-x64-msvc | 4.3.3 | MIT | win32/x64 |
| fsevents | 2.3.3 | MIT | darwin |
| lightningcss-android-arm64 | 1.32.0 | MPL-2.0 | android/arm64 |
| lightningcss-darwin-arm64 | 1.32.0 | MPL-2.0 | darwin/arm64 |
| lightningcss-darwin-x64 | 1.32.0 | MPL-2.0 | darwin/x64 |
| lightningcss-freebsd-x64 | 1.32.0 | MPL-2.0 | freebsd/x64 |
| lightningcss-linux-arm-gnueabihf | 1.32.0 | MPL-2.0 | linux/arm |
| lightningcss-linux-arm64-gnu | 1.32.0 | MPL-2.0 | linux/arm64 |
| lightningcss-linux-arm64-musl | 1.32.0 | MPL-2.0 | linux/arm64 |
| lightningcss-linux-x64-gnu | 1.32.0 | MPL-2.0 | linux/x64 |
| lightningcss-linux-x64-musl | 1.32.0 | MPL-2.0 | linux/x64 |
| lightningcss-win32-arm64-msvc | 1.32.0 | MPL-2.0 | win32/arm64 |
| lightningcss-win32-x64-msvc | 1.32.0 | MPL-2.0 | win32/x64 |

## Образы контейнеров

### Собственные образы

| Образ | Dockerfile | Базовые образы |
|---|---|---|
| crm/backend | `backend/Dockerfile` | ghcr.io/astral-sh/uv:python3.12-bookworm-slim, python:3.12-slim-bookworm |
| crm/cms-stub | `services/cms-stub/Dockerfile` | ghcr.io/astral-sh/uv:python3.12-bookworm-slim, python:3.12-slim-bookworm |
| crm/frontend | `frontend/Dockerfile` | node:22-alpine, nginxinc/nginx-unprivileged:1.27-alpine |
| crm/llm-gateway | `services/llm-gateway/Dockerfile` | ghcr.io/astral-sh/uv:python3.12-bookworm-slim, python:3.12-slim-bookworm |
| crm/lms-stub | `services/lms-stub/Dockerfile` | ghcr.io/astral-sh/uv:python3.12-bookworm-slim, python:3.12-slim-bookworm |
| crm/notification | `services/notification/Dockerfile` | ghcr.io/astral-sh/uv:python3.12-bookworm-slim, python:3.12-slim-bookworm |

### Сторонние образы

| Образ | Тег | Лицензия | Назначение | Где используется |
|---|---|---|---|---|
| curlimages/curl | 8.10.1 | curl¹ | вызов служебных эндпоинтов из CronJob и init-контейнеров | `deploy/k8s/base/cronjobs/idempotency-purge-cronjob.yaml`, `deploy/k8s/base/cronjobs/purge-deleted-cronjob.yaml`, `deploy/k8s/base/cronjobs/stuck-check-cronjob.yaml`, `deploy/k8s/base/jobs/minio-init-job.yaml`, `deploy/k8s/base/notification/deployment.yaml` |
| eqalpha/keydb | sha256:6537505c4235… (v6.3.4) | BSD-3-Clause¹ | кэш, pub/sub событий SSE, идемпотентность | `deploy/compose/docker-compose.dev.yml`, `deploy/k8s/base/keydb/deployment.yaml` |
| ghcr.io/astral-sh/uv | python3.12-bookworm-slim | MIT OR Apache-2.0¹ | установка зависимостей по uv.lock | `backend/Dockerfile`, `services/cms-stub/Dockerfile`, `services/llm-gateway/Dockerfile`, `services/lms-stub/Dockerfile`, `services/notification/Dockerfile` |
| grafana/k6 | 0.57.0 | AGPL-3.0¹ | нагрузочное тестирование | `deploy/load/README.md` |
| nginxinc/nginx-unprivileged | 1.27-alpine | BSD-2-Clause¹ | раздача интерфейса без root | `frontend/Dockerfile` |
| node | 22-alpine | MIT¹ | сборка интерфейса (стадия build) | `frontend/Dockerfile` |
| postgres | 16-alpine | PostgreSQL¹ | СУБД CRM и Keycloak; psql и pg_isready в init-контейнерах | `.github/workflows/ci.yml`, `deploy/compose/docker-compose.dev.yml`, `deploy/k8s/base/backend/deployment.yaml`, `deploy/k8s/base/jobs/db-migrate-job.yaml`, `deploy/k8s/base/jobs/seed-demo-job.yaml`, `deploy/k8s/base/keycloak/deployment.yaml`, `deploy/k8s/base/postgres/statefulset.yaml` |
| python | 3.12-slim-bookworm | PSF-2.0¹ | среда выполнения Python-сервисов (runtime-стадия) | `backend/Dockerfile`, `services/cms-stub/Dockerfile`, `services/llm-gateway/Dockerfile`, `services/lms-stub/Dockerfile`, `services/notification/Dockerfile` |
| quay.io/jetstack/cert-manager-cainjector | v1.21.2 | Apache-2.0¹ | TLS-сертификаты стенда на ВМ | `deploy/k8s/vm-addons/cert-manager.yaml` |
| quay.io/jetstack/cert-manager-controller | v1.21.2 | Apache-2.0¹ | TLS-сертификаты стенда на ВМ | `deploy/k8s/vm-addons/cert-manager.yaml` |
| quay.io/jetstack/cert-manager-webhook | v1.21.2 | Apache-2.0¹ | TLS-сертификаты стенда на ВМ | `deploy/k8s/vm-addons/cert-manager.yaml` |
| quay.io/keycloak/keycloak | 26.0 | Apache-2.0¹ | вход пользователей и сервисов (OIDC) | `deploy/compose/docker-compose.dev.yml`, `deploy/k8s/base/keycloak/deployment.yaml` |
| quay.io/minio/mc | RELEASE.2025-08-13T08-35-41Z | AGPL-3.0¹ | создание бакетов MinIO (Job minio-init) | `deploy/compose/docker-compose.dev.yml`, `deploy/k8s/base/jobs/minio-init-job.yaml` |
| quay.io/minio/minio | RELEASE.2025-09-07T16-13-09Z | AGPL-3.0¹ | S3-хранилище файлов | `deploy/compose/docker-compose.dev.yml`, `deploy/k8s/base/minio/statefulset.yaml` |

## Шрифты

| Файлы | Версия | Лицензия | Назначение |
|---|---|---|---|
| DejaVuSans-Bold.ttf, DejaVuSans.ttf | 2.35 | Bitstream Vera Fonts License; изменения DejaVu — public domain | кириллица в PDF отчётов (backend/app/modules/reporting/fonts) |

Шрифт Inter интерфейса — npm-пакет `@fontsource-variable/inter` в таблице npm.

## CI: действия GitHub Actions

| Действие | Версия | Лицензия | Назначение | Где |
|---|---|---|---|---|
| actions/checkout | v4 | MIT¹ | получение кода в CI | `.github/workflows/ci.yml` |
| actions/setup-node | v4 | MIT¹ | Node.js и кэш npm в CI | `.github/workflows/ci.yml` |
| astral-sh/setup-uv | v5 | MIT¹ | uv и Python в CI | `.github/workflows/ci.yml` |

## Сверка `backend/requirements.txt` с `uv.lock`

Пины `backend/requirements*.txt` совпадают с `uv.lock`.
