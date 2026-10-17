# llm-gateway — шлюз «Альмы» к GigaChat

FastAPI + httpx, порт **8040**, один процесс uvicorn. Единственный компонент, который знает
ключ GigaChat и ходит к нему наружу: **второй под с egress** после `notification`
(NetworkPolicy `41-llm-gateway-dmz`: только TCP 443 и 9443 вне кластерных и приватных сетей).
Ядро CRM по-прежнему без выхода в интернет.

Шлюз «глупый»: принимает сообщения и возвращает текст и usage. Промпт, разбор JSON, проверка
чисел, псевдонимы КАМов (`КАМ-01…`) и подстановка ФИО — в backend (модуль `ai_summary`,
«Сводка ИИ» в отчёте «Активность КАМов»). Шлюз не видит смысла данных; в его логах нет ни
текстов, ни ответов модели.

```
backend (без egress) ──POST /v1/chat, X-Internal-Token──> llm-gateway (tier: dmz, 1 реплика)
                                                            │ очередь на 1 поток, дневной лимит,
                                                            │ OAuth-кэш, ретраи, TLS НУЦ
                                                            ▼ только 443/9443 наружу
                          ngw.devices.sberbank.ru:9443 (OAuth) · gigachat.devices.sberbank.ru:443 (chat)
```

Перенос в отдельный сегмент DMZ или переключение на on-prem GigaChat — через
`GIGACHAT_API_URL` / `GIGACHAT_AUTH_URL`, без правки кода backend.

## HTTP API (контракт backend ↔ llm-gateway)

Все ответы — JSON. Ошибки — в конверте `{"error": {"code", "message", "trace_id"}}`;
`trace_id` — входной `X-Request-Id` или новый uuid4 (он же уходит в GigaChat как `X-Request-ID`).

### `POST /v1/chat`

Заголовки: `X-Internal-Token: <LLM_GATEWAY_TOKEN>` (обязателен), `X-Request-Id` (необязателен).

```json
{
  "messages": [
    {"role": "system", "content": "…"},
    {"role": "user", "content": "…"}
  ],
  "tier": "strong",
  "temperature": 0.2,
  "max_tokens": 1500,
  "response_schema": {"type": "object", "properties": {"…": "…"}, "required": ["…"]},
  "purpose": "kam_summary_team"
}
```

- `messages` — 1–8 элементов, `role ∈ {system, user, assistant}`, сумма длин `content` ≤ `LLM_MAX_INPUT_CHARS`;
- `tier` — `strong` (по умолчанию, `GIGACHAT_MODEL_STRONG`) или `fast` (`GIGACHAT_MODEL_FAST`);
- `temperature` — 0–1, по умолчанию 0.2; шлюз поднимает до 0.01 (API отвергает 0);
- `max_tokens` — 16–`LLM_MAX_OUTPUT_TOKENS`, по умолчанию 1500;
- `response_schema` — необязательная JSON Schema: уходит как `response_format` (structured output, бета);
- `purpose` — до 40 символов `[a-z0-9_]`, только для логов.

Ответ `200`:

```json
{
  "text": "{\"headline\": \"…\", …}",
  "model": "GigaChat-2-Pro:2.0.28.2",
  "finish_reason": "stop",
  "usage": {"prompt_tokens": 2310, "completion_tokens": 412, "total_tokens": 2722},
  "structured": true,
  "latency_ms": 63120,
  "queue_ms": 0,
  "attempts": 1
}
```

`latency_ms` — время у модели после получения слота (включая OAuth и ретраи), `queue_ms` —
ожидание слота, `attempts` — сколько раз отправлялся chat-запрос.

| HTTP | `code` | Когда | Заголовки |
|---|---|---|---|
| 400 | `validation_error` | Тело не по схеме, длинный вход, `max_tokens` больше лимита, неизвестный `tier` | |
| 401 | `unauthorized` | Нет или неверный `X-Internal-Token` | |
| 429 | `queue_full` | Ждущих ≥ `LLM_QUEUE_MAX` | `Retry-After: 30` |
| 503 | `queue_timeout` | Слот не получен за `LLM_QUEUE_TIMEOUT_SECONDS` | `Retry-After: 30` |
| 503 | `llm_not_configured` | `LLM_PROVIDER=gigachat` и пустой `GIGACHAT_CREDENTIALS` | |
| 503 | `llm_budget_exceeded` | `tokens_today ≥ LLM_DAILY_TOKEN_CAP` (проверка до слота и после него) | `Retry-After: <секунд до 00:00 МСК>` |
| 503 | `llm_rate_limited` | GigaChat отвечает 429 и попытки кончились (или ожидание не помещается в бюджет) | `Retry-After` от GigaChat или 30 |
| 502 | `llm_auth_failed` | OAuth 400/401/403 — ключ или scope отклонены (без ретраев); повторный 401 на chat | |
| 502 | `llm_upstream_error` | Прочие 4xx/5xx GigaChat после ретраев, пустые `choices`, сетевая ошибка/TLS | |
| 504 | `llm_timeout` | Исчерпан бюджет `LLM_TIMEOUT_SECONDS` или тайм-аут чтения | |

