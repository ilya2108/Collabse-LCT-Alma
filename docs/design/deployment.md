# Инфраструктура и деплой — CRM для работы с вузами (ЛЦТ 2026)

> Документ проектного уровня: по нему пишется весь код инфраструктуры без дополнительных вопросов.
> Сверен с манифестами `deploy/` и `Makefile`: три overlay (`minikube`, `k3d`, `vm`), компонент
> `prod-hardening`, три CronJob и приостановленный шаблон `demo-reset`, прод-контур — §4.4.
> Источник требований — техническое задание кейса и решения кейсодержателя (протокол Q&A
> 16.09.2026, в репозиторий не входит).
> Смежные документы: `docs/design/OVERVIEW.md` (сводная архитектура, модули backend),
> `docs/design/data-model.md` (БД), `docs/design/api-contract.md` (контракты LMS/CMS/notification).

---

## 0. TL;DR

- **Kubernetes — витрина решения.** Один kustomize `base` + три overlay: `minikube` и `k3d` — локальный стенд, `vm` — публичный демо-стенд на голом k3s с TLS (§4.3). В кластере: Deployments c HPA и probes, StatefulSet'ы с PVC, Jobs (миграции/сиды/бакеты), три CronJob (зависшие заявки, физическое удаление по 152-ФЗ, очистка ключей идемпотентности) и приостановленный CronJob-шаблон `demo-reset` (сброс стенда к показу, §3.8), Ingress, NetworkPolicy как иллюстрация закрытого контура.
- **Модульный монолит + 3 сервиса-сателлита** (notification, lms-stub, cms-stub) — осознанный выбор, допустимый по решениям кейсодержателя («допустимы монолит или микросервисы»); обоснование в §1.
- **152-ФЗ:** ключ шифрования ПДн живёт только в k8s Secret (`crm-pii`), генерируется при установке, в git не попадает.
- **Закрытый контур:** default-deny NetworkPolicy, точка-точка backend↔stubs/БД; notification-gateway — единственный pod с egress в интернет, в проде выносится в DMZ (диаграмма в §3.11).
- **300+ параллельных пользователей:** HPA backend 2→6 реплик + расчёт ёмкости, бюджет соединений PostgreSQL и k6-сценарий (§8) — прямой ответ кейсодержателю; профили НФТ «50 пользователей» и «10 параллельных отчётов» — §8.2.
- Локальная разработка — `docker-compose.dev.yml` (`make dev`), стенд — `make k8s-up` (k3d) / `make k8s-up-minikube`, публичный демо-стенд — `make vm-install` + `make vm-deploy`.
- **nip.io и Let's Encrypt — только для демо-стенда.** В проде — корпоративный DNS, ingress-контроллер контура заказчика и сертификат внутреннего CA; меняется один overlay, код и `base` — нет (§4.4).

### Фиксированные порты

| Компонент | Порт контейнера | Порт Service | Примечание |
|---|---|---|---|
| backend (FastAPI) | 8000 | 8000 | uvicorn, 2 воркера |
| frontend (nginx) | 8080 | **80** | nginx-unprivileged слушает 8080 под non-root; Service 80→8080 |
| notification (FastAPI+aiogram) | 8010 | 8010 | long-polling к Telegram |
| lms-stub | 8020 | 8020 | |
| cms-stub | 8030 | 8030 | |
| keycloak | 8080 (+9000 mgmt) | 8080 | health на mgmt-порту 9000 |
| postgres | 5432 | 5432 | |
| minio | 9000 / 9001 | 9000 / 9001 | API / Console |
| keydb | 6379 | 6379 | |

---

## 1. Обоснование: модульный монолит + Kubernetes (а не микросервисы)

Решения кейсодержателя допускают оба варианта: «монолит с feature-флагами» или «микросервисы + minikube/k3s». Мы выбираем **модульный монолит** (пакеты-модули `workflow`, `integrations`, `import_export`, `talent_pool`, `reporting`, `iam` внутри одного FastAPI-приложения) **плюс отдельные сервисы там, где граница реальная**, и при этом деплоим всё в Kubernetes.

Почему монолит, а не микросервисы:

1. **Транзакционная целостность workflow-движка.** Ключевое требование кейсодержателя — при изменении схемы workflow «на лету» существующие заявки сопоставляются на новую схему и работа не встаёт. Это одна ACID-транзакция над заявками, статусами и историей. В микросервисах это сага с компенсациями — риск, который на хакатоне ничем не окупается.
2. **Скорость команды.** Один репозиторий моделей SQLAlchemy, один Alembic, одна Pydantic-схема на модуль; нет версионирования межсервисных контрактов внутри ядра.
3. **Меньше режимов отказа.** Нет каскадных таймаутов между «микросервисами», которые на самом деле ходят в одну БД.
4. **Путь масштабирования не закрыт.** Модули общаются только через явные интерфейсы пакетов; вынос модуля в сервис — механическая операция. План масштабирования (§8) — горизонтальные реплики монолита за балансировщиком, ровно как кейсодержатель и предложил («реплики сервисов / второй инстанс монолита + балансировка»).

Где границы сервисов настоящие — там они и проведены:

- **notification-service** — другой контур безопасности (единственный egress в интернет → DMZ), другой runtime-паттерн (long-polling aiogram, долгоживущее соединение), независимый цикл падений: лежащий Telegram не должен трогать CRM.
- **lms-stub / cms-stub** — по определению имитируют *внешние* системы; отдельные процессы делают проверку двусторонности честной (проверяющий видит, что данные реально пришли по сети в другую систему).

Почему при монолите всё равно Kubernetes: k8s даёт нам не «микросервисность», а операционные примитивы, которые прямо отвечают требованиям — HPA (300+ пользователей), CronJob (зависшие заявки, очистки), NetworkPolicy (закрытый контур), Jobs (миграции/сиды), probes и самовосстановление. И тот же манифест без изменений разворачивается в контуре заказчика (§4.4).

---

## 2. Образы и Dockerfiles

Общие правила для всех образов:

- multi-stage, финальный слой минимальный;
- **non-root**: явный пользователь `uid=10001` (для nginx — штатный uid 101 образа `nginx-unprivileged`);
- `imagePullPolicy: IfNotPresent`, теги `crm/<name>:dev` (локальная сборка, без registry — образы импортируются в кластер, см. Makefile §6);
- никакой секретики в слоях образа — всё через env.

### 2.1 backend — `backend/Dockerfile`

```dockerfile
# --- builder ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime ---
FROM python:3.12-slim-bookworm
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app \
    && apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

Замечания:
- `curl` в runtime-слое нужен намеренно: этот же образ используется Job'ами и удобен для отладки probes;
- команды `alembic upgrade head` и `python -m app.seed` запускаются **из этого же образа** (Jobs §3.8) — один артефакт, никаких расхождений версий кода и миграций;
- 2 воркера uvicorn на под: масштабируемся подами (HPA), а не воркерами — так метрика CPU на под предсказуема;
- PDF-отчёты (дашборд, «Активность КАМов») строит `fpdf2` — чистый Python, зависимость зафиксирована в `uv.lock`; шрифты DejaVu (кириллица, ▲▼, ₽) лежат в пакете `app/modules/reporting/fonts/`. Системные библиотеки (pango/cairo, как для weasyprint) и шрифты ОС в образе не нужны.

### 2.2 frontend — `frontend/Dockerfile`

```dockerfile
# --- build ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# конфиг рантайма (URL Keycloak и т.п.) кладётся не в бандл, а в /config.js — см. ниже
RUN npm run build

