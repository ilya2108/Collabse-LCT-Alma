# backend — «Альма», CRM взаимодействия с вузами (ЛЦТ 2026)

Модульный монолит FastAPI (Python 3.10+, образ — 3.12; SQLAlchemy 2 async, Alembic, Pydantic v2).
Контракты — `docs/design/` (по убыванию приоритета: data-model.md → api-contract.md →
остальные); терминологический мост: таблица `deal` ↔ API `/requests` ↔ UI «заявка»,
`workflow_stage` ↔ `status`, `sort_order` ↔ `position`, `is_return` ↔ `kind: return`.

## Модули (`app/modules/*`)

| Модуль | Зона | Ключевые эндпоинты |
|---|---|---|
| `auth` | зеркало Keycloak (`app_user`), профиль с ролями и эффективными правами по процессам | `GET /auth/me` |
| `access` | каталог прав и ролей, свои роли, назначение ролей пользователям, разграничение B2B/B2C и «все / только свои» | `GET /roles`, `/admin/permissions`, `/admin/roles*`, `PUT /admin/users/{id}/roles` |
| `crm` | вузы, контакты, договоры «1 договор — N продуктов», продукты, программы + ручное ранжирование | `/universities*`, `/contracts*`, `/products*`, `/programs*` (+`/priority`, `/priorities/reorder`) |
| `workflow` | схемы B2B/B2C, пакеты операций с апрувом (`confirm_name` для опасных), атомарный пакетный перенос заявок со сводными уведомлениями, advisory-локи с ограниченным ожиданием, проверка зависших | `/workflows*`, `/workflows/{id}/impact-preview`, `changes/approve\|reject` |
| `requests` | заявки: CRUD, канбан, переходы (граф+права+комментарий форсируются), история, комментарии, файлы, reopen | `/requests*`, `/requests/{id}/files*` |
| `import_export` | 5-шаговый импорт (XLSX/XLS/CSV/JSON, кодировки `utf-8-sig→utf-8→cp1251→koi8-r→cp866`), экспорт (включая `report:kam-activity` в XLSX/PDF), справочники админки | `/import/sessions*`, `/export/jobs*`, `/admin/dictionaries*` |
| `talent_pool` | студенты (Fernet + HMAC blind index, маскирование всегда, `/reveal` под аудит), воронка, файлы, активности | `/students*`, `/activities*` |
| `reporting` | 8 JSON-агрегатов дашборда + отчёт «Активность КАМов» (детализация и сводка, PDF на fpdf2, многолистовой XLSX); кэш KeyDB 60 с, область отчётов `report_scope` | `/reports/*`, `/reports/kam-activity`, `/reports/kam-activity/summary` |
| `integrations` | конверт IntegrationEvent, транспорт `IntegrationTransport` (HTTP + HMAC), вебхуки HMAC, DLQ + ручной retry | `/integrations/{lms,cms}/webhook`, `/admin/integration/*` |
| `notifications` | настройки каналов/событий, in-app лента + маркеры прочтения, тест-отправка, привязка Telegram (код 6 цифр TTL 10 мин, KeyDB GETDEL), CRUD правил `notification_rule`, данные для команд бота | `/notifications*`, `/notifications/rules*`, `/internal/bot/*` |
| `files` | MinIO: 3 бакета, ключ `{entity_type}/{entity_id}/{file_id}/{safe_name}`, proxy/presigned; файл заявки — только в зоне видимости заявки | `GET /files/{id}/download` |
| `admin` | пользователи (manager_id для эскалаций, синк профилей из Keycloak — роли не трогает), настройки (`integration_mode` — только `http`), фичефлаги (SSE `flags.updated`), аудит | `/admin/users*`, `/admin/settings`, `/admin/feature-flags*`, `/admin/audit-log` |
| `ui` | SSE realtime (fan-out KeyDB pub/sub `crm:v1:events`), пресеты таблиц, глобальный поиск, флаги | `GET /events/stream`, `/ui/presets*`, `/search`, `/flags` |
| `internal` | CronJob-джобы под `X-Internal-Token` | `/internal/jobs/check-stuck-requests`, `purge-deleted`, `purge-idempotency-keys`, `outbox-relay` |