### `GET /v1/usage` (`X-Internal-Token`)

```json
{"provider": "gigachat", "configured": true, "model_strong": "GigaChat-2-Pro", "model_fast": "GigaChat-2",
 "day": "2026-10-17", "tokens_today": 18234, "daily_cap": 200000,
 "gate": {"limit": 1, "in_flight": 1, "waiting": 2, "total": 41, "max_in_flight": 1},
 "requests": {"ok": 37, "error": {"llm_timeout": 1, "queue_full": 0, "…": 0}},
 "latency_ms": {"p50": 61800, "p95": 98400, "last": 64210},
 "structured_supported": true, "token_cached": true}
```

Счётчики — с момента старта процесса; `latency_ms` — по последним 200 успешным вызовам.

### `GET /healthz`, `GET /readyz`

`/healthz` → `{"status": "ok"}` всегда. `/readyz` → 200, пока процесс жив: доступность GigaChat
на него **не влияет**, чтобы под не флапал при проблемах с интернетом. Диагностика — в `checks`:

```json
{"status": "ok", "checks": {"provider": "gigachat", "configured": true, "ca_bundle": "loaded", "token": "cached"}}
```

`ca_bundle`: `loaded` | `missing` (путь задан, файла нет) | `invalid` (не PEM) | `not_set`.

## Как шлюз ходит в GigaChat

Wire-формат — как в SDK `gigachat` 0.2.3; SDK не используется (чистый
httpx: явный контроль OAuth, TLS-бандла, ретраев и семафора; тесты на `httpx.MockTransport`).

- **OAuth**: `POST {GIGACHAT_AUTH_URL}`, `Authorization: Basic <GIGACHAT_CREDENTIALS>`,
  `RqUID: <uuid4>`, форма `scope=<GIGACHAT_SCOPE>` → `{"access_token", "expires_at" (мс)}`
  (поддержан и вариант `{"tok", "exp"}`). Токен (~30 мин) кэшируется до `expires_at − 60 с`;
  обновление под `asyncio.Lock` — параллельные запросы делают один OAuth-вызов.
- **Chat**: `POST {GIGACHAT_API_URL}/chat/completions`, `Authorization: Bearer <токен>`,
  `{"model", "messages", "temperature", "max_tokens", "stream": false}`; при схеме —
  `"response_format": {"type": "json_schema", "schema": …, "strict": false}`.
- **Structured output** (бета): на 400/422 с `response_format` шлюз запоминает
  `structured_supported=false` до рестарта и повторяет без него (не ретрай); в ответе
  `structured: false`. Backend разбирает JSON сам в обоих случаях.
- **401 на chat** — токен сбрасывается, один повтор OAuth + chat (не ретрай).
- **Ретраи** — до `LLM_RETRY_ATTEMPTS` попыток на `ConnectError`, `ConnectTimeout`,
  `RemoteProtocolError`, HTTP 429/500/502/503/504 (OAuth тоже): экспонента
  `LLM_RETRY_BASE_DELAY_SECONDS × 2^(n−1)` + джиттер; `Retry-After` учитывается, если
  помещается в бюджет, иначе ошибка отдаётся сразу. Тайм-аут чтения не ретраится: модель
  уже работала над ответом, повтор только удвоит ожидание.
- **Бюджет** `LLM_TIMEOUT_SECONDS` — на ВСЕ попытки после получения слота, включая OAuth
  (`asyncio.timeout`). Backend ждёт шлюз дольше: очередь 180 + бюджет 120 + запас 30 с.

### Очередь и дневной лимит

- Бесплатный тариф `GIGACHAT_API_PERS` держит **ровно один** параллельный поток — второй
  параллельный запрос получает 429 от Сбера. Поэтому `LLM_MAX_CONCURRENCY=1`, ждущих —
  не больше `LLM_QUEUE_MAX` (иначе 429 `queue_full`), ждать слот — не дольше
  `LLM_QUEUE_TIMEOUT_SECONDS` (иначе 503 `queue_timeout`).
- Дневной лимит — сумма `total_tokens` за сутки по Москве, включая ответы, признанные ошибкой
  (пустые `choices` и т. п.). **Счётчик живёт в памяти процесса: рестарт пода его обнуляет.**
  Для одной реплики это допустимо: лимит защищает от «убежавшего» цикла запросов, точный
  учёт — в кабинете GigaChat.
- Отсюда **1 реплика, `strategy: Recreate`, uvicorn без `--workers`**: два пода во время
  rolling update или два воркера дали бы два потока и два независимых счётчика.