# --- runtime ---
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
```

`frontend/nginx.conf` (важное: non-root nginx слушает **8080**, SPA-fallback, healthz):

```nginx
server {
    listen       8080;
    server_name  _;
    root   /usr/share/nginx/html;
    index  index.html;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;

    location = /healthz { return 200 "ok"; add_header Content-Type text/plain; }

    # рантайм-конфиг (генерируется из ConfigMap, см. §3.7): window.__ENV__ = {...}
    location = /config.js {
        add_header Cache-Control "no-store";
    }

    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

`index.html` подключает `<script src="/config.js"></script>` до бандла; файл монтируется из ConfigMap `frontend-env` (§3.7). Так один и тот же образ работает в compose, minikube и k3d без пересборки (на VPS — та же сборка под linux/amd64, отличается только `config.js`) — реальный «12-factor».

`frontend/.dockerignore` и `backend/.dockerignore` обязательны (`node_modules`, `dist`, `.venv`, `__pycache__`, `.git`).

### 2.3 notification — `services/notification/Dockerfile`

Тот же шаблон, что backend (uv, non-root 10001), отличия:

```dockerfile
EXPOSE 8010
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
```

aiogram запускается как background-task внутри того же процесса FastAPI (lifespan): long-polling наружу + HTTP API 8010 внутрь (backend дергает `POST /notify`). Один процесс — одна точка probes.

### 2.4 lms-stub и cms-stub — `services/lms-stub/Dockerfile`, `services/cms-stub/Dockerfile`

Тот же шаблон; `EXPOSE 8020` / `EXPOSE 8030`, `CMD` с соответствующим портом. У заглушек есть мини-UI (одна HTML-страница «журнал полученных вызовов») — это то, что проверяющий открывает для проверки двусторонности.

---

## 3. Kubernetes: kustomize base

### 3.1 Дерево каталогов

```
deploy/k8s/
├── base/
│   ├── kustomization.yaml
│   ├── namespace.yaml                  # Namespace crm
│   ├── config/
│   │   ├── app-configmap.yaml          # crm-config: несекретные настройки всех сервисов
│   │   └── frontend-env-configmap.yaml # frontend-env: /config.js
│   ├── backend/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── hpa.yaml
│   ├── frontend/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   ├── notification/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   ├── lms-stub/  {deployment,service}.yaml
│   ├── cms-stub/  {deployment,service}.yaml
│   ├── postgres/
│   │   ├── statefulset.yaml
│   │   ├── service.yaml                # headless + обычный
│   │   └── init-configmap.yaml         # 01-init.sql: CREATE DATABASE keycloak
│   ├── minio/
│   │   ├── statefulset.yaml
│   │   └── service.yaml
│   ├── keydb/
│   │   ├── deployment.yaml             # кэш: state не нужен, emptyDir
│   │   └── service.yaml
│   ├── keycloak/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── realm-configmap.yaml        # crm-realm.json (import)
│   ├── jobs/
│   │   ├── db-migrate-job.yaml
│   │   ├── seed-demo-job.yaml
│   │   └── minio-init-job.yaml
│   ├── cronjobs/
│   │   ├── stuck-check-cronjob.yaml        # зависшие заявки, ежечасно
│   │   ├── purge-deleted-cronjob.yaml      # физическое удаление soft-deleted (152-ФЗ), ночью
│   │   ├── idempotency-purge-cronjob.yaml  # просроченные ключи Idempotency-Key, ежечасно
│   │   └── demo-reset-cronjob.yaml         # шаблон Job сброса стенда (suspend: true), §3.8
│   ├── ingress/
│   │   └── ingress.yaml
│   └── networkpolicy/
│       ├── 00-default-deny.yaml
│       ├── 10-allow-dns.yaml
│       ├── 20-allow-from-ingress.yaml
│       ├── 30-backend.yaml
│       ├── 31-data-layer.yaml
│       ├── 32-keycloak.yaml
│       ├── 35-jobs-stubs.yaml          # Jobs/CronJob и заглушки под default-deny
│       └── 40-notification-dmz.yaml
├── components/
│   └── prod-hardening/                 # сужение доверия при передаче стенда наружу (§4.4)
├── overlays/
│   ├── minikube/
│   │   ├── kustomization.yaml
│   │   ├── secrets/*.env.example       # шаблоны; secrets/*.env — в .gitignore
│   │   └── patches/ ...
│   ├── k3d/
│   │   ├── kustomization.yaml
│   │   ├── secrets/*.env.example
│   │   └── patches/ ...                # traefik, *.localhost, local-path
│   └── vm/                             # публичный демо-стенд на k3s (§4.3)
│       ├── kustomization.yaml
│       ├── secrets/*.env.example       # секреты генерируются на самой VM
│       ├── resources/                  # redirect http→https, NetworkPolicy ACME-солвера
│       └── patches/ ...                # хосты nip.io + tls, Keycloak/iss, ресурсы, HPA
└── vm-addons/                          # ставится до overlay vm (deploy/scripts/vm-deploy.sh)
    ├── cert-manager.yaml               # cert-manager v1.21.2
    └── cluster-issuer.yaml             # ClusterIssuer letsencrypt-prod (http01 через traefik)
```

`base/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: crm
resources:
  - namespace.yaml
  - config/app-configmap.yaml
  - config/frontend-env-configmap.yaml
  - postgres/statefulset.yaml
  - postgres/service.yaml
  - postgres/init-configmap.yaml
  - minio/statefulset.yaml
  - minio/service.yaml
  - keydb/deployment.yaml
  - keydb/service.yaml
  - keycloak/deployment.yaml
  - keycloak/service.yaml
  - keycloak/realm-configmap.yaml
  - backend/deployment.yaml
  - backend/service.yaml
  - backend/hpa.yaml
  - frontend/deployment.yaml
  - frontend/service.yaml
  - notification/deployment.yaml
  - notification/service.yaml
  - lms-stub/deployment.yaml
  - lms-stub/service.yaml
  - cms-stub/deployment.yaml
  - cms-stub/service.yaml
  - jobs/db-migrate-job.yaml
  - jobs/seed-demo-job.yaml
  - jobs/minio-init-job.yaml
  - cronjobs/stuck-check-cronjob.yaml
  - cronjobs/purge-deleted-cronjob.yaml
  - cronjobs/idempotency-purge-cronjob.yaml
  - cronjobs/demo-reset-cronjob.yaml   # шаблон Job сброса стенда (suspend)
  - ingress/ingress.yaml
  - networkpolicy/00-default-deny.yaml
  - networkpolicy/10-allow-dns.yaml
  - networkpolicy/20-allow-from-ingress.yaml
  - networkpolicy/30-backend.yaml
  - networkpolicy/31-data-layer.yaml
  - networkpolicy/32-keycloak.yaml
  - networkpolicy/35-jobs-stubs.yaml
  - networkpolicy/40-notification-dmz.yaml
labels:
  - includeSelectors: false
    pairs:
      app.kubernetes.io/part-of: crm
```

Соглашение о метках (используется Service-селекторами и NetworkPolicy):

| Метка | Значения |
|---|---|
| `app.kubernetes.io/name` | `backend`, `frontend`, `notification`, `lms-stub`, `cms-stub`, `postgres`, `minio`, `keydb`, `keycloak` |
| `tier` | `app` (backend, frontend, stubs), `data` (postgres, minio, keydb), `iam` (keycloak), `dmz` (notification) |
| `role` | `job` — у подов Jobs/CronJob (для NetworkPolicy) |

### 3.2 Общая топология (диаграмма)

```mermaid
flowchart TB
    subgraph host["Хост разработчика / стенда"]
        HOSTS["k3d: *.crm.localhost -> 127.0.0.1 без /etc/hosts\nminikube и curl: *.crm.local через /etc/hosts\nVPS: *.168-113-158-10.nip.io"]
    end
    subgraph cluster["Kubernetes-кластер, namespace crm"]
        ING["Ingress (nginx / traefik)"]
        subgraph apps["tier=app"]
            FE["Deployment frontend x1"]
            BE["Deployment backend x2..6 + HPA"]
            LMS["Deployment lms-stub x1"]
            CMS["Deployment cms-stub x1"]
        end
        subgraph dmzseg["tier=dmz"]
            NT["Deployment notification x1"]
        end
        subgraph iam["tier=iam"]
            KC["Deployment keycloak x1"]
        end
        subgraph data["tier=data"]
            PG[("StatefulSet postgres + PVC 2Gi")]
            MC[("StatefulSet minio + PVC 5Gi")]
            KD[("Deployment keydb, emptyDir")]
        end
        JOBS["Jobs: db-migrate, minio-init, seed-demo"]
        CRON["CronJob: stuck-check, idempotency-purge (hourly),\npurge-deleted (daily), demo-reset (suspend)"]
    end
    HOSTS --> ING
    ING --> FE
    ING -->|"/api"| BE
    ING --> KC
    ING --> LMS
    ING --> CMS
    ING --> MC
    BE --> PG
    BE --> MC
    BE --> KD
    BE -->|JWKS| KC
    BE <-->|"JSON API (двусторонне)"| LMS
    BE <-->|"JSON API (двусторонне)"| CMS
    BE -->|"POST /notify"| NT
    NT -->|"client credentials"| KC
    NT -->|"вызовы API по ролям"| BE
    KC --> PG
    JOBS --> PG
    JOBS --> MC
    CRON -->|"POST /api/v1/internal/jobs/*\nX-Internal-Token"| BE
```

### 3.3 Deployments приложений

Сводная таблица (полный YAML показан для backend — остальные строятся по нему):

| Deployment | Реплики | Образ | initContainers | Ключевые env (источник) |
|---|---|---|---|---|
| `backend` | 2 (HPA до 6) | `crm/backend:dev` | `wait-postgres`, `wait-migrations` | `DATABASE_URL` (Secret `crm-db`), `PII_FERNET_KEYS`/`PII_HMAC_KEY` (Secret `crm-pii`), `S3_*` (Secret `crm-s3`), `KEYDB_URL`, `KEYCLOAK_*`, `LMS_BASE_URL`, `CMS_BASE_URL`, `NOTIFICATION_BASE_URL`, `INTERNAL_API_TOKEN` (Secret `crm-internal`) — несекретное из ConfigMap `crm-config` |
| `frontend` | 1 | `crm/frontend:dev` | — | volume `frontend-env` → `/usr/share/nginx/html/config.js` |
| `notification` | 1 (строго: long-polling — единственный consumer Telegram) | `crm/notification:dev` | `wait-keycloak` | `TELEGRAM_BOT_TOKEN` (Secret `crm-telegram`), `KC_CLIENT_ID=crm-notification`, `KC_CLIENT_SECRET` (Secret `crm-keycloak-clients`), `BACKEND_BASE_URL`, `INTERNAL_API_TOKEN` |
| `lms-stub` | 1 | `crm/lms-stub:dev` | — | `CRM_WEBHOOK_URL=http://backend:8000/api/v1/integrations/lms/webhook`, `LMS_WEBHOOK_SECRET` (Secret `crm-internal`) — HMAC-подпись X-Webhook-Signature |
| `cms-stub` | 1 | `crm/cms-stub:dev` | — | аналогично: `.../cms/webhook`, `CMS_WEBHOOK_SECRET` |

`base/backend/deployment.yaml` — эталон:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  labels: {app.kubernetes.io/name: backend, tier: app}
spec:
  replicas: 2
  selector:
    matchLabels: {app.kubernetes.io/name: backend}
  template:
    metadata:
      labels: {app.kubernetes.io/name: backend, tier: app}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        seccompProfile: {type: RuntimeDefault}
      initContainers:
        - name: wait-postgres
          image: postgres:16-alpine
          command: ["sh", "-c",
            "until pg_isready -h postgres -p 5432 -U $PGUSER; do echo waiting; sleep 2; done"]
          env:
            - name: PGUSER
              valueFrom: {secretKeyRef: {name: crm-db, key: POSTGRES_USER}}
        - name: wait-migrations
          image: crm/backend:dev
          imagePullPolicy: IfNotPresent
          # ждём, пока Job db-migrate доведёт схему до head (см. §3.8)
          command: ["sh", "-c",
            "until python -m app.tools.check_migrations; do echo 'waiting for alembic head'; sleep 3; done"]
          envFrom:
            - configMapRef: {name: crm-config}
            - secretRef: {name: crm-db}
      containers:
        - name: backend
          image: crm/backend:dev
          imagePullPolicy: IfNotPresent
          ports: [{containerPort: 8000, name: http}]
          envFrom:
            - configMapRef: {name: crm-config}
            - secretRef: {name: crm-db}
            - secretRef: {name: crm-s3}
            - secretRef: {name: crm-pii}
            - secretRef: {name: crm-internal}
          startupProbe:
            httpGet: {path: /healthz/live, port: http}
            periodSeconds: 2
            failureThreshold: 30          # до 60 с на старт
          readinessProbe:
            httpGet: {path: /healthz/ready, port: http}   # проверяет PG, KeyDB, MinIO, JWKS-кэш
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: {path: /healthz/live, port: http}    # только event loop, без зависимостей
            periodSeconds: 10
            failureThreshold: 3
          resources:
            requests: {cpu: 200m, memory: 256Mi}
            limits:   {cpu: "1",  memory: 512Mi}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false   # /tmp нужен для XLSX-импорта (openpyxl/xlrd)
            capabilities: {drop: ["ALL"]}
```

Правила probes для всех сервисов:

| Сервис | liveness | readiness | startup |
|---|---|---|---|
| backend | `GET /healthz/live` (без зависимостей) | `GET /healthz/ready` (SELECT 1 в PG; PING в KeyDB; head Alembic применён; bucket-check MinIO — с кэшем 10 с) | 60 с |
| frontend | `GET /healthz` :8080 | тот же | — |
| notification | `GET /healthz` :8010 | `GET /healthz/ready` (бот запущен, Keycloak-токен получен) | 60 с |
| lms/cms-stub | `GET /healthz` | тот же | — |
| keycloak | `GET /health/live` :9000 | `GET /health/ready` :9000 | 300 с (JVM + import realm) |
| postgres | `exec pg_isready -U $POSTGRES_USER` | тот же | — |
| minio | `GET /minio/health/live` :9000 | тот же | — |
| keydb | `exec keydb-cli ping` | тот же | — |

**Важно:** liveness никогда не проверяет внешние зависимости (иначе падение PG перезапускает все поды каскадом); зависимостями управляет readiness.

### 3.4 Stateful-слой

#### postgres — StatefulSet (реплика 1, PVC 2Gi)

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  labels: {app.kubernetes.io/name: postgres, tier: data}
spec:
  serviceName: postgres-hl
  replicas: 1
  selector:
    matchLabels: {app.kubernetes.io/name: postgres}
  template:
    metadata:
      labels: {app.kubernetes.io/name: postgres, tier: data}
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          # бюджет соединений §8.4: 118 < 150 (tests/test_connection_budget.py)
          args: ["postgres", "-c", "max_connections=150"]
          ports: [{containerPort: 5432, name: pg}]
          env:
            - name: POSTGRES_DB
              value: crm
          envFrom:
            - secretRef: {name: crm-db}       # POSTGRES_USER, POSTGRES_PASSWORD
          volumeMounts:
            - {name: data, mountPath: /var/lib/postgresql/data, subPath: pgdata}
            - {name: initdb, mountPath: /docker-entrypoint-initdb.d, readOnly: true}
          readinessProbe:
            exec: {command: ["sh", "-c", "pg_isready -U $POSTGRES_USER -d crm"]}
            periodSeconds: 5
          livenessProbe:
            exec: {command: ["sh", "-c", "pg_isready -U $POSTGRES_USER -d crm"]}
            periodSeconds: 10
          resources:
            requests: {cpu: 250m, memory: 512Mi}
            limits:   {cpu: "1",  memory: 1Gi}
      volumes:
        - name: initdb
          configMap: {name: postgres-init}
  volumeClaimTemplates:
    - metadata: {name: data}
      spec:
        accessModes: ["ReadWriteOnce"]
        resources: {requests: {storage: 2Gi}}
        # storageClassName задаётся в overlay
```

`postgres/init-configmap.yaml` → `01-init.sql` (выполняется только при первом старте пустого тома):

```sql
CREATE DATABASE keycloak;
-- отдельная БД для Keycloak в том же инстансе: один StatefulSet, две изолированные схемы данных
```

Два Service: `postgres-hl` (headless, `clusterIP: None`, для StatefulSet) и обычный `postgres` (ClusterIP) — приложения ходят по короткому DNS `postgres:5432`.

#### minio — StatefulSet (реплика 1, PVC 5Gi)

- образ `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` (пин релиза), args: `["server", "/data", "--console-address", ":9001"]`;
- env из Secret `crm-s3`: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`;
- `MINIO_BROWSER_REDIRECT_URL=http://minio.crm.local` — чтобы Console корректно работала за Ingress;
- PVC `5Gi` через `volumeClaimTemplates`, mountPath `/data`;
- probes: `GET /minio/health/live` на 9000;
- бакеты создаёт Job `minio-init` (§3.8), не приложение.

#### keydb — Deployment (кэш, состояние не критично)

- образ `eqalpha/keydb` v6.3.4, закреплённый по digest (`@sha256:…`), args: `["keydb-server", "--appendonly", "no", "--maxmemory", "192mb", "--maxmemory-policy", "allkeys-lru"]`;
- том `emptyDir` — это осознанно: KeyDB у нас только кэш (карточки, страницы, дашборды, one-time коды бота с TTL) — потеря содержимого не ломает систему, всё восстановимо из PG. Поэтому Deployment, а не StatefulSet — это осознанное решение, а не упрощение. По той же причине KeyDB не годится в шину интеграций: события живут только в outbox PostgreSQL.

#### keycloak — Deployment (реплика 1; состояние — в PostgreSQL)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  labels: {app.kubernetes.io/name: keycloak, tier: iam}
spec:
  replicas: 1
  selector: {matchLabels: {app.kubernetes.io/name: keycloak}}
  template:
    metadata:
      labels: {app.kubernetes.io/name: keycloak, tier: iam}
    spec:
      initContainers:
        - name: wait-postgres
          image: postgres:16-alpine
          command: ["sh", "-c", "until pg_isready -h postgres -p 5432; do sleep 2; done"]
      containers:
        - name: keycloak
          image: quay.io/keycloak/keycloak:26.0
          args: ["start", "--import-realm"]
          env:
            - {name: KC_DB, value: postgres}
            - {name: KC_DB_URL, value: "jdbc:postgresql://postgres:5432/keycloak"}
            - name: KC_DB_USERNAME
              valueFrom: {secretKeyRef: {name: crm-db, key: POSTGRES_USER}}
            - name: KC_DB_PASSWORD
              valueFrom: {secretKeyRef: {name: crm-db, key: POSTGRES_PASSWORD}}
            - name: KC_BOOTSTRAP_ADMIN_USERNAME
              valueFrom: {secretKeyRef: {name: crm-keycloak-admin, key: username}}
            - name: KC_BOOTSTRAP_ADMIN_PASSWORD
              valueFrom: {secretKeyRef: {name: crm-keycloak-admin, key: password}}
            - {name: KC_HEALTH_ENABLED, value: "true"}
            - {name: KC_HTTP_ENABLED, value: "true"}
            - {name: KC_PROXY_HEADERS, value: xforwarded}
            - {name: KC_HOSTNAME, value: "http://id.crm.local"}
            - {name: KC_HOSTNAME_BACKCHANNEL_DYNAMIC, value: "true"}
          ports:
            - {containerPort: 8080, name: http}
            - {containerPort: 9000, name: mgmt}
          volumeMounts:
            - {name: realm, mountPath: /opt/keycloak/data/import, readOnly: true}
          startupProbe:
            httpGet: {path: /health/started, port: mgmt}
            periodSeconds: 5
            failureThreshold: 60      # JVM + import realm: до 5 минут
          readinessProbe:
            httpGet: {path: /health/ready, port: mgmt}
            periodSeconds: 10
          livenessProbe:
            httpGet: {path: /health/live, port: mgmt}
            periodSeconds: 15
          resources:
            requests: {cpu: 500m, memory: 768Mi}
            limits:   {cpu: "1",  memory: 1536Mi}
      volumes:
        - name: realm
          configMap: {name: keycloak-realm}
```

**Критичный нюанс (двойное имя Keycloak).** Браузер ходит на `http://id.crm.local` (через Ingress), backend — на внутренний `http://keycloak:8080`. Чтобы токены валидировались:
- `KC_HOSTNAME=http://id.crm.local` фиксирует `iss=http://id.crm.local/realms/crm` во всех токенах;
- `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` разрешает обращения по внутреннему имени;
- backend конфигурируется **двумя** URL: `KEYCLOAK_INTERNAL_URL=http://keycloak:8080` (откуда качать JWKS) и `KEYCLOAK_ISSUER=http://id.crm.local/realms/crm` (что ожидать в `iss`).

**Импорт realm** — не Job, а штатный `start --import-realm` через обёртку `import-realm.sh` (подставляет client secrets из env в плейсхолдеры `${env.*}`, §5): файл `realm-crm.json` (ConfigMap `keycloak-realm`) содержит realm `crm`, клиентов `crm-frontend` (public, PKCE S256, redirect `http://crm.localhost/*`, `http://crm.local/*`, `http://localhost:5173/*`, `http://localhost/*` и хост VPS `https://crm.<IP>.nip.io/*`; в проде здесь хост корпоративного DNS, §4.4), `crm-notification` (confidential, service accounts, роль `svc_notification` — Telegram-бот), `crm-integration` (confidential, роль `svc_integration` — bearer-альтернатива HMAC для webhook'ов, по умолчанию не используется), `crm-loadtest` (confidential, Direct Access Grants — только для нагрузочного), канонические роли (`admin`, `head_kam`, `kam`, `observer` + сервисные `svc_notification`, `svc_integration` — список зафиксирован в `OVERVIEW.md`) и **преднастроенных пользователей по ролям с паролями** (`admin@demo`, `head@demo`, `kam1@demo`, `kam2@demo`, `observer@demo`) — их логины/пароли публикуются в README (решение кейсодержателя). Роли из realm — только стартовое назначение при первом входе; дальше роли людей хранятся в БД Альмы. При изменении realm-JSON достаточно `kubectl rollout restart deployment/keycloak`: import идемпотентен (существующий realm не перезатирается, стратегия `IGNORE_EXISTING`).

### 3.5 Services

Все — ClusterIP (наружу только Ingress):

| Service | selector (`app.kubernetes.io/name`) | port → targetPort |
|---|---|---|
| `backend` | backend | 8000 → 8000 |
| `frontend` | frontend | **80 → 8080** |
| `notification` | notification | 8010 → 8010 |
| `lms-stub` | lms-stub | 8020 → 8020 |
| `cms-stub` | cms-stub | 8030 → 8030 |
| `keycloak` | keycloak | 8080 → 8080 |
| `postgres`, `postgres-hl` (headless) | postgres | 5432 → 5432 |
| `minio` | minio | 9000 → 9000, 9001 → 9001 |
| `keydb` | keydb | 6379 → 6379 |

### 3.6 Ingress

Один манифест, две группы по пять хостов. `ingressClassName` в base — `nginx` (addon minikube), overlay k3d и vm патчат его на `traefik` (встроен в k3s).

| Хост (k3d, канон) | Алиас (minikube, curl) | Куда |
|---|---|---|
| `crm.localhost` | `crm.local` | `/api` → backend:8000, `/` → frontend:80 |
| `id.crm.localhost` | `id.crm.local` | keycloak:8080 (браузерный OIDC-редирект) |
| `lms.crm.localhost` | `lms.crm.local` | lms-stub:8020 — UI журнала обмена |
| `cms.crm.localhost` | `cms.crm.local` | cms-stub:8030 |
| `minio.crm.localhost` | `minio.crm.local` | minio:9001 (Console) |

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: crm
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"     # XLSX/XLS-импорт больших списков
    nginx.ingress.kubernetes.io/proxy-buffering: "off"     # SSE /api/v1/events/stream
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
spec:
  ingressClassName: nginx
  rules:
    - host: crm.localhost                 # основное приложение (канон для k3d, secure context)
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend: {service: {name: backend, port: {number: 8000}}}
          - path: /
            pathType: Prefix
            backend: {service: {name: frontend, port: {number: 80}}}
    - host: id.crm.localhost              # Keycloak (браузерный OIDC-редирект)
      http:
        paths:
          - path: /
            pathType: Prefix
            backend: {service: {name: keycloak, port: {number: 8080}}}
    # lms.crm.localhost, cms.crm.localhost, minio.crm.localhost — по тому же образцу;
    # группа *.crm.local повторяет те же правила (алиасы для curl/API и minikube)
```

- Фронт и API на **одном хосте** (пути `/` и `/api`) — CORS между ними не нужен вовсе.
- **Почему `*.localhost`.** Chromium и Edge считают `http://*.localhost` безопасным контекстом (secure context) и резолвят такие имена в 127.0.0.1 без `/etc/hosts`. keycloak-js использует Web Crypto для PKCE, а `window.crypto.subtle` есть только в безопасном контексте — поэтому вход в UI на k3d идёт через `http://crm.localhost`. Хосты `*.crm.local` остаются алиасами для curl/API и minikube (`/etc/hosts`, строку печатает `make hosts`); вход в UI через них невозможен.
- **TLS по средам.** Локальные overlay — HTTP: стенд работает на 127.0.0.1, и проверяющий не борется с предупреждением браузера о самоподписанном сертификате. Overlay `vm` — HTTPS: Ingress с аннотацией `cert-manager.io/cluster-issuer: letsencrypt-prod`, отдельный `tls`-секрет на каждый хост, принудительный редирект http→https (traefik Middleware `redirect-https`), §4.3. В проде — сертификат внутреннего CA, §4.4.
- MinIO Console наружу выставляется только на локальном стенде; в overlay `vm` её хоста нет.

### 3.7 ConfigMaps и Secrets

**ConfigMaps (несекретное):**

| ConfigMap | Ключи | Потребители |
|---|---|---|
| `crm-config` | `APP_ENV=k8s`; `KEYCLOAK_INTERNAL_URL=http://keycloak:8080`; `KEYCLOAK_ISSUER=http://id.crm.local/realms/crm`; `KEYCLOAK_REALM=crm`; `KEYCLOAK_AUDIENCES=account,crm-frontend,crm-notification,crm-integration,crm-loadtest` (последний — только для демо, его убирает `prod-hardening`, §4.4); `KEYDB_URL=redis://keydb:6379/0`; `S3_ENDPOINT=http://minio:9000`; `S3_BUCKET_FILES=crm-files`; `S3_BUCKET_IMPORTS=crm-imports`; `S3_BUCKET_EXPORTS=crm-exports`; `LMS_BASE_URL=http://lms-stub:8020`; `CMS_BASE_URL=http://cms-stub:8030`; `NOTIFICATION_BASE_URL=http://notification:8010`; `BACKEND_BASE_URL=http://backend:8000`; `STUCK_DEFAULT_DAYS=14` (сидирует `app_setting['stuck_threshold_days_default']`; фактические пороги per-workflow/per-этап хранятся в БД и настраиваются в админке/конструкторе); `DB_POOL_SIZE=4`, `DB_MAX_OVERFLOW=2` — пул на процесс uvicorn, бюджет соединений — §8.4 | backend, notification, jobs |
| `frontend-env` | файл `config.js` (`KEYCLOAK_URL` патчат overlay k3d и vm): `window.__ENV__={KEYCLOAK_URL:"http://id.crm.local",KEYCLOAK_REALM:"crm",KEYCLOAK_CLIENT_ID:"crm-frontend",API_BASE:"/api"}` | frontend (volume) |
| `postgres-init` | `01-init.sql` | postgres (initdb) |
| `keycloak-realm` | `crm-realm.json` | keycloak (import) |

**Secrets (все `type: Opaque`; создаются `secretGenerator` overlay-я из gitignored `secrets.env`):**

| Secret | Ключи | Кто читает | Комментарий |
|---|---|---|---|
| `crm-db` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL=postgresql+asyncpg://user:pass@postgres:5432/crm` | postgres, keycloak, backend, jobs | |
| `crm-pii` | `PII_FERNET_KEYS` — список Fernet-ключей через запятую (первый — активный, MultiFernet-ротация); `PII_HMAC_KEY` — отдельный ключ ≥32 байт для blind-index поиска (схема шифрования — `data-model.md` §8) | **только backend и jobs** | **Ключи шифрования ПДн (152-ФЗ). Генерируются `make k8s-secrets` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` / `openssl rand -base64 32`), в git не попадают никогда; ротация = новый ключ первым в списке + команда перешифрования `python -m app.tools.rekey`.** |
| `crm-s3` | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | minio, backend, minio-init | minio-init создаёт отдельный access key для приложения — backend не ходит под root-учёткой |
| `crm-keycloak-admin` | `username`, `password` | keycloak | bootstrap-админ |
| `crm-keycloak-clients` | `NOTIFICATION_CLIENT_SECRET`, `INTEGRATION_CLIENT_SECRET`, `LOADTEST_CLIENT_SECRET` | notification, stubs (опц.), k6 | client secrets confidential-клиентов (те же значения подставляются в realm-JSON при генерации, см. `make k8s-secrets`) |
| `crm-telegram` | `TELEGRAM_BOT_TOKEN` | **только notification** | backend токена бота не видит — часть DMZ-стори |
| `crm-internal` | `INTERNAL_API_TOKEN` — 32 hex; `LMS_WEBHOOK_SECRET`, `CMS_WEBHOOK_SECRET` — per-system секреты HMAC-подписи webhook'ов (api-contract.md §13.6) | backend, notification, stubs, CronJob | межсервисные вызовы внутри контура: заголовок `X-Internal-Token`; подпись `X-Webhook-Signature` |

Правила:
- в репозитории лежат только `secrets.env.example` с плейсхолдерами; `make k8s-secrets` генерирует `secrets.env` со случайными значениями;
- поды получают секреты через `envFrom`/`valueFrom`, никакие секреты не попадают в ConfigMap, аргументы командной строки или образы;
- матрица «какой под какой Secret видит» выше — минимально необходимая; это же ограничивает blast radius при компрометации пода (152-ФЗ).

`KEYCLOAK_ISSUER` и `KC_HOSTNAME` в base указывают на `id.crm.local`; overlay k3d меняет их на `http://id.crm.localhost`, overlay vm — на `https://id.crm.<IP>.nip.io`. Настройки без значения в ConfigMap берутся из кода (`backend/app/core/config.py`) и переопределяются env: ключи идемпотентности — `IDEMPOTENCY_ENABLED` (вкл.), `IDEMPOTENCY_TTL_HOURS` (24), `IDEMPOTENCY_LEASE_SECONDS` (60); TTL кэша каталога ролей в памяти процесса — `RBAC_CATALOG_TTL_SECONDS` (5).

### 3.8 Jobs: миграции, бакеты, сиды

Выбор «Job, а не initContainer» для миграций: у backend 2+ реплик (HPA), initContainer в каждой реплике устроил бы гонку `alembic upgrade`. Поэтому:

- **Job `db-migrate`** — единственный, кто меняет схему;
- initContainer `wait-migrations` в backend (см. §3.3) только *ждёт* соответствия head (`python -m app.tools.check_migrations` — сравнивает `alembic_version` в БД со скриптами в образе, exit 0/1);
- Jobs неизменяемы, поэтому Makefile всегда делает `kubectl delete job --ignore-not-found` перед `apply` (обёрнуто в `make k8s-apply`).

`jobs/db-migrate-job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate
  labels: {app.kubernetes.io/name: db-migrate, role: job}
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels: {app.kubernetes.io/name: db-migrate, role: job}
    spec:
      restartPolicy: Never
      initContainers:
        - name: wait-postgres
          image: postgres:16-alpine
          command: ["sh", "-c", "until pg_isready -h postgres -p 5432; do sleep 2; done"]
      containers:
        - name: migrate
          image: crm/backend:dev
          imagePullPolicy: IfNotPresent
          command: ["alembic", "upgrade", "head"]
          envFrom:
            - configMapRef: {name: crm-config}
            - secretRef: {name: crm-db}
```

**Job `minio-init`** — образ `minio/mc`, скрипт:

```sh
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb -p local/crm-files local/crm-imports local/crm-exports
mc admin user add local "$S3_ACCESS_KEY" "$S3_SECRET_KEY" || true
mc admin policy attach local readwrite --user "$S3_ACCESS_KEY" || true
```

(initContainer ждёт `http://minio:9000/minio/health/live` через curl-loop; все команды идемпотентны.)

**Job `seed-demo`** — образ `crm/backend:dev`, `command: ["python", "-m", "app.seed"]`. Сид **идемпотентен** (upsert по натуральным ключам) и наполняет демо-стенд под демо-сценарий:

- workflow B2B и B2C с ветвлениями и возвратами (канонические seed-схемы — `api-contract.md` §5.1, `workflow-engine.md` §4–5);
- 8 базовых вузов, договоры, продукты, программы, 10 ИТ-направлений с привязкой программ и продуктов, заявки на разных этапах;
- 3–4 заявки с датой последнего перехода «−20 дней» — **готовая фактура для демо CronJob о зависших**;
- студенты Talent Pool (ФИО+email шифруются тем же кодом, что в проде — сид ходит через сервисный слой, а не raw SQL);
- пользователи CRM, связанные по `keycloak_sub` с преднастроенными пользователями realm
  (+ `manager_id`: kam → head_kam для эскалаций);
- каталог прав (сверка с кодом) и демо-роли «КАМ B2B» / «КАМ B2C» с их кодами в переходах
  своего процесса (`seed_demo_roles`);
- история активности для отчёта «Активность КАМов» (`app.tools.seed_activity`): ещё 18 КАМов
  с разными профилями, 48 закреплённых вузов (всего на стенде 56), заявки с историей
  переходов, договоры подписавших вузов, комментарии, файлы, мероприятия и студенты на
  программах; план детерминирован (фиксированное зерно);
- скрытый онбординг демо-учёток (приветствие пройдено, чек-лист свёрнут).

Запускается initContainer-ами `wait-postgres` + `wait-migrations` (как у backend) и после готовности backend не обязателен — пишет напрямую в БД через тот же код приложения.

**Сброс стенда к показу — `cronjob/demo-reset`.** Обычный сид идемпотентен, но «стареет»:
даты демо-данных отсчитаны от момента засева, через неделю зависших становится больше, а
«Завершено за месяц» пустеет. Шаблон `cronjobs/demo-reset-cronjob.yaml` — приостановленный
CronJob (`suspend: true`), сам по расписанию не запускается. `make demo-reset ANCHOR=ГГГГ-ММ-ДД`
(скрипт `deploy/scripts/demo-reset.sh`) ставит `stuck-check` на паузу (возвращает его и при
ошибке), создаёт Job `--from=cronjob/demo-reset` с `DEMO_ANCHOR_DATE` и ждёт его. Job выполняет
`python -m app.tools.seed --reset --anchor …`: одной транзакцией очищает доменные таблицы
(TRUNCATE без CASCADE; пользователи, каталог ролей и прав, настройки, процессы и журнал
аудита сохраняются), возвращает роли к исходным и пересевает данные от якорной даты (09:00 МСК);
затем чистит файлы удалённых строк в MinIO и ключи кэша `dash:*`, `wf:*`, `export:*`,
`stuck:guard:*`, пишет аудит `stand.demo_reset`. Для VPS — `make vm-demo-reset ANCHOR=…` после
`make vm-deploy`, для docker-compose — `make demo-reset-local`.

### 3.9 CronJob: «зависшие» заявки (красивая k8s-стори)

Решение кейсодержателя по Telegram-боту: настраиваемый порог 1–2 недели без смены статуса → эскалация ответственному и руководителю КАМа. Архитектура: **CronJob — это только триггер**; вся логика (каскад порогов из БД, выбор адресатов, двухступенчатая эскалация, идемпотентность повторных запусков) — в backend (workflow-engine.md §8), отправка — через notification-service.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: stuck-check
  labels: {app.kubernetes.io/name: stuck-check, role: job}
spec:
  schedule: "0 * * * *"          # ежечасно; порог измеряется днями — чаще незачем
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 120
      template:
        metadata:
          labels: {app.kubernetes.io/name: stuck-check, role: job}
        spec:
          restartPolicy: Never
          containers:
            - name: trigger
              image: curlimages/curl:8.10.1
              command:
                - sh
                - -c
                # --retry-connrefused: свежему поду Job сетевой контроллер применяет
                # allow-правила NetworkPolicy с задержкой ~секунды — первый connect
                # может получить reject, ждём и повторяем
                - >
                  curl -fsS -X POST
                  --retry 5 --retry-connrefused --retry-delay 3 --retry-max-time 90
                  -H "X-Internal-Token: $INTERNAL_API_TOKEN"
                  http://backend:8000/api/v1/internal/jobs/check-stuck-requests
              env:
                - name: INTERNAL_API_TOKEN
                  valueFrom: {secretKeyRef: {name: crm-internal, key: INTERNAL_API_TOKEN}}
```

Контракт endpoint-а: `POST /api/v1/internal/jobs/check-stuck-requests` → `200 {"checked": N, "stuck_found": M, "newly_stuck": K, "notifications_sent": …, "escalations_sent": …, "duration_ms": …}` (api-contract.md §11.1); повторный вызов не шлёт дубли (уникальный `notification.dedup_key` + KeyDB-guard). Паритет для docker-compose — `make stuck-check` (тот же curl на localhost:8000). На демо: `kubectl create job stuck-demo --from=cronjob/stuck-check -n crm` (или `make demo`) — эскалация прилетает в Telegram сразу (сид уже подготовил просроченные заявки, §3.8).

**Остальные CronJob** — тот же паттерн «триггер → internal endpoint»: логика в backend, CronJob только дёргает его с `X-Internal-Token`, `concurrencyPolicy: Forbid`, образ `curlimages/curl:8.10.1`, те же `--retry`:

| CronJob | Расписание | Endpoint | Что делает |
|---|---|---|---|
| `purge-deleted` | `30 3 * * *` (ночью) | `POST /api/v1/internal/jobs/purge-deleted` | «Право на забвение» (152-ФЗ): физически удаляет soft-deleted записи старше 30 дней вместе с файлами в MinIO (api-contract.md §11.2) |
| `idempotency-purge` | `15 * * * *` (ежечасно, со сдвигом от stuck-check) | `POST /api/v1/internal/jobs/purge-idempotency-keys` | Удаляет ключи `Idempotency-Key` старше TTL (24 ч) пачками по 5 000 с commit на пачку: сохранённые ответы мутаций содержат ПДн-шифртекст — дольше срока не храним (api-contract.md §1.6). Ответ — `{"purged": N, "duration_ms": …}` |

Паритет для docker-compose — `make idempotency-purge`.

### 3.10 HPA, ресурсы

`base/backend/hpa.yaml` — прямой ответ на «покажите план масштабирования на 300+ одновременных пользователей»:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: backend
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: backend
  minReplicas: 2                  # всегда >=2: и отказоустойчивость, и балансировка
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: {type: Utilization, averageUtilization: 70}
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies: [{type: Pods, value: 2, periodSeconds: 30}]
    scaleDown:
      stabilizationWindowSeconds: 120
```

Требует metrics-server: в k3s он встроен, в minikube — `minikube addons enable metrics-server` (зашито в `make k8s-up-minikube`).

Сводка ресурсов (влезает в кластер 4 CPU / 6 GiB — типовой ноутбук):

| Компонент | requests CPU/Mem | limits CPU/Mem |
|---|---|---|
| backend ×2..6 | 200m / 256Mi | 1 / 512Mi |
| frontend | 50m / 64Mi | 200m / 128Mi |
| notification | 50m / 128Mi | 300m / 256Mi |
| lms-stub, cms-stub (каждый) | 50m / 64Mi | 200m / 128Mi |
| postgres | 250m / 512Mi | 1 / 1Gi |
| keycloak | 500m / 768Mi | 1 / 1536Mi |
| minio | 100m / 256Mi | 500m / 512Mi |
| keydb | 50m / 128Mi | 300m / 256Mi |
| **Σ requests (backend ×2)** | **≈1.7 CPU / 2.5 GiB** | |

### 3.11 NetworkPolicy — закрытый контур

Требование кейсодержателя: закрытый контур, 152-ФЗ; notification-gateway с выходом в Telegram в целевой архитектуре выносится в DMZ (точка-точка). В прототипе мы **эмулируем контур средствами NetworkPolicy** в одном кластере — и показываем это на диаграмме как прямой ответ кейсодержателю.

```mermaid
flowchart LR
    subgraph internet["Интернет"]
        TG["Telegram Bot API"]
        MAX["Max / Exchange - каналы-заглушки"]
    end
    subgraph dmz["DMZ - в проде отдельный сегмент; в прототипе tier=dmz + NetworkPolicy"]
        NT["notification-gateway"]
    end
    subgraph closed["Закрытый контур - namespace crm, default-deny"]
        direction TB
        USR["Пользователи через VPN"] --> ING["Ingress"]
        ING --> FE["frontend"]
        ING --> BE["backend"]
        ING --> KC["keycloak"]
        BE <--> LMS["lms-stub"]
        BE <--> CMS["cms-stub"]
        BE --> PG[("postgres - ПДн зашифрованы")]
        BE --> S3[("minio")]
        BE --> KD[("keydb")]
        BE --> KC
        KC --> PG
    end
    BE -- "единственный канал точка-точка: POST /notify + ответные API-вызовы по ролям" --> NT
    NT -- "единственный egress наружу (443)" --> TG
    NT -.-> MAX
```

Ключевые тезисы:
1. **Default-deny на весь namespace** — любой трафик, не разрешённый явно, запрещён (включая egress в интернет: ядро CRM физически не может выйти наружу).
2. **Точка-точка**: backend разговаривает только с lms-stub, cms-stub, postgres, minio, keydb, keycloak и notification. Заглушки не видят БД. Frontend (nginx со статикой) вообще ни с кем не разговаривает.
3. **notification — прообраз DMZ**: единственный под с разрешённым egress в интернет; секрет бота видит только он; в целевой архитектуре переезжает в отдельный сегмент без изменения кода (общение с ядром уже только по HTTP точка-точка).
4. Runtime-выхода в интернет у ядра нет и по коду (обогащение из открытых источников — только разовая предзагрузка через админку — решение кейсодержателя), и по сети (NetworkPolicy) — двойной ответ.

Полные манифесты — два эталонных, остальные по матрице:

`networkpolicy/00-default-deny.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: default-deny-all}
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
```

`networkpolicy/40-notification-dmz.yaml` (единственный egress наружу):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: notification-dmz}
spec:
  podSelector:
    matchLabels: {app.kubernetes.io/name: notification}
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - podSelector: {matchLabels: {app.kubernetes.io/name: backend}}
      ports: [{port: 8010}]
  egress:
    - to:                                    # DNS
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
      ports: [{port: 53, protocol: UDP}, {port: 53, protocol: TCP}]
    - to:                                    # backend (ответные вызовы по ролям)
        - podSelector: {matchLabels: {app.kubernetes.io/name: backend}}
      ports: [{port: 8000}]
    - to:                                    # keycloak (client credentials)
        - podSelector: {matchLabels: {app.kubernetes.io/name: keycloak}}
      ports: [{port: 8080}]
    - to:                                    # Telegram Bot API: только 443 и только не в кластер
        - ipBlock:
            cidr: 0.0.0.0/0
            except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]
      ports: [{port: 443}]