Сквозное в `app/core/`: конфиг из env, единый формат ошибок
`{error:{code,message,details,trace_id}}`, JWT/JWKS и `require_permission`, каталог
прав (`rbac_catalog`) и модель доступа (`access`: права по процессам, `DealScope`),
PII-криптография, KeyDB cache-aside с деградацией в PG, middleware Idempotency-Key с
хранилищем в PG, три транзакционных outbox'а (notification / integration_event /
audit_log) + фоновый relay и сменный транспорт интеграций, SSE-хаб. OpenAPI — 150
методов.

## Роли и права

- **Права — код.** `core/rbac_catalog.py`: 60 прав в 8 группах. 18 прав — «с
  областью»: действуют только в процессах роли (B2B, B2C) и с её областью записей
  (`all` / `own`), остальные общие (справочники, админка). У прав есть зависимости
  (`requires`): редактор не даст сохранить правку без просмотра.
- **Роли — данные.** Таблицы `app_role` / `app_role_permission` (миграция 0004
  засевает каталог из кода). Системные роли: admin — 59 прав, заблокирована от
  правки; head_kam — 40; kam — 28; observer — 14. Наборы подобраны так, что доступ
  базовых ролей к каждому эндпоинту совпал с прежней матрицей —
  регрессия `tests/test_access_matrix.py`. Свои роли — `POST /admin/roles`;
  удалить роль, пока она назначена или указана в переходах процессов, нельзя
  (409 `role_in_use`).
- **Назначения — в Альме.** Источник истины — `app_user.roles`; роль из токена
  Keycloak становится только стартовым назначением при первом входе, синхронизация
  с Keycloak роли не трогает.
- **Проверки.** Эндпоинты — `Depends(require_permission("<код>"))`; права
  нескольких ролей объединяются по процессу. Запросы по заявкам проходят через
  `DealScope.apply(stmt)`: процессы роли и «только свои».
- **Живое применение.** Назначения читаются из БД на каждом запросе; каталог ролей
  кэшируется в процессе на `RBAC_CATALOG_TTL_SECONDS` (5 с) и сбрасывается на всех
  репликах по SSE `access.changed` — интерфейс перестраивается без перезахода.
- **Аварийный вход**, если в системе не осталось администратора:
  `python -m app.tools.rbac grant-admin <username>` (`list-admins` — кто может
  управлять ролями); в кластере — через `kubectl -n crm exec deploy/backend -- …`.
  Назначение пишется в аудит с `source = "cli"`.

## Перенос заявок при изменении схемы

- **Одна транзакция на пакет**: схема, перенос, история, сводки в outbox и аудит —
  либо всё, либо ничего (`workflow/service.py::apply_change`).
- **Один SQL-оператор на удаляемый этап** (`workflow/migration.py`): CTE
  `UPDATE deal … RETURNING` сразу пишет `deal_stage_history`, наружу — только
  агрегаты по ответственным. Число обращений к БД не зависит от числа заявок.
- **Сводные уведомления**: каждый получатель, который раньше получил бы хотя бы одно
  уведомление о переходе, получает ровно одно `request.transitioned` с
  `payload.count`. Правила и личные настройки — те же; эквивалентность прежнему
  поштучному пути — `tests/db/test_workflow_bulk_migration.py::test_digest_equivalent_to_legacy`.
- **Локи** (`workflow/locks.py`): строка пакета `FOR UPDATE` → advisory-лок воронки
  (переходы — shared, применение — exclusive) → строки заявок. Ожидание ограничено
  `lock_timeout`: переход — 10 с, применение — 30 с; затем `409 workflow_busy` с
  `Retry-After: 2`, пакет остаётся на согласовании.
- **Замеры** (`lock_wait_ms`, `apply_ms`, `migrate_ms`, `lock_held_ms`) — в ответе
  approve и в логе `app.workflow`; отчёт переноса хранится в
  `workflow_change_request.impact['migration_report']`.
- **Бенчмарк**: `python -m app.tools.bench_migration` (или `make bench-migration` из
  корня) на одноразовой базе `*_bench` / `*_test`. 10 000 заявок — 0,59 с под локом
  вместо 25,1 с, 50 000 — 2,9 с вместо 130 с; подробности —
  `docs/evidence/migration-benchmark.md`.