### Логи

Одна JSON-строка на вызов: `event=llm.chat`, `request_id`, `purpose`, `tier`, `model`,
`status` (`ok` или код ошибки), `attempts`, `queue_ms`, `latency_ms`, `prompt_tokens`,
`completion_tokens`, `input_chars`, `input_sha256` (sha256 канонического JSON `messages`),
`structured`. **Никогда** не логируются тексты сообщений, ответ модели, токен, ключ,
заголовки `Authorization`; форматтер дополнительно вычищает поля с `token`, `secret`,
`authorization`, `password`, `credential` (кроме счётчиков `*_tokens`). Ошибка валидации
настроек на старте не печатает вход (`hide_input_in_errors`).

## Настройки (env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `APP_ENV` | `dev` | `k8s`/`prod`: пустой или `dev-llm-token` токен и `GIGACHAT_VERIFY_SSL=false` — падение на старте |
| `LLM_GATEWAY_TOKEN` | `dev-llm-token` | Ожидаемый `X-Internal-Token`. При любом `APP_ENV` с настоящим `GIGACHAT_CREDENTIALS` (и `LLM_PROVIDER=gigachat`) пустой или `dev-llm-token` токен не принимается: все вызовы — 401, в лог — ошибка на старте. `make dev-env` и `make llm-secret` генерируют случайный |
| `LLM_PROVIDER` | `gigachat` | `gigachat` \| `stub` (детерминированный ответ без сети) |
| `GIGACHAT_CREDENTIALS` | `""` | Authorization key (base64). Пусто → 503 `llm_not_configured` |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` | |
| `GIGACHAT_MODEL_STRONG` | `GigaChat-2-Pro` | `tier=strong` |
| `GIGACHAT_MODEL_FAST` | `GigaChat-2` | `tier=fast` («GigaChat 2 Lite»; имя `GigaChat-2-Lite` шлюз переводит в `GigaChat-2`) |
| `GIGACHAT_AUTH_URL` | `https://ngw.devices.sberbank.ru:9443/api/v2/oauth` | |
| `GIGACHAT_API_URL` | `https://gigachat.devices.sberbank.ru/api/v1` | Чат — `{API_URL}/chat/completions` |
| `GIGACHAT_CA_BUNDLE` | `""` | PEM НУЦ; в k8s и compose — `/etc/alma/certs/russian_trusted_bundle.pem` |
| `GIGACHAT_VERIFY_SSL` | `true` | `false` — только dev |
| `GIGACHAT_PROXY_URL` | `""` | Необязательный HTTPS-прокси |
| `LLM_MAX_CONCURRENCY` | `1` | Одновременных вызовов GigaChat |
| `LLM_QUEUE_MAX` | `8` | Ждущих сверх лимита → 429 `queue_full` |
| `LLM_QUEUE_TIMEOUT_SECONDS` | `180` | Дольше ждать слот → 503 `queue_timeout` |
| `LLM_TIMEOUT_SECONDS` | `120` | Бюджет на все попытки после слота, включая OAuth |
| `LLM_CONNECT_TIMEOUT_SECONDS` | `10` | |
| `LLM_RETRY_ATTEMPTS` | `3` | Всего попыток на ретраибельные сбои |
| `LLM_RETRY_BASE_DELAY_SECONDS` | `2` | База экспоненты |
| `LLM_DAILY_TOKEN_CAP` | `200000` | `total_tokens` за сутки МСК; 0 — без лимита |
| `LLM_MAX_INPUT_CHARS` | `60000` | Сумма `content` |
| `LLM_MAX_OUTPUT_TOKENS` | `2048` | Верхняя граница `max_tokens` |
| `LOG_LEVEL` | `INFO` | |

## TLS: сертификаты НУЦ Минцифры

Цепочка `*.devices.sberbank.ru` подписана **Russian Trusted Root CA**, которого нет в certifi.
Публичные Root CA и Sub CA лежат в `deploy/k8s/base/llm-gateway/certs/russian_trusted_bundle.pem`
(одна копия: из неё и ConfigMap `llm-gateway-ca` через `configMapGenerator` base, и том compose).
Шлюз строит контекст `certifi + бандл`, нормализуя CRLF → LF. Нет файла — предупреждение в лог
и `ca_bundle=missing`, старт не падает.

**Ротация**: Sub CA действует **до 06.03.2027** (Root CA — до 27.02.2032). Новый бандл —
заменить PEM в `deploy/k8s/base/llm-gateway/certs/` и `make k8s-apply`: имя ConfigMap с
хеш-суффиксом изменится, и Deployment перекатится сам, без пересборки образа.

## Секреты

Secret `crm-llm` (gitignored `deploy/k8s/overlays/<оверлей>/secrets/crm-llm.env`, шаблон —
`crm-llm.env.example`):

