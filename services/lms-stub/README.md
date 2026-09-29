# lms-stub — заглушка LMS

Маленький автономный FastAPI-сервис (порт **8020**), имитирующий внешнюю LMS.
Витрина **двусторонней** интеграции CRM↔LMS (api-contract.md §13): принимает push из CRM
и по кнопкам на демо-странице шлёт подписанные вебхуки обратно в CRM — эксперт видит
путь данных «туда-обратно» по сети между отдельными процессами.

## Что умеет

**Приём из CRM** (конверт `IntegrationEvent`, api-contract §13.1–13.2):

| Эндпоинт | Событие | Эффект |
|---|---|---|
| `POST /api/v1/enrollments` | `crm.request.handed_over` | Создаёт зачисление, выдаёт `lms_enrollment_id` (CRM хранит его в `external_refs`) |
| `POST /api/v1/programs/upsert` | `crm.program.upserted` | Синхронизирует программу, назначает `lms_course_id`, если CRM его не знает |

Каждый запрос: проверка HMAC `X-Webhook-Signature: sha256=<hex HMAC_SHA256(secret, raw_body)>`
(неверная подпись → `401 unauthorized`) и **идемпотентность по `event_id`** — повтор
возвращает тот же ответ с `"duplicate": true`, побочные эффекты не выполняются.
Повторная передача той же заявки (другой `event_id`, тот же `crm_request_id`)
переиспользует выданный `lms_enrollment_id`.

**Исходящие вебхуки в CRM** (`POST {CRM_WEBHOOK_URL}`, тот же HMAC-секрет):
кнопки на `/` у каждого зачисления — «Студент начал обучение» (`lms.learning.started`),
«Прогресс 50%» (`lms.learning.progress`, `payload.progress.percent = 50`),
«Завершил обучение» (`lms.learning.completed`, в `links.s3_keys` — ключ финального отчёта).
Конверт эхо-возвращает `correlation` (`crm_request_id` + `lms_enrollment_id`) и пять блоков
payload из исходного события — так CRM матчит вебхук к заявке.

Доставка — через outbox в SQLite с политикой CRM (api-contract §13.7): ретраи только
на сетевые ошибки и 5xx по расписанию **1 мин → 5 мин → 30 мин → 2 ч → 6 ч**, затем DLQ;
4xx — ошибка контракта, в DLQ сразу.

**Демо-страница `/`** — журнал обмена (←/→, время, тип, `event_id`, статус доставки,
JSON-тело по клику), таблицы зачислений и программ, кнопки-триггеры. Без CDN — все стили
и скрипт inline (закрытый контур). Автообновление раз в 15 секунд.

**Служебное**: `GET /api/v1/enrollments` — принятые зачисления;
`GET /api/events?direction=in|out` — журнал в JSON; `GET /healthz` (liveness),
`GET /readyz` (readiness, проверка SQLite) + алиасы `/health/live`, `/health/ready`;
OpenAPI — `/docs`.

## Конфигурация (env)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `LMS_WEBHOOK_SECRET` | `dev-lms-secret` | Per-system секрет HMAC (K8s Secret `crm-internal`), общий для обоих направлений |
| `CRM_WEBHOOK_URL` | `http://backend:8000/api/v1/integrations/lms/webhook` | Куда слать вебхуки `lms.learning.*` |
| `DB_PATH` | `/data/lms-stub.sqlite3` | Файл SQLite (журнал, зачисления, outbox) |
| `WEBHOOK_RETRY_SCHEDULE` | `60,300,1800,7200,21600` | Расписание ретраев, секунды через запятую |
| `HTTP_TIMEOUT_SECONDS` | `10` | Таймаут HTTP-вызова CRM |
| `RELAY_POLL_SECONDS` | `5` | Период опроса outbox фоновым relay |

## Локальный запуск

```bash
cd services/lms-stub
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
DB_PATH=./data/lms-stub.sqlite3 CRM_WEBHOOK_URL=http://localhost:8000/api/v1/integrations/lms/webhook \
  uvicorn app.main:app --port 8020
```

Проверка приёма вручную:

```bash
BODY='{"event_id":"11111111-1111-1111-1111-111111111111","event_type":"crm.request.handed_over","occurred_at":"2026-09-16T12:00:00Z","source":"crm","correlation":{"crm_request_id":"rq-demo"},"payload":{"program":{"crm_program_id":"pg-demo","name":"Демо-программа"},"university":{"name":"МФТИ"},"links":{"db_keys":{"request_id":"rq-demo"},"s3_keys":[]}}}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac dev-lms-secret -hex | sed 's/^.* //')"
curl -s -X POST http://localhost:8020/api/v1/enrollments \
  -H "Content-Type: application/json" -H "X-Webhook-Signature: $SIG" -d "$BODY"
```

## Тесты и линт

```bash
pytest        # HMAC (включая контрольный вектор), идемпотентность, конверт, ретраи/DLQ
ruff check .
```

## Заметки по дизайну

- Сервис **автономен**: никакого общего кода с backend/cms-stub — заглушки имитируют
  чужие внешние системы, честность проверки двусторонности важнее DRY.
- Хранилище — SQLite (по контракту §13.5 заглушкам это явно разрешено); подключение
  ленивое, `readyz` деградирует в 503 при недоступности файла.
- Авторизация Bearer-токеном клиента `crm-integration` (альтернатива HMAC из §13.6)
  осознанно не включена: в MVP зафиксирован HMAC-режим, транспортный адаптер меняется
  без изменения состава данных (Р-16).
- LMS-события **не двигают** заявку по workflow в CRM автоматически — переходы делают
  люди (резолюция контракта); заглушка лишь поставляет факты о ходе обучения.