```

Матрица остальных политик (каждая строка — отдельный манифест по образцу выше):

| Политика | podSelector | Ingress от | Egress к |
|---|---|---|---|
| `10-allow-dns` | все поды | — | kube-system:53/UDP,TCP |
| `20-allow-from-ingress` | frontend, backend, keycloak, lms-stub, cms-stub, minio | namespace ingress-контроллера (`ingress-nginx` / `kube-system` для traefik — задаётся в overlay) | — |
| `30-backend` | backend | ingress-контроллер; поды `role=job` (CronJob); notification; lms-stub, cms-stub (callbacks) | postgres:5432, minio:9000, keydb:6379, keycloak:8080, lms-stub:8020, cms-stub:8030, notification:8010 |
| `31-data-layer` | postgres / minio / keydb | postgres ← backend, keycloak, `role=job`; minio ← backend, `role=job`, ingress-контроллер (Console); keydb ← backend | — (egress не нужен) |
| `32-keycloak` | keycloak | ingress-контроллер; backend; notification; `role=job` | postgres:5432 |
| `35-jobs-stubs`: `jobs-egress` | поды `role=job` (Jobs и CronJob) | — | postgres:5432 (миграции, сид), minio:9000, keydb:6379, keycloak:8080, backend:8000 (триггеры stuck-check, purge-deleted, idempotency-purge) |
| `35-jobs-stubs`: `stubs-egress` | lms-stub, cms-stub | — | только backend:8000 (webhook в CRM); БД и интернет заглушкам не видны |
| `35-jobs-stubs`: `stubs-ingress` | lms-stub, cms-stub | backend на 8020/8030 (outbox-доставка: передача заявки в LMS, публикация программ в CMS) | — |

NetworkPolicy требует разрешения с обеих сторон: egress backend → заглушки (`30-backend`) без `stubs-ingress` отправлял бы outbox-доставку в ретраи и DLQ.

Overlay `vm` добавляет к матрице две точки: `allow-acme-solver` (поды-солверы cert-manager http01 сквозь default-deny) и egress notification до одного IP:порта HTTP-прокси для Telegram Bot API (`patches/netpol-notification-proxy.json`) — DMZ-принцип сохраняется: выход наружу по-прежнему только у notification.

**Enforcement по средам:** k3s (k3d) enforce'ит NetworkPolicy из коробки (встроенный kube-router контроллер); minikube с дефолтным CNI — **нет**, поэтому `make k8s-up-minikube` стартует кластер с `--cni=calico`. Это проверяемо на демо: `kubectl exec` в backend → `curl https://api.telegram.org` падает по timeout, из notification — проходит.

