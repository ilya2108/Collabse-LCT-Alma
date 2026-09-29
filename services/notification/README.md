# notification-service — нотификации «Альмы» + Telegram-бот

FastAPI + aiogram 3, порт **8010**. Единственный компонент контура с egress в интернет
(прообраз DMZ): секрет Telegram-бота видит только этот сервис, ядро CRM наружу не ходит
(`docs/design/OVERVIEW.md` §5, `deployment.md` §3.11).

Два контура в **одном процессе** (lifespan, одна точка probes):

1. **HTTP API** — приём нотификаций из outbox backend'а и доставка в канал
   (telegram — реальный, email/max — структурированные заглушки по дизайну, Р-15).
2. **Telegram-бот** (long-polling) — привязка аккаунта по one-time code и команды
   строго по ролевой модели привязанного пользователя. Включается только при
   `TELEGRAM_BOT_TOKEN`; без токена сервис работает как чистый relay — сценарий
   CI/демо в контуре без интернета.

## HTTP API

### `POST /api/v1/send` — приём нотификации (backend-relay)

Заголовок `X-Internal-Token` (Secret `crm-internal`). Тело — одна запись outbox-таблицы
`notification` (`data-model.md` §9.4):

```json
{
  "notification_id": "ntf-uuid",
  "channel": "telegram",
  "recipient": "199401234",
  "subject": "Заявка зависла",
  "text": "🔥 Заявка «МФТИ — ДПО» висит на этапе «Переговоры» уже 15 дней.",
  "meta": {"request_id": "rq-uuid", "url": "/requests/rq-uuid"},
  "event": "request.stuck"
}
```

- Принимаются и имена колонок data-model: `address`→`recipient`, `body`→`text`,
  `payload`→`meta`, `event_type`→`event` — relay может слать строку outbox как есть.
- Если `text` не передан, но передан `template` — текст рендерится здесь
  (Jinja2, русские; реестр `api-contract.md` §9.4: `request_stuck`,
  `request_transitioned`, `request_assigned`, `comment_added`,
  `workflow_change_requested`, `workflow_changed`, `talent_pool_added`,
  `lead_received`, `test_message`) из полей `meta`.
- Ответ — `202 {"accepted": true}` (в т.ч. на дубль).
- **Идемпотентность** по `notification_id`: KeyDB `SET NX crm:v1:notif:sent:{id}`
  TTL 7 суток; при недоступности KeyDB — деградация в память процесса
  (истина всё равно защищена `uq_notification_dedup` в PG на стороне backend).
- Ошибка доставки в канал → `502 channel_send_failed`, клейм идемпотентности
  снимается — relay backend'а помечает `failed` и ретраит.
- Формат ошибок — единый конверт `{"error": {code, message, trace_id}}`
  (`api-contract.md` §1.4).

### `GET /api/v1/outbox?channel=&limit=` — журнал отправок (демо/отладка)

Последние 100 записей на канал в памяти процесса: заглушечные email/max и реальные
telegram-отправки — «письмо ушло» видно без внешних систем. Авторизация:
`X-Internal-Token` **или** Bearer-токен Keycloak с realm-ролью `admin`
(`api-contract.md` §9.4). Алиас — `GET /api/v1/stub-outbox`.

### `GET /api/v1/stats` — операционные счётчики (та же авторизация, что и outbox)

Метрики с момента старта процесса: `sent`/`failed` по каналам, отброшенные дубли,
ретраи Telegram, команды бота, rate-limited, привязки/отвязки; плюс режимы каналов
(`active`/`stub`), username бота и шаблон deep-link
(`https://t.me/<bot>?start=<code>` — канонический генератор `app/bot/deeplink.py`,
формат `api-contract.md` §9.2). Обнуляются при рестарте — история отправок живёт
в outbox-таблице `notification` в PG.

### Health-пробы (без аутентификации)

| Путь | Назначение |
|---|---|
| `GET /healthz` (алиасы `/health/live`, `/healthz/live`) | liveness: процесс жив |
| `GET /readyz` (алиасы `/health/ready`, `/healthz/ready`) | readiness: в режиме relay — всегда ок; с ботом — polling запущен и токен Keycloak получен; статус KeyDB (`ok/degraded/disabled`) — информационный |

## Каналы

| Канал | Режим | Поведение |
|---|---|---|
| `telegram` | active (с токеном) | `bot.send_message`: HTML-разметка (значения экранируются), тема жирным, кнопка «Открыть в CRM» из `meta.url` (абсолютный URL = `CRM_PUBLIC_URL` + путь) |
| `telegram` | stub (без токена) | JSON-лог + запись в журнал отправок |
| `email`, `max` | stub (по дизайну, Р-15) | JSON-лог + журнал отправок; замена на реальную интеграцию — реализация одного метода `send` |

Устойчивость Telegram-канала:

- **Ретраи**: 3 попытки (env `TELEGRAM_SEND_ATTEMPTS`) на `429 RetryAfter`
  (пауза из ответа, потолок 15 с), сетевых и 5xx-ошибках Bot API с экспоненциальным
  backoff (0.5с → 1с); 4xx (кроме 429) не ретраятся. При исчерпании — `502
  channel_send_failed`, клейм идемпотентности снят, backend-outbox перепланирует
  доставку своим расписанием.
