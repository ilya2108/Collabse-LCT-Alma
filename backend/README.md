# backend — CRM «Вузы» (ЛЦТ 2026)

Модульный монолит FastAPI (Python 3.10+, SQLAlchemy 2 async, Alembic, Pydantic v2).
Контракты — `docs/design/` (по убыванию приоритета: data-model.md → api-contract.md →
остальные); терминологический мост: таблица `deal` ↔ API `/requests` ↔ UI «заявка»,
`workflow_stage` ↔ `status`, `sort_order` ↔ `position`, `is_return` ↔ `kind: return`.

## Модули (`app/modules/*`)

| Модуль | Зона | Ключевые эндпоинты |
|---|---|---|
| `auth` | зеркало Keycloak (`app_user`), permissions по RBAC-матрице §12 | `GET /auth/me` |
| `crm` | вузы, контакты, договоры «1 договор — N продуктов», продукты, программы + ручное ранжирование | `/universities*`, `/contracts*`, `/products*`, `/programs*` (+`/priority`, `/priorities/reorder`) |
| `workflow` | схемы B2B/B2C, пакеты операций с апрувом (`confirm_name` для опасных), атомарная миграция заявок, проверка зависших | `/workflows*`, `/workflows/{id}/impact-preview`, `changes/approve|reject` |
| `requests` | заявки: CRUD, канбан, переходы (граф+роли+комментарий форсируются), история, комментарии, reopen | `/requests*` |
| `import_export` | 5-шаговый импорт (XLSX/XLS/CSV/JSON, кодировки `utf-8-sig→utf-8→cp1251→koi8-r→cp866`), экспорт, справочники админки | `/import/sessions*`, `/export/jobs*`, `/admin/dictionaries*` |
| `talent_pool` | студенты (Fernet + HMAC blind index, маскирование всегда, `/reveal` под аудит), воронка, файлы, активности | `/students*`, `/activities*` |
| `reporting` | 8 JSON-агрегатов дашборда, кэш KeyDB 60 с, kam-скоуп | `/reports/*` |
| `integrations` | конверт IntegrationEvent, вебхуки HMAC, DLQ + ручной retry | `/integrations/{lms,cms}/webhook`, `/admin/integration/*` |
| `notifications` | настройки каналов/событий, in-app лента + маркеры прочтения, тест-отправка, привязка Telegram (код 6 цифр TTL 10 мин, KeyDB GETDEL), CRUD правил `notification_rule`, данные для команд бота | `/notifications*`, `/notifications/rules*`, `/internal/bot/*` |
| `files` | MinIO: 3 бакета, ключ `{entity_type}/{entity_id}/{file_id}/{safe_name}`, proxy/presigned | `GET /files/{id}/download` |
| `admin` | пользователи (manager_id для эскалаций, синк из Keycloak), настройки, фичефлаги (SSE `flags.updated`), аудит | `/admin/users*`, `/admin/settings`, `/admin/feature-flags*`, `/admin/audit-log` |
| `ui` | SSE realtime (fan-out KeyDB pub/sub `crm:v1:events`), пресеты таблиц, глобальный поиск, флаги | `GET /events/stream`, `/ui/presets*`, `/search`, `/flags` |
| `internal` | CronJob-джобы под `X-Internal-Token` | `/internal/jobs/check-stuck-requests`, `purge-deleted`, `outbox-relay` |

Сквозное в `app/core/`: конфиг из env, единый формат ошибок
`{error:{code,message,details,trace_id}}`, JWT/JWKS + роли, PII-криптография,
KeyDB cache-aside с деградацией в PG, три транзакционных outbox'а
(notification / integration_event / audit_log) + фоновый relay, SSE-хаб.

## Нотификации и Telegram-бот (стадия B4)

- Получатели вычисляются **на backend** (Р-9): активные `notification_rule`
  (recipient_type: `responsible` / `manager_of_responsible` / `role` / `user` /
  `tg_username` / `email`) + персональные настройки получателя
  (`GET/PUT /notifications/settings`): выключенное событие — строка не создаётся,
  выключенный канал — строка со статусом `skipped` (остаётся в in-app ленте).
- Доставка: outbox `notification` в PG (в транзакции с бизнес-изменением) → relay →
  `POST http://notification:8010/api/v1/send` (`X-Internal-Token`, идемпотентность
  по `notification_id`); после отправки — SSE `notification.created` (колокольчик).
- Привязка: `POST /notifications/telegram/link-code` → код 6 цифр в KeyDB
  `crm:v1:tg:link:{code}` (SETEX 600) → бот (client credentials `crm-notification`,
  роль `svc_notification`) вызывает `POST /internal/bot/bind` (GETDEL) → upsert
  `app_user.tg_chat_id` + аудит + SSE `telegram.linked`.
- Команды бота — `/internal/bot/users/{chat_id}/*`: backend резолвит chat_id → app_user
  и применяет RBAC этого пользователя; ПДн студентов в ответы бота не попадают.
- Зависшие: `POST /api/v1/internal/jobs/check-stuck-requests` (ежечасный CronJob,
  `X-Internal-Token`), каскад порога rule → stage → workflow → app_setting (дефолт 14
  дней), ступени 1× (ответственный) и 1.5× (`manager_id`; отключается настройкой
  `stuck_escalate_to_manager`), дедуп `notification.dedup_key` + KeyDB-guard —
  повторный вызов дублей не шлёт.

## Резолюции хранения (DDL зафиксирован, таблиц нет — используем `app_setting`)