---

## 4. Overlays: minikube, k3d, vm

Base остаётся полностью рабочим и нейтральным; overlay меняет только окружение-специфичное. `minikube` и `k3d` — локальный стенд на ноутбуке, `vm` — публичный демо-стенд на голом k3s. Для передачи стенда наружу любой overlay подключает компонент `components/prod-hardening` (§4.4). `make k8s-validate` рендерит все три overlay и проверяет их kubeconform.

### 4.1 Сравнение

| Аспект | `overlays/minikube` | `overlays/k3d` | `overlays/vm` (публичный демо-стенд) |
|---|---|---|---|
| Ingress class | `nginx` (addon `ingress`) — патч не нужен, в base уже `nginx` | патч: `ingressClassName: traefik` (встроен в k3s) | `traefik` + принудительный редирект http→https |
| Хосты | `*.crm.local` через `/etc/hosts` (`make hosts`) | `*.crm.localhost` → 127.0.0.1 без `/etc/hosts`; патчи `KC_HOSTNAME`, `KEYCLOAK_ISSUER`, `config.js` → `id.crm.localhost` | `crm`, `id.crm`, `lms.crm`, `cms.crm` `.168-113-158-10.nip.io`; те же три патча → `https://id.crm…nip.io`; MinIO Console наружу не выставлена |
| TLS | нет | нет | Let's Encrypt через cert-manager (`vm-addons/`), отдельный `tls`-секрет на хост |
| StorageClass в PVC | `standard` (провижинер minikube) | `local-path` (встроенный local-path-provisioner) | `local-path` (k3s) |
| Доступ снаружи | Ingress через `minikube ip` (драйвер docker на macOS — через `minikube tunnel`, тогда IP = 127.0.0.1) | k3d serverlb пробрасывает 80/443 на localhost (`k3d cluster create -p "80:80@loadbalancer"`) — самый простой путь для проверяющего | публичный IP VPS, 80/443 |
| metrics-server (для HPA) | `minikube addons enable metrics-server` | встроен в k3s | встроен в k3s |
| NetworkPolicy enforcement | требуется `minikube start --cni=calico` | встроен (kube-router) | встроен (kube-router); + `allow-acme-solver`, egress notification до HTTP-прокси Bot API |
| Namespace ingress-контроллера в `20-allow-from-ingress` | `ingress-nginx` | `kube-system` (traefik) | `kube-system` (traefik) |
| Ресурсы | как в base | как в base | под скромную VM: backend requests 100m/128Mi, HPA 1→4 реплики; Keycloak requests 512Mi, limit 1Gi |
| Образы | `minikube image load crm/*:dev` | `k3d image import -c crm crm/*:dev` | `crm/*:vm` под linux/amd64: сборка на машине разработчика, `scp` и `k3s ctr images import` |
| Секреты | `make k8s-secrets` локально | `make k8s-secrets` локально | генерируются на самой VM (`gen-secrets.sh deploy/k8s/overlays/vm`) и не покидают её |

