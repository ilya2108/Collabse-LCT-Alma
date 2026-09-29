# cms-stub — заглушка CMS-сайта

Маленький автономный FastAPI-сервис (порт **8030**), имитирующий внешний сайт с каталогом
программ. Витрина **двусторонней** интеграции CRM↔CMS (api-contract.md §13.4): принимает
публикацию каталога из CRM, а публичная демо-форма «Оставить заявку на программу» шлёт
подписанный вебхук `cms.lead.created` обратно в CRM — там появляется B2C-заявка,
и её идентификатор возвращается на страницу заглушки. Полный путь «туда-обратно» виден
эксперту за 30 секунд.

## Что умеет

**Приём из CRM** (конверт `IntegrationEvent`, api-contract §13.1, §13.4):

| Эндпоинт | Событие | Эффект |
|---|---|---|
| `POST /api/v1/catalog/upsert` | `crm.program.published` | Публикует/обновляет позицию каталога, выдаёт `cms_external_id` (CRM хранит его в программе) |
| `POST /api/v1/catalog/unpublish` | `crm.program.unpublished` | Снимает позицию с публикации (неизвестная программа → `404 not_found`) |

Каждый запрос: проверка HMAC `X-Webhook-Signature: sha256=<hex HMAC_SHA256(secret, raw_body)>`
(неверная подпись → `401 unauthorized`) и **идемпотентность по `event_id`** — повтор
возвращает тот же ответ с `"duplicate": true`, побочные эффекты не выполняются.
Повторная публикация той же программы (другой `event_id`) переиспользует `cms_external_id`.

**Исходящий вебхук в CRM** (`POST {CRM_WEBHOOK_URL}`, тот же HMAC-секрет): сабмит
демо-формы создаёт лид `lead-N` и событие `cms.lead.created` — `status: new`,
`responsible/university: null`, блоки `program`/`product` из каталога (или `null`,
если программа не выбрана), расширение `payload.lead` с данными клиента
(физлицо/юрлицо). CRM отвечает `201 {"crm_request_id": …}` — id созданной B2C-заявки
сохраняется в лиде и показывается в таблице «Лиды с сайта».

Доставка — через outbox в SQLite с политикой CRM (api-contract §13.7): ретраи только
на сетевые ошибки и 5xx по расписанию **1 мин → 5 мин → 30 мин → 2 ч → 6 ч**, затем DLQ;
4xx — ошибка контракта, в DLQ сразу.

**Демо-страница `/`** — форма лида, каталог программ (со статусом публикации), лиды
со статусом доставки и обратной ссылкой на заявку CRM, журнал обмена (←/→, JSON-тело
по клику). Без CDN — все стили и скрипт inline (закрытый контур). Автообновление раз
в 15 секунд (не срабатывает, пока курсор в форме).

**Служебное**: `GET /api/v1/catalog` — опубликованный каталог;
`GET /api/events?direction=in|out` — журнал в JSON; `GET /healthz` (liveness),
`GET /readyz` (readiness, проверка SQLite) + алиасы `/health/live`, `/health/ready`;
OpenAPI — `/docs`.

## Конфигурация (env)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `CMS_WEBHOOK_SECRET` | `dev-cms-secret` | Per-system секрет HMAC (K8s Secret `crm-internal`), общий для обоих направлений |
| `CRM_WEBHOOK_URL` | `http://backend:8000/api/v1/integrations/cms/webhook` | Куда слать `cms.lead.created` |
| `DB_PATH` | `/data/cms-stub.sqlite3` | Файл SQLite (журнал, каталог, лиды, outbox) |
| `WEBHOOK_RETRY_SCHEDULE` | `60,300,1800,7200,21600` | Расписание ретраев, секунды через запятую |
| `HTTP_TIMEOUT_SECONDS` | `10` | Таймаут HTTP-вызова CRM |
| `RELAY_POLL_SECONDS` | `5` | Период опроса outbox фоновым relay |

## Локальный запуск

```bash
cd services/cms-stub
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
DB_PATH=./data/cms-stub.sqlite3 CRM_WEBHOOK_URL=http://localhost:8000/api/v1/integrations/cms/webhook \
  uvicorn app.main:app --port 8030
```

Проверка приёма вручную:

```bash
BODY='{"event_id":"22222222-2222-2222-2222-222222222222","event_type":"crm.program.published","occurred_at":"2026-09-16T12:00:00Z","source":"crm","correlation":{},"payload":{"program":{"crm_program_id":"pg-demo","name":"Демо-программа"},"product":{"code":"dpo-ds","name":"ДПО: Data Science"},"links":{"db_keys":{"program_id":"pg-demo"},"s3_keys":[]}}}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac dev-cms-secret -hex | sed 's/^.* //')"
curl -s -X POST http://localhost:8030/api/v1/catalog/upsert \
  -H "Content-Type: application/json" -H "X-Webhook-Signature: $SIG" -d "$BODY"
```

## Тесты и линт

```bash
pytest        # HMAC (включая контрольный вектор), идемпотентность, каталог, лид → вебхук, ретраи/DLQ
ruff check .
```

## Заметки по дизайну

- Сервис **автономен**: никакого общего кода с backend/lms-stub — заглушки имитируют
  чужие внешние системы, честность проверки двусторонности важнее DRY.
- Хранилище — SQLite (по контракту §13.5 заглушкам это явно разрешено); подключение
  ленивое, `readyz` деградирует в 503 при недоступности файла.
- Данные формы лида — демо-данные «внешнего сайта»; ПДн-контур CRM (шифрование Fernet,
  маскирование) начинается на стороне backend при записи лида в B2C-заявку (§13.4).
- Авторизация Bearer-токеном клиента `crm-integration` (альтернатива HMAC из §13.6)
  осознанно не включена: в MVP зафиксирован HMAC-режим, транспортный адаптер меняется
  без изменения состава данных (Р-16).
