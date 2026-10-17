# CRM «Вузы» — ЛЦТ 2026. Цели верхнего уровня (подробности: docs/design/deployment.md).
# Локальная разработка — make dev (compose); стенд/демо — make k8s-up (k3d) или k8s-up-minikube.

CLUSTER      ?= crm
NS           ?= crm
IMAGES        = crm/backend crm/frontend crm/notification crm/llm-gateway crm/lms-stub crm/cms-stub
K3D_OVERLAY   = deploy/k8s/overlays/k3d
MINI_OVERLAY  = deploy/k8s/overlays/minikube
VM_OVERLAY    = deploy/k8s/overlays/vm
OVERLAY      ?= $(K3D_OVERLAY)
COMPOSE       = docker compose -f deploy/compose/docker-compose.dev.yml
# Сводки ИИ: ключ GigaChat (gitignored) — секрет оверлея k3d; LLM_SRC — файл с GIGACHAT_*
LLM_ENV      ?= $(K3D_OVERLAY)/secrets/crm-llm.env
LLM_SRC      ?=
LLM_CA        = deploy/k8s/base/llm-gateway/certs/russian_trusted_bundle.pem
# uv: из PATH, иначе из venv backend (там он ставится зависимостью)
UV           ?= $(or $(shell command -v uv 2>/dev/null),$(wildcard $(CURDIR)/backend/.venv/bin/uv),uv)
# Публичный VPS (голый k3s): деплой — deploy/scripts/vm-*.sh
VM_IP        ?= 168.113.158.10
VM_HOST      ?= root@$(VM_IP)
# Замер массового переноса заявок (bench-migration): одноразовая БД на локальном PostgreSQL
BENCH_PG     ?= postgresql://crm:crm@localhost:5432
BENCH_DB     ?= crm_bench
BENCH_ARGS   ?=
BENCH_URL     = $(subst postgresql://,postgresql+asyncpg://,$(BENCH_PG))/$(BENCH_DB)
# интерпретатор backend (путь — относительно backend/): venv, если он есть
BACKEND_PY   ?= $(if $(wildcard backend/.venv/bin/python),.venv/bin/python,python3)
# make check: база интеграционных тестов backend (tests/db пересоздаёт её схему — crm_test*/*_test)
# и Python окружений сервисов — как python-version в CI (код backend требует 3.12)
ALMA_TEST_DB ?= crm_test
CHECK_PYTHON ?= 3.12
# «сейчас» демо-данных для demo-reset: пусто — момент запуска; ГГГГ-ММ-ДД — 09:00 МСК дня
ANCHOR       ?=
# k6: секрет клиента crm-loadtest (если не задан — из секрета кластера) и прогон сценария
K6_SECRET     = LOADTEST_CLIENT_SECRET=$${LOADTEST_CLIENT_SECRET:-$$(kubectl -n $(NS) get secret crm-keycloak-clients -o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d)}
K6_CHECK      = command -v k6 >/dev/null 2>&1 || \
	  { echo "k6 не установлен — запуск в docker описан в deploy/load/README.md"; exit 1; }

.PHONY: help dev dev-env dev-down build-images realm-sync k8s-secrets k8s-up \
        k8s-up-minikube k8s-apply k8s-validate k8s-down seed demo demo-reset \
        demo-reset-local hosts stuck-check idempotency-purge demo-idempotency \
        bench-migration docs-arch load-test load-test-users load-test-reports load-test-nft logs \
        clean vm-install vm-deploy vm-smoke vm-demo-reset llm-secret llm-smoke llm-smoke-k8s \
        check check-python check-frontend check-backend check-notification check-llm-gateway \
        check-lms-stub check-cms-stub

help: ## список целей
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## --- Локальная разработка ---------------------------------------------------
dev: dev-env ## compose-стек (инфра + backend/notification/llm-gateway/заглушки) + подсказка про фронт
	$(COMPOSE) up -d --build
	@echo "API:      http://localhost:8000/docs"
	@echo "Keycloak: http://localhost:8080  (admin / см. deploy/compose/.env)"
	@echo "Фронт:    cd frontend && npm run dev   → http://localhost:5173"

dev-env: ## сгенерировать deploy/compose/.env, если его нет (PII-ключи и пр.)
	./deploy/scripts/gen-dotenv.sh

dev-down: ## остановить compose-стек
	$(COMPOSE) down

## --- Проверки перед коммитом (повторяют CI: .github/workflows/ci.yml) ----------
# Python — шаги «Тесты» и «Линтер» матрицы python-services с её флагами uv; uv run доводит
# окружение сервиса до uv.lock. tests/db backend — на PostgreSQL localhost:5432 (crm/crm,
# база ALMA_TEST_DB): без базы — ошибка, а не пропуск. Фронт — шаги job frontend после
# `npm ci` (здесь не запускается: пересоздаёт node_modules). Цель на сервис — как job CI:
# внутри стоп на первой ошибке; make -k check проверит все сервисы (fail-fast: false).
define py_check
	cd $(1) && $(2) $(UV) run --python $(CHECK_PYTHON) $(3) pytest -q
	cd $(1) && $(UV) run --python $(CHECK_PYTHON) $(3) ruff check app tests
endef

check: check-python check-frontend ## все проверки CI: ruff и pytest 5 сервисов, tsc, ESLint, юнит-тесты, справка, сборка

check-python: check-backend check-notification check-llm-gateway check-lms-stub check-cms-stub ## ruff и pytest всех Python-сервисов

check-backend: ## pytest (tests/db на PostgreSQL обязательны) и ruff backend
	$(call py_check,backend,ALMA_DB_TESTS=required ALMA_TEST_DB=$(ALMA_TEST_DB),--extra dev)

check-notification:
	$(call py_check,services/notification,,--group dev)

check-llm-gateway:
	$(call py_check,services/llm-gateway,,--group dev)

check-lms-stub:
	$(call py_check,services/lms-stub,,--extra dev)

check-cms-stub:
	$(call py_check,services/cms-stub,,--extra dev)

check-frontend: ## фронт как в CI: tsc, ESLint (хуки React), юнит-тесты, справка, сборка
	cd frontend && npm run typecheck
	cd frontend && npm run lint
	cd frontend && npm run test:unit
	cd frontend && npm run docs:help
	cd frontend && npm run build

## --- Сборка образов ----------------------------------------------------------
build-images: ## собрать все образы crm/*:dev
	docker build -t crm/backend:dev backend
	docker build -t crm/frontend:dev frontend
	docker build -t crm/notification:dev services/notification
	docker build -t crm/llm-gateway:dev services/llm-gateway
	docker build -t crm/lms-stub:dev services/lms-stub
	docker build -t crm/cms-stub:dev services/cms-stub

## --- Kubernetes ---------------------------------------------------------------
realm-sync: ## регенерировать ConfigMap keycloak-realm из deploy/keycloak/realm-crm.json
	./deploy/scripts/sync-realm-configmap.sh

k8s-secrets: ## сгенерировать gitignored-секреты overlay-я (повторный запуск не перетирает)
	./deploy/scripts/gen-secrets.sh $(OVERLAY)

k8s-up: OVERLAY=$(K3D_OVERLAY)
k8s-up: build-images k8s-secrets ## k3d: кластер + импорт образов + манифесты + ожидание
	k3d cluster list $(CLUSTER) >/dev/null 2>&1 || \
	  k3d cluster create $(CLUSTER) --servers 1 --agents 1 \
	    -p "80:80@loadbalancer" -p "443:443@loadbalancer" --wait
	k3d image import -c $(CLUSTER) $(addsuffix :dev,$(IMAGES))
	$(MAKE) k8s-apply OVERLAY=$(K3D_OVERLAY)
	$(MAKE) hosts

k8s-up-minikube: OVERLAY=$(MINI_OVERLAY)
k8s-up-minikube: build-images k8s-secrets ## minikube (--cni=calico: enforcement NetworkPolicy)
	minikube status -p $(CLUSTER) >/dev/null 2>&1 || \
	  minikube start -p $(CLUSTER) --cpus=4 --memory=6g --cni=calico
	minikube -p $(CLUSTER) addons enable ingress
	minikube -p $(CLUSTER) addons enable metrics-server
	for i in $(IMAGES); do minikube -p $(CLUSTER) image load $$i:dev; done
	$(MAKE) k8s-apply OVERLAY=$(MINI_OVERLAY)
	$(MAKE) hosts

k8s-apply: ## идемпотентный apply: Jobs пересоздаются (Alembic идемпотентен)
	kubectl delete job db-migrate seed-demo minio-init -n $(NS) --ignore-not-found
	kubectl apply -k $(OVERLAY)
	kubectl -n $(NS) wait --for=condition=ready pod -l app.kubernetes.io/name=postgres --timeout=180s
	kubectl -n $(NS) wait --for=condition=complete job/db-migrate --timeout=300s
	kubectl -n $(NS) wait --for=condition=complete job/minio-init --timeout=180s
	kubectl -n $(NS) wait --for=condition=available deploy/keycloak --timeout=600s
	kubectl -n $(NS) wait --for=condition=available deploy/backend --timeout=300s
	kubectl -n $(NS) wait --for=condition=available deploy/llm-gateway --timeout=180s
	@echo "Стенд готов: http://crm.localhost (k3d, без /etc/hosts); minikube — http://crm.local (make hosts)"

# pipefail: без него упавший kustomize (нет секретов) даёт kubeconform пустой вход — «0 ресурсов», exit 0
k8s-validate: SHELL := /bin/bash
k8s-validate: ## рендер всех overlay + kubeconform (нужны сгенерированные секреты)
	set -o pipefail; kubectl kustomize $(K3D_OVERLAY) | kubeconform -strict -ignore-missing-schemas -summary
	set -o pipefail; kubectl kustomize $(MINI_OVERLAY) | kubeconform -strict -ignore-missing-schemas -summary
	set -o pipefail; kubectl kustomize $(VM_OVERLAY) | kubeconform -strict -ignore-missing-schemas -summary

k8s-down: ## удалить кластер (k3d или minikube)
	k3d cluster delete $(CLUSTER) 2>/dev/null || minikube delete -p $(CLUSTER) || true

## --- Сводки ИИ: GigaChat через llm-gateway (services/llm-gateway/README.md) ------
# Ключ — только в gitignored-файлах; скрипт печатает имена ключей, но не значения.
llm-secret: ## ключ GigaChat в секрет: LLM_SRC=<файл с GIGACHAT_*> [OVERLAY=<оверлей>|compose]
	./deploy/scripts/llm-secret.sh "$(LLM_SRC)" $(OVERLAY)

llm-smoke: ## живой смоук GigaChat с этой машины: OAuth + 2 вызова (env из LLM_ENV, тратит токены)
	@test -f "$(LLM_ENV)" || { echo "llm-smoke: нет $(LLM_ENV) — make k8s-secrets, затем make llm-secret LLM_SRC=…" >&2; exit 2; }
	@cd services/llm-gateway && set -a && . "$(abspath $(LLM_ENV))" && set +a && \
	  ALMA_LLM_LIVE=1 GIGACHAT_CA_BUNDLE="$(abspath $(LLM_CA))" $(UV) run python -m app.tools.smoke

llm-smoke-k8s: ## тот же смоук из DMZ-пода llm-gateway (проверка egress 443/9443 из кластера)
	kubectl -n $(NS) exec deploy/llm-gateway -- sh -c 'ALMA_LLM_LIVE=1 python -m app.tools.smoke'

## --- Публичный VPS (голый k3s, судьи заходят по https://crm.<IP>.nip.io) -------
vm-install: ## VPS: bootstrap — swap 2G + установка k3s + ожидание node Ready (идемпотентен)
	ssh $(VM_HOST) 'bash -s' < deploy/scripts/vm-install.sh

vm-deploy: ## VPS: amd64-образы → scp/import, rsync deploy/, cert-manager, apply vm, smoke
	VM_IP=$(VM_IP) VM_HOST=$(VM_HOST) ./deploy/scripts/vm-deploy.sh

vm-smoke: ## VPS: только smoke-проверки публичных URL стенда
	VM_IP=$(VM_IP) VM_HOST=$(VM_HOST) ./deploy/scripts/vm-deploy.sh smoke

vm-demo-reset: ## VPS: сброс стенда к эталону (ANCHOR=ГГГГ-ММ-ДД — дата показа); deploy/ — после vm-deploy
	ssh $(VM_HOST) "cd /opt/crm && KUBECTL='k3s kubectl' NS=$(NS) ANCHOR='$(ANCHOR)' bash deploy/scripts/demo-reset.sh"

## --- Данные и демо ------------------------------------------------------------
# Сид идемпотентен и даты уже созданных строк не двигает: к показу стенд «стареет»
# (зависшие копятся, «Завершено за месяц» пустеет). Перед показом — make demo-reset.
seed: ## перезапустить сид демо-данных (идемпотентен; обновляет цвета и пороги этапов)
	kubectl delete job seed-demo -n $(NS) --ignore-not-found
	kubectl apply -k $(OVERLAY)
	kubectl -n $(NS) wait --for=condition=complete job/seed-demo --timeout=300s

demo: seed ## подготовить демо: сид + триггер зависших заявок на глазах у жюри
	kubectl -n $(NS) delete job stuck-demo --ignore-not-found
	kubectl -n $(NS) create job stuck-demo --from=cronjob/stuck-check
	kubectl -n $(NS) wait --for=condition=complete job/stuck-demo --timeout=120s
	@echo "CRM:    http://crm.localhost        (пользователи по ролям — README)"
	@echo "LMS:    http://lms.crm.localhost    CMS: http://cms.crm.localhost"
	@echo "MinIO:  http://minio.crm.localhost  Keycloak: http://id.crm.localhost"
	@echo "(вход в UI — только через *.localhost: http://*.local — insecure context, PKCE не работает)"

# Сброс к эталону: CronJob stuck-check на паузе, Job из шаблона cronjob/demo-reset —
# доменные таблицы очищаются и засеваются от «сегодня» (или ANCHOR), app_user (keycloak_sub,
# Telegram) и аудит сохраняются, роли F2 и демо-роли kam_b2b/kam_b2c — к исходным.
# Утром в день показа:  make demo-reset ANCHOR=2026-10-23
demo-reset: ## сброс стенда к эталону: пересев от сегодня/ANCHOR, роли к исходным (k8s)
	NS=$(NS) ANCHOR='$(ANCHOR)' ./deploy/scripts/demo-reset.sh

demo-reset-local: ## то же для compose-стека (make dev)
	MODE=compose ANCHOR='$(ANCHOR)' ./deploy/scripts/demo-reset.sh

hosts: ## строка для /etc/hosts — нужна только для алиасов *.crm.local (curl/API, minikube)
	@echo "127.0.0.1 crm.local id.crm.local lms.crm.local cms.crm.local minio.crm.local"
	@echo "(для minikube замените 127.0.0.1 на: minikube -p $(CLUSTER) ip, либо запустите 'minikube tunnel')"
	@echo "(браузерный вход на k3d — через http://crm.localhost: /etc/hosts не нужен, Chromium резолвит сам)"

stuck-check: ## compose-паритет k8s CronJob: триггер проверки зависших заявок
	curl -fsS -X POST -H "X-Internal-Token: $${INTERNAL_API_TOKEN:-dev-internal-token}" \
	  http://localhost:8000/api/v1/internal/jobs/check-stuck-requests

idempotency-purge: ## compose-паритет k8s CronJob: очистка просроченных ключей Idempotency-Key
	curl -fsS -X POST -H "X-Internal-Token: $${INTERNAL_API_TOKEN:-dev-internal-token}" \
	  http://localhost:8000/api/v1/internal/jobs/purge-idempotency-keys

demo-idempotency: ## демо Idempotency-Key против стенда (нужен DEMO_PASSWORD; см. скрипт)
	LOADTEST_CLIENT_SECRET=$${LOADTEST_CLIENT_SECRET:-$$(kubectl -n $(NS) get secret crm-keycloak-clients -o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d)} \
	./deploy/scripts/demo-idempotency.sh

# Замер переноса заявок при удалении этапа (docs/evidence/migration-benchmark.md):
# создаёт БД $(BENCH_DB), если её нет, доводит схему до head и запускает
# `python -m app.tools.bench_migration` — таблицы домена этой базы очищаются.
# Markdown-таблица — в stdout, ход замера — в stderr:
#   make -s bench-migration > bench.md
#   make bench-migration BENCH_ARGS="--sizes 1000,10000,50000 --legacy-max 50000 --json $PWD/bench.json"
bench-migration: ## замер массового переноса заявок на одноразовой БД crm_bench (BENCH_ARGS=...)
	@case "$(BENCH_DB)" in \
	  *_bench|*_test|crm_test*) ;; \
	  *) echo "bench-migration: нужна одноразовая база *_bench, *_test или crm_test* (BENCH_DB=$(BENCH_DB)) — замер очищает таблицы" >&2; exit 2;; \
	esac
	@printf '%s\n' \
	  'import asyncio, sys' \
	  'import asyncpg' \
	  'async def main(dsn, name):' \
	  '    conn = await asyncpg.connect(dsn, timeout=5)' \
	  '    try:' \
	  '        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $$1", name):' \
	  '            await conn.execute(f"CREATE DATABASE \"{name}\"")' \
	  '            print(f"bench-migration: создана база {name}", file=sys.stderr)' \
	  '    finally:' \
	  '        await conn.close()' \
	  'asyncio.run(main(sys.argv[1], sys.argv[2]))' \
	  | (cd backend && $(BACKEND_PY) - "$(BENCH_PG)/postgres" "$(BENCH_DB)")
	@cd backend && DATABASE_URL="$(BENCH_URL)" $(BACKEND_PY) -m alembic upgrade head
	@cd backend && DATABASE_URL="$(BENCH_URL)" $(BACKEND_PY) -m app.tools.bench_migration $(BENCH_ARGS)