| Данные | Ключ `app_setting` |
|---|---|
| Настройки нотификаций пользователя | `notify:settings:{user_id}` |
| Маркеры прочтения in-app ленты | `notify:read:{user_id}` |
| Пресеты UI пользователя | `ui:presets:{user_id}` |
| Фичефлаги (`auto_ranking`, `report_pdf`, `llm_features`) | `feature_flags` |
| Справочники-списки (`regions`, `tags`, …) | `dict:{name}` |
| Версия документа `/admin/settings` | `settings_version` |

## Структура

```
app/
  core/        # config, db, errors, security (JWT/JWKS/RBAC), pii, cache (KeyDB),
               # outbox (+relay), sse, events (реестр событий), audit, pagination
  models/      # все таблицы по data-model.md (optimistic locking — системная xmin)
  modules/     # см. таблицу выше
  tools/       # seed (идемпотентный демо-сид), check_migrations, rekey (ротация PII)
alembic/       # одна начальная миграция: raw DDL из data-model.md + pg_trgm
tests/         # unit-тесты (без PG/KeyDB/MinIO; KeyDB-деградация проверяется)
```

## Локальный запуск

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm
export PII_FERNET_KEYS=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export PII_HMAC_KEY=$(openssl rand -base64 32)
./.venv/bin/alembic upgrade head
./.venv/bin/python -m app.seed                       # идемпотентный демо-сид
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

OpenAPI — `http://localhost:8000/api/v1/docs`.

### Переменные окружения

Полный список — `app/core/config.py`; имена совпадают с `deployment.md` §3.7
(ConfigMap `crm-config`, Secrets `crm-db`/`crm-pii`/`crm-s3`/`crm-internal`). Ключевые:

| Переменная | Назначение |
|---|---|
| `DATABASE_URL`, `KEYDB_URL`, `S3_*` | PostgreSQL, KeyDB, MinIO |
| `KEYCLOAK_INTERNAL_URL`, `KEYCLOAK_ISSUER`, `KEYCLOAK_AUDIENCES` | JWKS и проверка JWT |
| `PII_FERNET_KEYS`, `PII_HMAC_KEY` | шифрование ПДн + blind index (Secret `crm-pii`) |
| `INTERNAL_API_TOKEN` | CronJob → backend и backend → notification-service |
| `LMS_WEBHOOK_SECRET`, `CMS_WEBHOOK_SECRET` | HMAC вебхуков интеграций |
| `NOTIFICATION_BASE_URL`, `LMS_BASE_URL`, `CMS_BASE_URL` | адреса сервисов контура |
| `TELEGRAM_BOT_USERNAME` | @имя бота для deep-link привязки (`https://t.me/…?start=<код>`); по умолчанию `lct_crm_bot` — единый дефолт с notification-service |
| `KEYCLOAK_ADMIN_CLIENT_ID/SECRET` | сервис-аккаунт `view-users` для `POST /admin/users/sync` (штатная синхронизация — upsert при логине); в realm предзаведён клиент `crm-backend-admin`, секрет — `BACKEND_ADMIN_CLIENT_SECRET` (compose `.env` / Secret `crm-keycloak-clients`) |
| `FILE_DELIVERY` | `proxy` (по умолчанию) или `presigned` |

## Проверки

```bash
./.venv/bin/python -m pytest -q                    # unit-тесты (243)
./.venv/bin/python -m ruff check app tests
./.venv/bin/python -m app.tools.check_migrations   # exit 0 = схема на head
```

Тесты не требуют инфраструктуры: `KEYDB_URL` в `tests/conftest.py` заведомо
недоступен — так проверяется деградация кэша в PG и ответ 503 на код привязки.

## Статус стадий

- **B1** — каркас, конфиг, все модели + начальная миграция, идемпотентный сид,
  Keycloak JWT + RBAC + permissions (`GET /auth/me`), PII-утилиты и ротация ключей,
  единый формат ошибок, health-пробы, CRUD справочников (вузы, контакты, договоры
  «1 договор — N продуктов», продукты, программы с ручным ранжированием).
- **B2** — движок workflow (пакеты операций, pending/approve с `confirm_name`,
  атомарная миграция заявок), заявки `/requests` (CRUD/канбан/переходы/история/
  комментарии/reopen), три outbox'а (notification/integration/audit) + relay,
  SSE `/events/stream`, вебхуки LMS/CMS (HMAC, идемпотентность по `event_id`),
  CronJob «зависшие» с каскадом порогов и эскалацией руководителю.
- **B3** — импорт (5 шагов: magic bytes, кодировки, интерактивный маппинг,
  dry-run c XLSX-отчётом ошибок, apply c дедупом студентов по `email_hmac`),
  экспорт XLSX/CSV(BOM|cp1251)/JSON (включая `report:<code>`), файлы MinIO,
  Talent Pool (маскирование + `/reveal` под аудит, воронка, активности),
  отчёты `/reports/*` с кэшем 60 с, справочники админки, `purge-deleted` (152-ФЗ).
- **B4** — модуль нотификаций целиком (настройки, лента, тест, правила, Telegram-бот,
  internal bot API), админка (пользователи + синк Keycloak, настройки, фичефлаги
  с SSE `flags.updated`, аудит с фильтром `action` — чип «Раскрытия ПДн» в UI),
  UI-инфраструктура (пресеты, глобальный поиск, `/flags`). Реестр эндпоинтов
  api-contract.md §15 закрыт полностью; сверх реестра — CRUD
  `/notifications/rules` (требование стадии, путей в §15 нет).