### 4.2 k3d и minikube

`overlays/k3d/kustomization.yaml` (minikube — симметрично, без патчей хостов):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: crm
resources:
  - ../../base
secretGenerator:
  - name: crm-db
    envs: [secrets/crm-db.env]
  - name: crm-pii
    envs: [secrets/crm-pii.env]
  - name: crm-s3
    envs: [secrets/crm-s3.env]
  - name: crm-keycloak-admin
    envs: [secrets/crm-keycloak-admin.env]
  - name: crm-keycloak-clients
    envs: [secrets/crm-keycloak-clients.env]
  - name: crm-telegram
    envs: [secrets/crm-telegram.env]
  - name: crm-internal
    envs: [secrets/crm-internal.env]
generatorOptions:
  disableNameSuffixHash: true    # имена секретов зашиты в envFrom подов
patches:
  - path: patches/ingress-class.yaml           # ingressClassName: traefik
  - path: patches/keycloak-hostname.yaml       # KC_HOSTNAME=http://id.crm.localhost
  - path: patches/app-config-localhost.yaml    # KEYCLOAK_ISSUER → id.crm.localhost
  - path: patches/frontend-env-localhost.yaml  # config.js: KEYCLOAK_URL → id.crm.localhost
  - target: {group: apps, version: v1, kind: StatefulSet, name: postgres}
    path: patches/storageclass.yaml            # local-path для PVC
  - target: {group: apps, version: v1, kind: StatefulSet, name: minio}
    path: patches/storageclass.yaml
  - target: {group: networking.k8s.io, version: v1, kind: NetworkPolicy, name: allow-from-ingress}
    path: patches/netpol-ingress-ns.yaml       # traefik живёт в kube-system