docs-arch: ## перегенерировать модель Archi и перечень библиотек (docs/architecture)
	python3 docs/architecture/archi/build_archi.py
	cd backend && $(BACKEND_PY) -m app.tools.list_libraries

load-test: ## k6: 300→400 VU против стенда (docker-вариант — deploy/load/README.md)
	@$(K6_CHECK)
	$(K6_SECRET) \
	k6 run deploy/load/k6-script.js \
	  --out json=docs/defense/load-raw.json \
	  --summary-export docs/defense/load-summary.json

# НФТ ТЗ (методика — deploy/load/README.md): 50 пользователей под 5 учётками, 10 параллельных
# отчётов на холодном кэше с выгрузками и оба профиля разом. DURATION=5m по умолчанию.
load-test-users: ## k6: 50 VU под 5+ учётками (НФТ «50 параллельных пользователей»)
	@$(K6_CHECK)
	$(K6_SECRET) k6 run -e SCENARIO=users50 deploy/load/k6-script.js \
	  --summary-export docs/defense/load-users50-summary.json

load-test-reports: ## k6: 10 VU строят разные отчёты + выгрузки, холодный кэш (НФТ «10 отчётов»)
	@$(K6_CHECK)
	$(K6_SECRET) k6 run -e SCENARIO=reports deploy/load/k6-script.js \
	  --summary-export docs/defense/load-reports-summary.json

load-test-nft: ## k6: 10 отчётов на фоне 50 пользователей одновременно
	@$(K6_CHECK)
	$(K6_SECRET) k6 run -e SCENARIO=nft deploy/load/k6-script.js \
	  --out json=docs/defense/load-nft-raw.json \
	  --summary-export docs/defense/load-nft-summary.json

logs: ## логи backend в кластере
	kubectl -n $(NS) logs -f deploy/backend

clean: ## снести compose-стек (с томами) и кластер; секреты *.env НЕ трогает
	$(COMPOSE) down -v --remove-orphans 2>/dev/null || true
	$(MAKE) k8s-down
	@echo "Сгенерированные секреты (deploy/compose/.env, deploy/k8s/overlays/*/secrets/*.env) сохранены."