## Idempotency-Key

Семантика — черновик IETF `draft-ietf-httpapi-idempotency-key-header`
(`core/idempotency.py`, чистый ASGI-middleware; хранилище — `core/idempotency_store.py`,
таблица `idempotency_key` из миграции 0003):

- POST/PUT/PATCH/DELETE под `/api/v1/` с заголовком `Idempotency-Key`; без заголовка
  поведение прежнее. Вебхуки LMS/CMS исключены — у них своя идемпотентность по
  `event_id`;
- ключ действует в области принципала: `sub` проверенного JWT или `internal`
  (X-Internal-Token);
- повтор с тем же отпечатком (метод, путь, query, media-type, хеш тела) в течение
  24 ч после успеха → сохранённый ответ + `Idempotent-Replayed: true`; тот же ключ с
  другим запросом → 422 `idempotency_key_reused`; пока первый запрос выполняется →
  409 `idempotency_in_progress` + `Retry-After: 1`;
- сохраняются только 2xx: при ошибке ключ освобождается, повтор безопасен. Тело
  ответа хранится Fernet-шифртекстом, тело запроса — только хешем;
- ошибка хранилища → fail-closed 503 `service_unavailable` + `Retry-After: 2`;
- очистка просроченных ключей — `POST /internal/jobs/purge-idempotency-keys`
  (CronJob `idempotency-purge` ежечасно; на compose-стенде — `make idempotency-purge`).

Фронтенд выдаёт ключ каждому действию пользователя сам и повторяет запрос с тем же
ключом при обрыве связи, 502/503/504, `idempotency_in_progress` и `workflow_busy`.

## Отчёт «Активность КАМов»

- `reporting/kam_activity.py` — детализация «КАМ → вузы → заявки» и сводка для
  заказчика (KPI с дельтами к прошлому периоду той же длины, конверсия по этапам,
  «где застревают», динамика, разрез по КАМам, студенты на программах).
- Взаимодействие — собственное действие ответственного КАМа в периоде: ручной
  переход, комментарий, файл заявки, мероприятие с вузом. Системные переходы
  (перенос схемы, LMS, импорт) и действия других пользователей не считаются. Этап
  остановки восстанавливается по `deal_stage_history` на конец периода.
- Область — `report_scope(viewer)`: процессы ролей с `reports:read`, «все КАМы» —
  только с `reports:all`; КАМы отчёта — роли с правом `requests:kam`.
- Выгрузка — `POST /export/jobs {entity_type: "report:kam-activity"}`:
  XLSX «Сводка / КАМы / Вузы / Заявки / Динамика», PDF (`reporting/pdf.py`, fpdf2,
  шрифты DejaVu в `reporting/fonts/`), CSV/JSON — лист «Заявки». PDF отчёта флагом
  `report_pdf` не закрыт.
- Индексы под отчёт — миграция 0005. Демо-история ~20 КАМов —
  `python -m app.tools.seed_activity`, основной сид вызывает её сам.

## Нотификации и Telegram-бот

- Получатели вычисляются **на backend** (Р-9): активные `notification_rule`
  (recipient_type: `responsible` / `manager_of_responsible` / `role` / `user` /
  `tg_username` / `email`) + персональные настройки получателя
  (`GET/PUT /notifications/settings`): выключенное событие — строка не создаётся,
  выключенный канал — строка со статусом `skipped` (остаётся в in-app ленте).
  Получатели правила `role` — активные пользователи с ролью, в области которых
  заявка: процесс роли и «только свои» (`outbox.role_rule_members`).
- Доставка: outbox `notification` в PG (в транзакции с бизнес-изменением) → relay →
  `POST http://notification:8010/api/v1/send` (`X-Internal-Token`, идемпотентность
  по `notification_id`); после отправки — SSE `notification.created` (колокольчик).
- Привязка: `POST /notifications/telegram/link-code` → код 6 цифр в KeyDB
  `crm:v1:tg:link:{code}` (SETEX 600) → бот (client credentials `crm-notification`,
  роль `svc_notification`) вызывает `POST /internal/bot/bind` (GETDEL) → upsert
  `app_user.tg_chat_id` + аудит + SSE `telegram.linked`.