images:
  - name: crm/backend
    newTag: dev
  - name: crm/frontend
    newTag: dev
  - name: crm/notification
    newTag: dev
  - name: crm/lms-stub
    newTag: dev
  - name: crm/cms-stub
    newTag: dev
```

`secrets/*.env` — в `.gitignore`; рядом `secrets/*.env.example` с плейсхолдерами. `make k8s-secrets` копирует example → env и заменяет плейсхолдеры на сгенерированные значения (включая `PII_FERNET_KEYS`/`PII_HMAC_KEY`); повторный запуск ничего не перетирает — ключ ПДн стабилен между apply.

Рекомендованная команда создания кластера k3d (зашита в Makefile):

```sh
k3d cluster create crm --servers 1 --agents 1 \
  -p "80:80@loadbalancer" -p "443:443@loadbalancer" --wait
```

minikube:

```sh
minikube start -p crm --cpus=4 --memory=6g --cni=calico
minikube -p crm addons enable ingress
minikube -p crm addons enable metrics-server
```

### 4.3 vm — публичный демо-стенд на k3s

Демо-стенд, доступный проверяющим без VPN и `/etc/hosts`: голый k3s на VPS (Ubuntu x86_64), адреса — `https://crm.168-113-158-10.nip.io`, `id.crm…`, `lms.crm…`, `cms.crm…`. nip.io — публичный wildcard-DNS: имя `*.168-113-158-10.nip.io` резолвится в этот IP, поэтому своя DNS-зона стенду не нужна.

Порядок развёртывания:
1. `make vm-install` — идемпотентный bootstrap VPS: swap 2 ГБ, установка k3s, ожидание `node Ready`.
2. `make vm-deploy` (`deploy/scripts/vm-deploy.sh`, 7 шагов): сборка образов `linux/amd64` с тегом `:vm` (не пересекается с локальными arm64-образами `:dev`) → доставка и импорт в containerd k3s → `rsync deploy/` → генерация секретов overlay на VPS → cert-manager и ClusterIssuer `letsencrypt-prod` (ClusterIssuer применяется после готовности webhook cert-manager, с ретраями) → `kubectl apply -k deploy/k8s/overlays/vm` и ожидание готовности → smoke публичных URL.
3. `make vm-smoke` — только smoke: главная, `/api/v1/openapi.json`, OIDC-discovery Keycloak, UI заглушек.

Что меняет overlay `vm` относительно base:
- Ingress: только хосты nip.io (группы `*.localhost`/`*.crm.local` из base заменены целиком), аннотация `cert-manager.io/cluster-issuer: letsencrypt-prod`, `tls`-секрет на каждый хост, traefik Middleware `redirect-https`;
- Keycloak: `KC_HOSTNAME=https://id.crm…nip.io` (TLS терминирует traefik, `KC_PROXY_HEADERS=xforwarded`); `crm-config`: `KEYCLOAK_ISSUER`, `CRM_PUBLIC_URL`, `TELEGRAM_BOT_USERNAME`; `config.js`: `KEYCLOAK_URL`; redirect URI и web origins клиента `crm-frontend` в `realm-crm.json` включают хост nip.io;
- NetworkPolicy: `allow-acme-solver` для http01-челленджей сквозь default-deny; egress notification до HTTP-прокси Bot API;
- ресурсы и HPA — под скромную VM (§4.1).

**Ограничения демо-стенда.** Он нужен, чтобы систему можно было проверить из интернета, и в таком виде в прод не переносится:
- nip.io — внешний сервис: разрешение имён зависит от чужого DNS, а IP стенда виден в имени хоста;
- Let's Encrypt требует, чтобы хосты были доступны из интернета (http01) — в закрытом контуре это невозможно; лимит LE — 5 дубликатов сертификата в неделю на набор хостов;
- email владельца стенда в ClusterIssuer — для уведомлений LE об истечении;
- клиент `crm-loadtest` (Direct Access Grants) включён ради k6 и smoke.

### 4.4 Прод-контур: DNS, ingress, сертификаты

nip.io и Let's Encrypt используются **только для демо**. В контуре заказчика:

| Что | Демо-стенд (`vm`) | Прод |
|---|---|---|
| DNS | `*.<IP>.nip.io` (публичный wildcard-DNS) | корпоративный DNS: записи вида `crm.<домен>`, `id.crm.<домен>` во внутренней зоне, указывают на ingress-контроллер контура; заглушки LMS/CMS заменяются настоящими системами, их хосты в Ingress не нужны |
| Ingress | traefik k3s на публичном IP | ingress-контроллер кластера заказчика (nginx / traefik / корпоративный балансировщик перед ним); `ingressClassName` и namespace контроллера в `20-allow-from-ingress` — патчем overlay |
| TLS | Let's Encrypt, http01 через интернет | сертификат **внутреннего CA** заказчика: либо готовый secret `kubernetes.io/tls` в namespace `crm` (выпускает PKI заказчика), либо cert-manager с `ClusterIssuer` типа `ca` / Vault / ACME внутреннего CA — аннотация на Ingress та же, меняется имя issuer |
| Доверие к CA | публичные корни в браузерах | корневой сертификат внутреннего CA раздаётся на рабочие места групповыми политиками; поды, которые ходят во внешний HTTPS контура (Keycloak, LMS/CMS), монтируют bundle CA, если обращаются по внешнему имени |
| Секреты | генерируются на VM, `secretGenerator` из gitignored `*.env` | тот же `secretGenerator` или внешнее хранилище секретов заказчика (Vault, External Secrets) — имена Secret и ключи (§3.7) не меняются |
| Доступ | открыт из интернета | только из корпоративной сети / VPN; наружу — только notification-gateway в DMZ (§3.11) |

Как разворачивается прод: копия `overlays/vm` (или `overlays/k3d`) в `overlays/prod` с заменой пяти мест — хосты и `tls` в Ingress, `KC_HOSTNAME`, `KEYCLOAK_ISSUER`, `KEYCLOAK_URL` в `config.js`, redirect URI клиента `crm-frontend` в realm. Плюс:
- `components: [../../components/prod-hardening]` — убирает `crm-loadtest` из `KEYCLOAK_AUDIENCES` (backend перестаёт принимать токены нагрузочного клиента); сам клиент `crm-loadtest` отключается в Keycloak (`clients[crm-loadtest].enabled=false` в `realm-crm.json` + `make realm-sync` на свежей БД либо `kcadm.sh update clients/<uuid> -r crm -s enabled=false` на живом realm) — у него единственного включены Direct Access Grants;
- StorageClass, ресурсы и HPA — под кластер заказчика; образы — из корпоративного registry вместо импорта архивов;
- выход notification к Telegram — по правилам DMZ заказчика: напрямую на 443, через HTTP-прокси (`TELEGRAM_PROXY_URL`) или через свой сервер Bot API (`TELEGRAM_API_BASE`); в overlay `vm` секрет `crm-telegram` подключён к notification целиком (`envFrom`), поэтому эти ключи задаются в нём без правки манифестов.

Код backend и frontend, `base` и схема БД при этом не меняются: все адреса приходят из ConfigMap, `config.js` и realm.

---

## 5. docker-compose.dev.yml — локальная разработка

Назначение: быстрый inner loop (hot-reload backend, vite dev server на хосте), без Ingress и NetworkPolicy. Кластер — для стенда и демо, compose — для разработки. Файл: `deploy/compose/docker-compose.dev.yml`, env: `deploy/compose/.env` (генерируется `make dev-env` из `.env.example`).

```yaml
name: crm-dev

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: crm
      POSTGRES_USER: ${POSTGRES_USER:-crm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-crm}
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./initdb:/docker-entrypoint-initdb.d:ro     # 01-init.sql (БД keycloak)
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d crm"]
      interval: 5s
      timeout: 3s
      retries: 10

  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    # entrypoint-обёртка: KC 26 сам НЕ подставляет ${env.*} в realm при импорте
    entrypoint: ["/bin/bash", "/realm-src/import-realm.sh"]
    command: ["start-dev", "--import-realm", "--health-enabled=true"]
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
      KC_DB_USERNAME: ${POSTGRES_USER:-crm}
      KC_DB_PASSWORD: ${POSTGRES_PASSWORD:-crm}
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_ADMIN_PASSWORD:-admin}
      KC_HTTP_PORT: "8080"
      # подстановка ${env.*} в realm-crm.json при импорте
      NOTIFICATION_CLIENT_SECRET: ${NOTIFICATION_CLIENT_SECRET:-dev-notification-secret}
      INTEGRATION_CLIENT_SECRET: ${INTEGRATION_CLIENT_SECRET:-dev-integration-secret}
      LOADTEST_CLIENT_SECRET: ${LOADTEST_CLIENT_SECRET:-dev-loadtest-secret}
      BACKEND_ADMIN_CLIENT_SECRET: ${BACKEND_ADMIN_CLIENT_SECRET:-dev-backend-admin-secret}
    ports: ["8080:8080", "9001:9000"]   # 8080 — HTTP; mgmt-порт 9000 → хостовой 9001 (9000 хоста занят MinIO API)
    volumes:
      - ../keycloak/realm-crm.json:/realm-src/realm-crm.json:ro
      - ../keycloak/import-realm.sh:/realm-src/import-realm.sh:ro
    depends_on:
      postgres: {condition: service_healthy}
    healthcheck:
      test: ["CMD-SHELL", "exec 3<>/dev/tcp/127.0.0.1/9000 && echo -e 'GET /health/ready HTTP/1.1\\r\\nHost: localhost\\r\\n\\r\\n' >&3 && head -1 <&3 | grep -q 200"]
      interval: 10s
      timeout: 5s
      retries: 30

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9091"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-minioadmin}
    ports: ["9000:9000", "9091:9091"]
    volumes: [miniodata:/data]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      retries: 10

  minio-init:
    image: minio/mc
    depends_on:
      minio: {condition: service_healthy}
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 $${MINIO_ROOT_USER:-minioadmin} $${MINIO_ROOT_PASSWORD:-minioadmin} &&
      mc mb -p local/crm-files local/crm-imports local/crm-exports"

  keydb:
    image: eqalpha/keydb:latest
    command: ["keydb-server", "--appendonly", "no", "--maxmemory", "192mb", "--maxmemory-policy", "allkeys-lru"]
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "keydb-cli", "ping"]
      interval: 5s
      retries: 10

  backend:
    build: {context: ../../backend, target: builder}   # dev-стадия с uv и dev-зависимостями
    command: ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
    environment:
      APP_ENV: dev
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-crm}:${POSTGRES_PASSWORD:-crm}@postgres:5432/crm
      KEYDB_URL: redis://keydb:6379/0
      S3_ENDPOINT: http://minio:9000
      S3_ACCESS_KEY: ${MINIO_ROOT_USER:-minioadmin}
      S3_SECRET_KEY: ${MINIO_ROOT_PASSWORD:-minioadmin}
      S3_BUCKET_FILES: crm-files
      S3_BUCKET_IMPORTS: crm-imports
      S3_BUCKET_EXPORTS: crm-exports
      KEYCLOAK_INTERNAL_URL: http://keycloak:8080
      KEYCLOAK_ISSUER: http://localhost:8080/realms/crm   # в dev браузер и backend видят KC на localhost:8080
      KEYCLOAK_REALM: crm
      LMS_BASE_URL: http://lms-stub:8020
      CMS_BASE_URL: http://cms-stub:8030
      NOTIFICATION_BASE_URL: http://notification:8010
      PII_FERNET_KEYS: ${PII_FERNET_KEYS:?run make dev-env}
      PII_HMAC_KEY: ${PII_HMAC_KEY:?run make dev-env}
      INTERNAL_API_TOKEN: ${INTERNAL_API_TOKEN:-dev-internal-token}
      LMS_WEBHOOK_SECRET: ${LMS_WEBHOOK_SECRET:-dev-lms-secret}
      CMS_WEBHOOK_SECRET: ${CMS_WEBHOOK_SECRET:-dev-cms-secret}
      STUCK_DEFAULT_DAYS: "14"
    volumes: [../../backend:/app]        # hot-reload
    ports: ["8000:8000"]
    depends_on:
      postgres: {condition: service_healthy}
      keydb: {condition: service_healthy}
      minio: {condition: service_healthy}
      keycloak: {condition: service_healthy}

  notification:
    build: {context: ../../services/notification, target: builder}
    command: ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--reload"]
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}      # пусто => канал TG в режиме console-заглушки
      KEYCLOAK_INTERNAL_URL: http://keycloak:8080
      KEYCLOAK_REALM: crm
      KC_CLIENT_ID: crm-notification
      KC_CLIENT_SECRET: ${NOTIFICATION_CLIENT_SECRET:-dev-secret}
      BACKEND_BASE_URL: http://backend:8000
      INTERNAL_API_TOKEN: ${INTERNAL_API_TOKEN:-dev-internal-token}
    volumes: [../../services/notification:/app]
    ports: ["8010:8010"]
    depends_on:
      keycloak: {condition: service_healthy}

  lms-stub:
    build: {context: ../../services/lms-stub}
    environment:
      CRM_WEBHOOK_URL: http://backend:8000/api/v1/integrations/lms/webhook
      LMS_WEBHOOK_SECRET: ${LMS_WEBHOOK_SECRET:-dev-lms-secret}
    ports: ["8020:8020"]

  cms-stub:
    build: {context: ../../services/cms-stub}
    environment:
      CRM_WEBHOOK_URL: http://backend:8000/api/v1/integrations/cms/webhook
      CMS_WEBHOOK_SECRET: ${CMS_WEBHOOK_SECRET:-dev-cms-secret}
    ports: ["8030:8030"]

  # полный фронт в контейнере — по требованию: docker compose --profile full up
  frontend:
    profiles: [full]
    build: {context: ../../frontend}
    ports: ["80:8080"]

volumes:
  pgdata:
  miniodata:
```

Фронтенд в dev-режиме запускается на хосте: `npm run dev` (vite, порт 5173) с прокси в `vite.config.ts`:

```ts
server: { proxy: { "/api": "http://localhost:8000" } }
```

Realm один на все среды — `deploy/keycloak/realm-crm.json`: в нём сразу перечислены redirect URI всех стендов (`localhost:5173`, `localhost`, `crm.localhost`, `crm.local`, хост VPS), а client secrets записаны плейсхолдерами `${env.*}`. Их подставляет из окружения контейнера обёртка `deploy/keycloak/import-realm.sh` (Keycloak 26 сам этого при импорте не делает) — и в compose, и в k8s; ConfigMap `keycloak-realm` пересобирается из того же файла командой `make realm-sync` — расхождения исключены.

---

## 6. Makefile

Цели верхнего уровня (полный файл — в корне репо, `make help` печатает список):

| Группа | Цель | Что делает |
|---|---|---|
| Разработка | `dev` | compose-стек (инфра + backend/notification/заглушки) + подсказка про `npm run dev` |
| | `dev-env` | сгенерировать `deploy/compose/.env`, если его нет (PII-ключи и пр., `deploy/scripts/gen-dotenv.sh`) |
| | `dev-down` | остановить compose-стек |
| Образы | `build-images` | собрать `crm/backend`, `crm/frontend`, `crm/notification`, `crm/lms-stub`, `crm/cms-stub` с тегом `:dev` |
| Kubernetes | `realm-sync` | регенерировать ConfigMap `keycloak-realm` из `deploy/keycloak/realm-crm.json` |
| | `k8s-secrets` | сгенерировать gitignored-секреты overlay (`OVERLAY=…`); повторный запуск не перетирает |
| | `k8s-up` | k3d: кластер + импорт образов + `k8s-apply` + подсказка по хостам |
| | `k8s-up-minikube` | minikube с `--cni=calico`, addons ingress и metrics-server, загрузка образов, `k8s-apply` |
| | `k8s-apply` | идемпотентный apply: Jobs `db-migrate`, `seed-demo`, `minio-init` пересоздаются, затем `kubectl wait` по контрольным точкам §7 |
| | `k8s-validate` | рендер overlay k3d, minikube и vm + kubeconform `-strict` |
| | `k8s-down` | удалить кластер (k3d или minikube) |
| Публичный VPS | `vm-install` | bootstrap VPS: swap, k3s, ожидание node Ready (идемпотентен) |
| | `vm-deploy` | amd64-образы → импорт на VPS, `rsync deploy/`, секреты, cert-manager, apply overlay `vm`, smoke (§4.3) |
| | `vm-smoke` | только smoke-проверки публичных URL |
| Данные и демо | `seed` | перезапустить Job `seed-demo` (сид идемпотентен) |
| | `demo` | `seed` + ручной запуск `stuck-check` + список адресов стенда |
| | `demo-reset` | сброс стенда к показу (`ANCHOR=ГГГГ-ММ-ДД`): `stuck-check` на паузу, Job из шаблона `cronjob/demo-reset`, пересев одной транзакцией (§3.8); `demo-reset-local` — то же для compose, `vm-demo-reset` — для VPS |
| | `hosts` | строка для `/etc/hosts` — нужна только для алиасов `*.crm.local` (curl/API, minikube) |
| | `stuck-check` | compose-паритет CronJob: проверка зависших заявок |
| | `idempotency-purge` | compose-паритет CronJob: очистка просроченных ключей Idempotency-Key |
| | `demo-idempotency` | демонстрация Idempotency-Key против стенда (`deploy/scripts/demo-idempotency.sh`, нужен `DEMO_PASSWORD`) |
| Замеры | `bench-migration` | замер массового переноса заявок на одноразовой БД `crm_bench` (`BENCH_DB`, `BENCH_ARGS`): создаёт базу, если её нет, доводит до head и запускает `python -m app.tools.bench_migration`; база с другим именем — отказ (workflow-engine.md §6.8) |
| | `load-test` | k6: 300→400 VU против стенда (`deploy/load/k6-script.js`; docker-вариант — `deploy/load/README.md`) |
| | `load-test-users` / `load-test-reports` / `load-test-nft` | профили НФТ: 50 пользователей под разными учётками, 10 параллельных отчётов с выгрузками, оба сразу (§8.2) |
| Служебное | `logs` | логи backend в кластере |
| | `clean` | снести compose-стек (с томами) и кластер; сгенерированные секреты `*.env` не трогает |

`k8s-apply` — ядро всех k8s-целей:

```makefile
k8s-apply: ## идемпотентный apply: Jobs пересоздаются (Alembic идемпотентен)
	kubectl delete job db-migrate seed-demo minio-init -n $(NS) --ignore-not-found
	kubectl apply -k $(OVERLAY)
	kubectl -n $(NS) wait --for=condition=ready pod -l app.kubernetes.io/name=postgres --timeout=180s
	kubectl -n $(NS) wait --for=condition=complete job/db-migrate --timeout=300s
	kubectl -n $(NS) wait --for=condition=complete job/minio-init --timeout=180s
	kubectl -n $(NS) wait --for=condition=available deploy/keycloak --timeout=600s
	kubectl -n $(NS) wait --for=condition=available deploy/backend --timeout=300s
	@echo "Стенд готов: http://crm.localhost (k3d, без /etc/hosts); minikube — http://crm.local (make hosts)"
```

Примечания:
- `gen-secrets.sh` создаёт `secrets/*.env` из `*.env.example` (только отсутствующие; повторный запуск ничего не перетирает — ключ ПДн стабилен между apply);
- отдельного `make k8s-migrate` не нужно: перезапуск миграций = `make k8s-apply` (Jobs пересоздаются всегда, Alembic идемпотентен);
- `bench-migration` и тесты с БД очищают таблицы, поэтому проверяют имя базы: инструмент замера принимает только `crm_test*`, `*_test`, `*_bench` (иначе нужен `--force`), фикстуры `tests/db` — только `crm_test*` и `crm*_test`.

---

## 7. Порядок старта и готовность

В k8s нет «порядка запуска» — есть сходимость: всё применяется одним `kubectl apply -k`, зависимости выражены initContainers, probes и Job-ами. Фактическая последовательность сходимости:

```mermaid
flowchart TD
    A["postgres, minio, keydb стартуют параллельно"] --> B["Job minio-init: бакеты + app-ключ S3"]
    A --> C["keycloak: initContainer wait-postgres → start --import-realm\n(realm, роли, пользователи, клиенты)"]
    A --> D["Job db-migrate: alembic upgrade head"]
    D --> E["backend x2: wait-postgres + wait-migrations → readiness OK\n(readiness также требует PG/KeyDB/MinIO/JWKS)"]
    C --> E
    B --> E
    E --> F["Job seed-demo: демо-данные (идемпотентен)"]
    C --> G["notification: wait-keycloak → бот запущен"]
    A2["frontend, lms-stub, cms-stub — без зависимостей"] --> H["Ingress маршрутизирует, readiness-гейты снимаются"]
    E --> H
    F --> I["CronJob: stuck-check и idempotency-purge — ежечасно, purge-deleted — ночью"]
    G --> H
```

Контрольные точки готовности (их же выполняет `make k8s-apply` через `kubectl wait`):

1. `pod/postgres-0 Ready` → 2. `job/db-migrate Complete` и `job/minio-init Complete` → 3. `deploy/keycloak Available` (до 5 минут: JVM + импорт realm) → 4. `deploy/backend Available` → 5. `job/seed-demo Complete` → стенд готов.

Устойчивость к рестартам: любой под можно убить (`kubectl delete pod`) — самовосстановление без ручных действий; это готовый демо-сценарий: HPA + второй backend-под не дают ни одной ошибки во фронте.

---

## 8. Нагрузочное тестирование и план масштабирования на 300+

Решение кейсодержателя: «свой отчёт нагрузочного тестирования (Postman и т.п.) достаточен; показать план масштабирования на 300+ одновременных пользователей». Мы делаем строже, чем Postman. Нефункциональные требования ТЗ к числу параллельных пользователей и отчётов закрываются отдельными профилями того же сценария (§8.2, «Профили НФТ»).

### 8.1 Инструмент — k6 (locust — запасной)

**k6**: один бинарь без рантайм-зависимостей, сценарии как код (JS в репо), пороги (`thresholds`) — машинная проверка SLA прямо в прогоне, готовые сводки p95/p99. **locust** оставляем как запасной вариант (если понадобится живой web-UI), но базовый отчёт делаем на k6: его thresholds превращают «отчёт о нагрузочном» в воспроизводимый артефакт CI, а не скриншот.

### 8.2 Сценарий `deploy/load/k6-script.js`

Профиль моделирует именно **300+ одновременных пользователей** (не RPS): виртуальный пользователь = КАМ с think-time. Ниже — ядро сценария; в файле ещё режим `SMOKE=1` (10 VU / 30 с) и аккуратная запись: переход только по рёбрам, которые движок сам объявил доступными, с комментарием и актуальной `version`.

```js
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://crm.local";
const KC   = __ENV.KC_URL   || "http://id.crm.local";

export const options = {
  scenarios: {
    steady: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "2m", target: 300 },   // разгон до 300 пользователей
        { duration: "5m", target: 300 },   // полка: основное окно измерений
        { duration: "1m", target: 400 },   // «300+»: запас прочности
        { duration: "1m", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],                  // <1% ошибок
    http_req_duration: ["p(95)<500", "p(99)<1000"],  // SLA API, мс
  },
};

export function setup() {
  // токен под нагрузочным confidential-клиентом (Direct Access Grants включён только у него)
  const res = http.post(`${KC}/realms/crm/protocol/openid-connect/token`, {
    grant_type: "password",
    client_id: "crm-loadtest",
    client_secret: __ENV.LOADTEST_CLIENT_SECRET,
    username: "kam1",
    password: __ENV.LOADTEST_USER_PASSWORD,
  });
  const token = res.json("access_token");
  const h = { headers: { Authorization: `Bearer ${token}` } };
  // id заявок — UUID, поэтому берём реальный список из API (сид гарантирует >= 50 заявок)
  const ids = http.get(`${BASE}/api/v1/requests?limit=50`, h).json("items").map((i) => i.id);
  return { token, ids };
}

export default function (data) {
  const h = { headers: { Authorization: `Bearer ${data.token}` } };
  const id = data.ids[__VU % data.ids.length];

  // 60% — списки/дашборды (чтение, кэшируется KeyDB)
  let r = http.get(`${BASE}/api/v1/requests?limit=20`, h);
  check(r, { "list 200": (x) => x.status === 200 });
  sleep(1 + Math.random() * 2);

  // 30% — карточка заявки
  r = http.get(`${BASE}/api/v1/requests/${id}`, h);
  check(r, { "card 200": (x) => x.status === 200 });
  sleep(1 + Math.random() * 2);

  // 10% — запись: переход по workflow (валидируется движком)
  if (__VU % 10 === 0) {
    const tr = http.get(`${BASE}/api/v1/requests/${id}/transitions`, h).json("items");
    if (tr && tr.length) {
      const card = r.json();
      r = http.post(`${BASE}/api/v1/requests/${id}/transitions`,
        JSON.stringify({ to_status_id: tr[0].to_status_id, comment: "load-test", version: card.version }),
        { headers: { ...h.headers, "Content-Type": "application/json" } });
      check(r, { "transition ok": (x) => x.status === 200 || x.status === 409 }); // 409 = валидный отказ движка/optimistic lock
    }
  }
  sleep(5 + Math.random() * 10);   // think time: пользователь, а не пушка
}
```

Токен получается один раз в `setup()` через выделенный клиент `crm-loadtest` (Direct Access Grants включён **только** у него — остальные клиенты realm его не разрешают); сид гарантирует существование заявок 1..50 и пользователя `kam1`.

**Профили НФТ.** Тот же скрипт содержит профили под нефункциональные требования ТЗ (выбор —
переменной окружения, запуск — make-целями; методика — `deploy/load/README.md`, раздел «Методика
НФТ», и `docs/evidence/load-report.md` §8):

| Профиль | Нагрузка | Пороги в скрипте |
|---|---|---|
| `users50` (`make load-test-users`) | 50 VU под 5 учётками разных ролей, путь КАМа, 5 минут | ошибки < 1 %, экраны p95 < 500 мс и p99 < 1 с |
| `reports` (`make load-test-reports`) | 10 VU строят разные отчёты (10 кодов) со случайными периодом и фильтрами на холодном кэше; каждая третья итерация — выгрузка XLSX/PDF со скачиванием | отчёт p95 < 1 с, выгрузка p95 < 3 с |
| `nft` (`make load-test-nft`) | оба профиля одновременно | все пороги выше |

Перед прогоном и после него стенд сбрасывается к эталону (`make demo-reset`, §3.8). Прогона
профилей НФТ ещё не было — результаты добавляются в `load-report.md` по факту.

### 8.3 Что измеряем

| Метрика | Источник | Целевое значение |
|---|---|---|
| p95 / p99 латентность API | k6 summary | p95 < 500 мс, p99 < 1000 мс |
| Ошибки | k6 `http_req_failed` | < 1% |
| Пропускная способность | k6 `http_reqs` | справочно (~40–60 RPS при 300 VU с think-time) |
| Реплики backend во времени | `kubectl get hpa backend -n crm -w` (лог в отчёт) | 2 → 4..6 на полке, обратно 2 после спада |
| CPU/Mem подов | `kubectl top pods -n crm` (metrics-server) | внутри limits |
| Соединения PG | `pg_stat_activity` | < 80% max_connections |

Артефакты: `docs/evidence/load-report.md` (методика, результаты прогона, вывод HPA; сырые данные `load-raw.json` и `load-summary.json` сохраняются локально и в git не попадают) — «отчёт нагрузочного тестирования» по требованию кейсодержателя. Замер массового переноса заявок при изменении схемы — отдельно, `docs/evidence/migration-benchmark.md` (workflow-engine.md §6.8).

### 8.4 Расчёт ёмкости (план масштабирования)

- 300 одновременных пользователей × think-time 10–15 с × ~2 запроса на действие ⇒ **~40–60 RPS** пиково.
- Backend IO-bound (async SQLAlchemy, кэш KeyDB на горячих чтениях): один uvicorn-воркер держит 150–200 RPS на этом профиле; под (2 воркера) ≈ 300 RPS.
- Базовые **2 реплики = ~600 RPS** — десятикратный запас; HPA до 6 реплик закрывает и 400 VU, и деградацию кэша.
- Соединения к PG (исправлено после прогона 16.09, где пулы упёрлись в лимит СУБД): backend (maxReplicas 6 + maxSurge 2) × 2 воркера × (`DB_POOL_SIZE` 4 + `DB_MAX_OVERFLOW` 2) = 96, Keycloak — 10 (`KC_DB_POOL_MAX_SIZE`), Jobs — 9, резерв суперпользователя — 3; итого **118 < `max_connections=150`** (аргумент StatefulSet postgres). Бюджет сверяет `backend/tests/test_connection_budget.py` по тем же манифестам; на VPS (HPA max 4) — 82. При дальнейшем росте — PgBouncer перед PG, в прототипе не нужен.
- Statefulness: приложение stateless (сессии — в JWT, файлы — в MinIO, кэш — в KeyDB) ⇒ горизонтальное масштабирование без sticky sessions; балансировка — штатный Service/Ingress.
- Прод-рост дальше 6 реплик: реплика чтения PG + вынос reporting-модуля первым (границы модулей уже готовы, §1).

Это дословно закрывает формулировку кейсодержателя «реплики сервисов / второй инстанс монолита + балансировка» — но в живом кластере, а не на слайде.

---

## 9. Матрица соответствия решениям кейсодержателя (Q&A 16.09.2026)

Деплой-релевантные пункты — где закрыты в этом документе; остальные — ссылка на профильный документ.

| Решение кейсодержателя | Где закрыто |
|---|---|
| Keycloak в своей инфраструктуре, RBAC, пользователи по ролям в README | §3.4 keycloak (self-hosted, import realm с пользователями), §3.6 (`id.crm.localhost`); роли и права — в БД Альмы, Keycloak — только вход (ux.md §3.1); каталог прав засевает миграция Alembic, поэтому отдельного шага деплоя не нужно |
| Закрытый контур; безопасность библиотек | §3.11 NetworkPolicy default-deny + диаграмма; пины версий образов; `uv.lock`/`npm ci` — фиксация зависимостей, сканирование уязвимостей в CI не подключено (план) |
| 152-ФЗ: шифрование/псевдонимизация ПДн студентов | §3.7 Secret `crm-pii` (ключ только у backend, генерация при установке, ротация `rekey`); сама схема шифрования — data-model.md |
| PostgreSQL + MinIO (кейсодержатель одобрил) | §3.4 StatefulSet-ы с PVC; бакеты §3.8 |
| KeyDB как кэш | §3.4 keydb (Deployment, LRU 192mb, без персистентности — осознанно) |
| Монолит или микросервисы; «для микросервисов простого compose мало — нужен minikube/k3s» | §1 обоснование; мы даём **и** minikube, **и** k3d(k3s), **и** публичный стенд на k3s — overlay §4 |
| Нагрузочное + план масштабирования 300+ | §8 целиком: k6, HPA §3.10, расчёт ёмкости §8.4 |
| Telegram-бот; notification-gateway в DMZ (закрытый контур) | §3.11 (единственный egress + Mermaid-диаграмма DMZ), §3.3 notification, Secret `crm-telegram` только у него |
| «Зависшие» заявки, настраиваемый порог, эскалация | §3.9 CronJob (триггер) + `STUCK_DEFAULT_DAYS` в ConfigMap + каскад порогов в БД; демо-фактура в сиде §3.8 |
| Изменение схемы «на лету», работа не встаёт | backend: пакетный перенос, ограниченное ожидание локов, сводные уведомления (workflow-engine.md §6.3–6.8); замер — `make bench-migration` (§6) |
| Двусторонние интеграции LMS/CMS на заглушках, проверяемые | §1 (stubs — отдельные процессы), §3.6 (`lms`/`cms.crm.localhost`, на VPS — `lms`/`cms.crm…nip.io` — UI журнала вызовов), NetworkPolicy точка-точка §3.11 (`stubs-ingress`/`stubs-egress`) |
| Обогащение из открытых источников — только разовая предзагрузка, без runtime-интернета | §3.11: у ядра нет egress наружу по сети — не только по коду |
| Решение развёрнуто и рабочее; доступ проверяющим; README с пользователями | публичный демо-стенд §4.3 (`make vm-deploy`, HTTPS без VPN и `/etc/hosts`), локально — §6 `make k8s-up` + `make demo`; realm-пользователи §3.4; публичный README без приватных VPN-конфигов; перенос в контур заказчика — §4.4 |
| Обоснование стека при отходе от Kotlin/Python/Node/React | стек — Python/React (внутри списка); инфраструктурные выборы обоснованы по месту (§1, §3.4, §8.1) |
| Мобильного приложения нет — адаптивный веб-интерфейс от 375 px | не деплой-вопрос; Ingress ничего мобильно-специфичного не требует |
| Импорт XLSX/XLS больших списков | §3.6 `proxy-body-size: 50m`; `/tmp` в подах backend §3.3 |

---

## 10. Открытые вопросы / риски

1. ~~Контур для публичного стенда~~ — **решено**: overlay `vm` на голом k3s публичного VPS, хосты nip.io, TLS Let's Encrypt (§4.3). Это демо-конфигурация; для контура заказчика — корпоративный DNS, его ingress-контроллер и сертификат внутреннего CA (§4.4). Managed k8s — тот же base, свой overlay (storage class и ingress class провайдера, images через registry).
2. **Telegram из закрытого контура**: если egress наружу закрыт совсем — notification переключается в режим console/webhook-заглушки (пустой `TELEGRAM_BOT_TOKEN` уже поддержан, §5). Если Bot API недоступен напрямую — через HTTP-прокси (`TELEGRAM_PROXY_URL`) или свой сервер Bot API (`TELEGRAM_API_BASE`) с точечным egress notification только до него — так сделано на VPS (§4.3).
3. **`crm.local` и minikube на macOS**: драйвер docker требует `minikube tunnel`; локальный k3d-путь основной именно поэтому (127.0.0.1 из коробки), а вход в UI — через `*.localhost` (secure context для PKCE, §3.6).
4. ~~Пины образов~~ — **решено**: `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z`, `quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z`, `eqalpha/keydb` v6.3.4 по digest, `curlimages/curl:8.10.1`, `postgres:16-alpine`, `quay.io/keycloak/keycloak:26.0`.
5. ~~Итоговый список ролей realm~~ — **решено**: в realm — `admin`, `head_kam`, `kam`, `observer` (стартовое назначение при первом входе) + сервисные `svc_notification`, `svc_integration`; дальше роли людей живут в БД Альмы и настраиваются в интерфейсе. Realm правится в одном месте — `deploy/keycloak/realm-crm.json`, ConfigMap пересобирает `make realm-sync`.
6. **Аварийный доступ к ролям.** Если в Альме не осталось пользователя с правом управления ролями (интерфейс и сервер это предотвращают, но БД могли править руками) — `kubectl exec deploy/backend -- python -m app.tools.rbac grant-admin <username>`, действие пишется в аудит.