| Ключ | Кто видит |
|---|---|
| `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`, `GIGACHAT_MODEL_*` | только `llm-gateway` (`envFrom`) |
| `LLM_GATEWAY_TOKEN` (случайный hex, `make k8s-secrets`) | `llm-gateway` и контейнер `backend` — точечно `secretKeyRef` |

Init-контейнер `wait-migrations`, Job `seed-demo` и шаблон `demo-reset` тоже строят полный
`Settings` backend, но токен им не монтируется: они перекрывают `AI_SUMMARY_PROVIDER=rules`
(crm-config задаёт `gigachat`), и проверка «боевой токен при gigachat» к ним не относится.

В compose шлюз опубликован только на loopback (`127.0.0.1:8040`): backend ходит к нему по
сети compose, а `0.0.0.0` у Docker Desktop — это интерфейс локальной сети.

Заполнить ключ (значения не печатаются — только имена ключей):

```bash
make k8s-secrets                                                   # crm-llm.env со случайным токеном
make llm-secret LLM_SRC=~/secrets/gigachat.env                     # k3d (OVERLAY по умолчанию)
make llm-secret LLM_SRC=~/secrets/gigachat.env OVERLAY=compose     # deploy/compose/.env
```

`LLM_SRC` — любой env-файл; переносятся только `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`,
`GIGACHAT_MODEL_STRONG`, `GIGACHAT_MODEL_FAST`. После смены ключа в кластере —
`make k8s-apply` (секрет пересоздастся) и `kubectl -n crm rollout restart deploy/llm-gateway`.

**VPS** (оверлей `vm`): ключ кладётся на сервер вручную и не покидает его —
`/root/alma-secrets/gigachat.env` с `GIGACHAT_*`. `make vm-deploy` сам вызывает на VPS
`llm-secret.sh … deploy/k8s/overlays/vm`; вручную:

```bash
ssh root@<VPS> 'cd /opt/crm && sh deploy/scripts/llm-secret.sh /root/alma-secrets/gigachat.env deploy/k8s/overlays/vm'
```

Без ключа шлюз отвечает 503 `llm_not_configured`, а CRM предлагает сводку по правилам.

## Смоук с живым GigaChat

```bash
make llm-smoke       # с этой машины: env из secrets/crm-llm.env оверлея k3d, бандл НУЦ из репозитория
make llm-smoke-k8s   # из DMZ-пода llm-gateway: заодно проверяет egress 443/9443 через NetworkPolicy
```

`python -m app.tools.smoke` требует `ALMA_LLM_LIVE=1` (иначе exit 2) и `GIGACHAT_CREDENTIALS`
(иначе exit 2 со списком переменных). Шаги: OAuth («токен получен, истекает через N мин» —
без значения), `tier=fast` «Ответь одним словом: ОК», `tier=strong` со схемой сводки на
синтетических агрегатах (2 псевдонима, 6 чисел, без ПДн) — JSON разбирается, поля на месте.
Печатает модель, задержку, токены и `structured_supported`. Exit 0 — всё ок, 1 — ошибки.

Результат 17.10.2026 (ключ `GIGACHAT_API_PERS`, `make llm-smoke` и `make llm-smoke-k8s`):

- хост чата `gigachat.devices.sberbank.ru/api/v1` (по умолчанию) отвечает, TLS — через бандл НУЦ;
  `api.giga.chat/v1` (дефолт SDK 0.2.3) тоже отвечает тем же токеном
  и дополнительно отдаёт модели GigaChat-3 — переключение одной переменной `GIGACHAT_API_URL`;
- OAuth ≈ 0,3 с; `GigaChat-2` ≈ 0,4 с; `GigaChat-2-Pro` со схемой ≈ 2,2 с на коротком входе,
  `structured_supported=true` (structured output принят);
- модели `GigaChat-2-Lite` в `GET /models` нет ни на одном хосте (404 No such model) — «Lite»
  в API называется `GigaChat-2`; шлюз переводит старое имя сам;
- из пода `backend` соединение с `gigachat.devices.sberbank.ru:443` отклоняется политикой,
  из `notification` закрыт порт 9443 (OAuth Сбера) — к GigaChat ходит только `llm-gateway`.

## Разработка и тесты

```bash
cd services/llm-gateway
uv sync --group dev
uv run pytest -q                 # MockTransport: OAuth, chat, structured, ретраи, бюджет, очередь, логи
uv run ruff check app tests
ALMA_LLM_LIVE=1 uv run pytest -q tests/test_live.py   # живой GigaChat, env — как у make llm-smoke
```

В compose (`make dev`) шлюз поднимается рядом с backend на `localhost:8040` с hot-reload;
`LLM_PROVIDER=stub` в `deploy/compose/.env` — ответы без сети и ключа.