- Команды бота — `/internal/bot/users/{chat_id}/*`: backend резолвит chat_id → app_user
  и применяет права этого пользователя; ПДн студентов в ответы бота не попадают.
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
| Фичефлаги (`auto_ranking`, `report_pdf` — включён по умолчанию, `llm_features`) | `feature_flags` |
| Справочники-списки (`regions`, `tags`, …) | `dict:{name}` |
| Версия документа `/admin/settings` | `settings_version` |

## Структура

```
app/
  core/        # config, db, errors, security (JWT/JWKS, require_permission), rbac_catalog
               # (права), access (роли, права по процессам, DealScope), pii, cache (KeyDB),
               # idempotency (+ idempotency_store), outbox (+relay), integration_transport,
               # sse, events (реестр событий), audit, pagination
  models/      # 35 таблиц по data-model.md (optimistic locking — системная xmin)
  modules/     # см. таблицу выше
  tools/       # seed (+ seed_activity), check_migrations, rekey (ротация PII),
               # rbac (аварийный admin), bench_migration (замер переноса заявок)
alembic/       # 0001 начальная схема (raw DDL из data-model.md + pg_trgm),
               # 0002 маскирование ПДн лидов, 0003 idempotency_key,
               # 0004 роли и права, 0005 индексы отчёта «Активность КАМов»
tests/         # юнит-тесты без инфраструктуры (KeyDB-деградация проверяется)
  db/          # интеграционные тесты на PostgreSQL: схема из миграций, фабрики данных
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
| `IDEMPOTENCY_ENABLED`, `IDEMPOTENCY_TTL_HOURS` (24), `IDEMPOTENCY_LEASE_SECONDS` (60) | Idempotency-Key: включение, срок хранения ответов, аренда ключа на время обработки |
| `IDEMPOTENCY_MAX_REQUEST_BYTES`, `IDEMPOTENCY_MAX_RESPONSE_BYTES` | лимиты тела запроса (60 МБ) и сохраняемого ответа (1 МБ) |
| `RBAC_CATALOG_TTL_SECONDS` | TTL кэша каталога ролей в процессе (5 с) |

## Проверки

```bash
./.venv/bin/python -m pytest -q                    # 1 448 тестов (1 311 юнит + 137 tests/db)
ALMA_DB_TESTS=required ./.venv/bin/python -m pytest -q   # как в CI: без PostgreSQL — ошибка
./.venv/bin/python -m ruff check app tests
./.venv/bin/python -m app.tools.check_migrations   # exit 0 = схема на head
```

Юнит-тесты не требуют инфраструктуры: `KEYDB_URL` в `tests/conftest.py` заведомо
недоступен — так проверяется деградация кэша в PG и ответ 503 на код привязки.

`tests/db` — на PostgreSQL `localhost:5432` (`crm/crm`). Схема создаётся настоящими
миграциями (`alembic upgrade head`), база пересоздаётся на каждый прогон, поэтому её
имя обязано начинаться с `crm_test` или оканчиваться на `_test` (по умолчанию
`crm_test`; своя — `ALMA_TEST_DB=crm_test_<имя>` или `ALMA_TEST_DATABASE_URL`).
Без PostgreSQL эти тесты пропускаются, `ALMA_DB_TESTS=required` превращает пропуск в
ошибку. Два режима изоляции: `db_session` — тест во внешней транзакции, `commit()`
сервисов фиксирует SAVEPOINT; `db_committed_sessionmaker` — настоящие COMMIT на
отдельных соединениях (гонки, advisory-локи), после теста таблицы очищаются, данные
миграций восстанавливаются. Полный прогон — около минуты.

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
- **B5** — роли и права в БД Альмы (каталог 60 прав, свои роли, B2B/B2C и «только
  свои», аварийный `grant-admin`); отчёт «Активность КАМов» (детализация, сводка,
  PDF/XLSX) и файлы заявки; пакетный перенос заявок со сводными уведомлениями,
  ограниченным ожиданием локов и бенчмарком; сквозной Idempotency-Key в PG;
  транспорт интеграций — сменный адаптер (`integration_mode` только `http`);
  интеграционные тесты на настоящем PostgreSQL и в CI.