- **Flood-control исходящих**: клиентский троттлинг до лимитов Bot API —
  25 msg/s суммарно и пауза 1 с между сообщениями в один чат (настраивается),
  чтобы веерная рассылка эскалаций из CronJob не ловила 429.
- **Гигиена логов**: тексты уведомлений в логи не пишутся (только длина),
  адреса маскируются (`i***@corp.local`, `199***`), поля, похожие на секреты
  (token/secret/authorization/password), вычищаются JSON-форматтером. Полный
  текст — только в журнале `/api/v1/outbox` под авторизацией.

## Telegram-бот

Авторизация — через тот же Keycloak: бот ходит в backend под client credentials
клиента `crm-notification` (роль `svc_notification`), токен кэшируется и обновляется
при 401. Собственного доступа к данным у бота нет: каждая команда — запрос
`/api/v1/internal/bot/users/{chat_id}/*`, backend применяет RBAC привязанного
пользователя. ПДн студентов/клиентов в бота не передаются by design.

| Команда | Действие |
|---|---|
| `/start <код>` (deep-link из CRM), `/link <код>` | обмен 6-значного кода (TTL 10 мин, KeyDB `GETDEL` на стороне backend) на привязку: `POST /internal/bot/bind`; приветствие с именем и ролью |
| `/my` | мои открытые заявки: этап, вуз/контрагент, дней на этапе |
| `/stuck` | зависшие заявки; пометка «⚠ эскалировано руководителю» при ≥1.5× порога |
| `/summary` (алиас `/status`) | сводка по воронке: активные/зависшие/ждут согласования |
| `/inbox` | непрочитанные уведомления (лента §9.1, `api-contract.md` §9.3) |
| `/unlink` | отвязать chat_id (`DELETE /internal/bot/bindings/{chat_id}`) |
| `/help` | справка |

Непривязанный chat_id получает подсказку «Настройки → Уведомления → Привязать
Telegram». Rate-limit команд — KeyDB `crm:v1:rl:tg:{chat_id}` (20/мин, при
недоступности KeyDB лимит не применяется). Остановка graceful: lifespan сначала
штатно гасит long-polling (`dispatcher.stop_polling`, cancel — только по таймауту
5 с), затем закрывает HTTP-клиенты — хэндлеры в полёте дорабатывают.

## Конфигурация (env, `deployment.md` §3.7/§5)

| Переменная | Дефолт | Назначение |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `""` | пусто ⇒ бот выключен, канал telegram — заглушка |
| `TELEGRAM_BOT_USERNAME` | `lct_crm_bot` | username бота для deep-link `t.me/<bot>?start=<code>` |
| `TELEGRAM_SEND_ATTEMPTS` | `3` | попытки отправки в Telegram (429/сеть/5xx) |
| `TELEGRAM_RETRY_BASE_DELAY_SECONDS` | `0.5` | базовый backoff между попытками |
| `TELEGRAM_SEND_RATE_PER_SECOND` | `25` | клиентский flood-control: сообщений/сек суммарно |
| `TELEGRAM_PER_CHAT_INTERVAL_SECONDS` | `1.0` | пауза между сообщениями в один чат |
| `KEYCLOAK_INTERNAL_URL` | `http://keycloak:8080` | token endpoint + JWKS |
| `KEYCLOAK_REALM` | `crm` | realm |
| `KEYCLOAK_ISSUER` | `http://id.crm.local/realms/crm` | ожидаемый `iss` admin-токенов |
| `KC_CLIENT_ID` / `KC_CLIENT_SECRET` | `crm-notification` / `""` | client credentials бота |
| `BACKEND_BASE_URL` | `http://backend:8000` | ядро CRM (`/api/v1/internal/bot/*`) |
| `INTERNAL_API_TOKEN` | `dev-internal-token` | общий секрет контура (`X-Internal-Token`) |
| `KEYDB_URL` | `redis://keydb:6379/0` | идемпотентность и rate-limit; `""` ⇒ только память |
| `CRM_PUBLIC_URL` | `http://crm.local` | абсолютные ссылки в кнопках Telegram |
| `LOG_LEVEL` | `INFO` | уровень JSON-логов |

## Запуск и проверки

```bash
# зависимости (в Docker — uv sync --frozen по uv.lock)
python3 -m venv .venv && ./.venv/bin/pip install -e . pytest pytest-asyncio ruff

# локальный запуск (relay-режим, без бота)
INTERNAL_API_TOKEN=dev KEYDB_URL= ./.venv/bin/uvicorn app.main:app --port 8010

# тесты и линтер
./.venv/bin/pytest -q
./.venv/bin/ruff check app tests
```

Тесты покрывают: идемпотентность `POST /api/v1/send` (дубль, ретрай после сбоя
канала), маршрутизацию каналов и алиасы полей data-model, рендер шаблонов и
плюрализацию, HTML/кнопку Telegram, ретраи Telegram (429/сеть, потолок
retry_after, не-ретрай 4xx) и flood-control, graceful shutdown бота (штатный
stop и cancel по таймауту), хэндлеры бота (`/start` с кодом, ошибки привязки,
`/my`, `/stuck` с эскалационной пометкой, `/summary`, `/inbox`, `/unlink`),
метрики `GET /api/v1/stats` и deep-link, гигиену логов (маскирование адресатов,
redaction секретов), аутентификацию `X-Internal-Token` и admin-Bearer (JWKS),
health-пробы.
