# CRM «Вузы» — ЛЦТ 2026. Цели верхнего уровня (подробности: docs/design/deployment.md).
# Локальная разработка — make dev (compose); стенд/демо — make k8s-up (k3d) или k8s-up-minikube.

CLUSTER      ?= crm
NS           ?= crm
IMAGES        = crm/backend crm/frontend crm/notification crm/lms-stub crm/cms-stub
K3D_OVERLAY   = deploy/k8s/overlays/k3d
MINI_OVERLAY  = deploy/k8s/overlays/minikube
VM_OVERLAY    = deploy/k8s/overlays/vm
OVERLAY      ?= $(K3D_OVERLAY)
COMPOSE       = docker compose -f deploy/compose/docker-compose.dev.yml
# Публичный VPS (голый k3s): деплой — deploy/scripts/vm-*.sh
VM_IP        ?= 168.113.158.10
VM_HOST      ?= root@$(VM_IP)

.PHONY: help dev dev-env dev-down build-images realm-sync k8s-secrets k8s-up \
        k8s-up-minikube k8s-apply k8s-validate k8s-down seed demo hosts \
        stuck-check load-test logs clean vm-install vm-deploy vm-smoke

help: ## список целей
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## --- Локальная разработка ---------------------------------------------------
dev: dev-env ## compose-стек (инфра + backend/notification/заглушки) + подсказка про фронт
	$(COMPOSE) up -d --build
	@echo "API:      http://localhost:8000/docs"
	@echo "Keycloak: http://localhost:8080  (admin / см. deploy/compose/.env)"
	@echo "Фронт:    cd frontend && npm run dev   → http://localhost:5173"

dev-env: ## сгенерировать deploy/compose/.env, если его нет (PII-ключи и пр.)
	./deploy/scripts/gen-dotenv.sh

dev-down: ## остановить compose-стек
	$(COMPOSE) down

## --- Сборка образов ----------------------------------------------------------
build-images: ## собрать все образы crm/*:dev
	docker build -t crm/backend:dev backend
	docker build -t crm/frontend:dev frontend
	docker build -t crm/notification:dev services/notification
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
	@echo "Стенд готов: http://crm.localhost (k3d, без /etc/hosts); minikube — http://crm.local (make hosts)"

k8s-validate: ## рендер всех overlay + kubeconform (нужны сгенерированные секреты)
	kubectl kustomize $(K3D_OVERLAY) | kubeconform -strict -ignore-missing-schemas -summary
	kubectl kustomize $(MINI_OVERLAY) | kubeconform -strict -ignore-missing-schemas -summary
	kubectl kustomize $(VM_OVERLAY) | kubeconform -strict -ignore-missing-schemas -summary

k8s-down: ## удалить кластер (k3d или minikube)
	k3d cluster delete $(CLUSTER) 2>/dev/null || minikube delete -p $(CLUSTER) || true

## --- Публичный VPS (голый k3s, судьи заходят по https://crm.<IP>.nip.io) -------
vm-install: ## VPS: bootstrap — swap 2G + установка k3s + ожидание node Ready (идемпотентен)
	ssh $(VM_HOST) 'bash -s' < deploy/scripts/vm-install.sh

vm-deploy: ## VPS: amd64-образы → scp/import, rsync deploy/, cert-manager, apply vm, smoke
	VM_IP=$(VM_IP) VM_HOST=$(VM_HOST) ./deploy/scripts/vm-deploy.sh

vm-smoke: ## VPS: только smoke-проверки публичных URL стенда
	VM_IP=$(VM_IP) VM_HOST=$(VM_HOST) ./deploy/scripts/vm-deploy.sh smoke

## --- Данные и демо ------------------------------------------------------------
seed: ## перезапустить сид демо-данных (идемпотентен)
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

hosts: ## строка для /etc/hosts — нужна только для алиасов *.crm.local (curl/API, minikube)
	@echo "127.0.0.1 crm.local id.crm.local lms.crm.local cms.crm.local minio.crm.local"
	@echo "(для minikube замените 127.0.0.1 на: minikube -p $(CLUSTER) ip, либо запустите 'minikube tunnel')"
	@echo "(браузерный вход на k3d — через http://crm.localhost: /etc/hosts не нужен, Chromium резолвит сам)"

stuck-check: ## compose-паритет k8s CronJob: триггер проверки зависших заявок
	curl -fsS -X POST -H "X-Internal-Token: $${INTERNAL_API_TOKEN:-dev-internal-token}" \
	  http://localhost:8000/api/v1/internal/jobs/check-stuck-requests

load-test: ## k6: 300→400 VU против стенда (docker-вариант — deploy/load/README.md)
	@command -v k6 >/dev/null 2>&1 || \
	  { echo "k6 не установлен — запуск в docker описан в deploy/load/README.md"; exit 1; }
	LOADTEST_CLIENT_SECRET=$${LOADTEST_CLIENT_SECRET:-$$(kubectl -n $(NS) get secret crm-keycloak-clients -o jsonpath='{.data.LOADTEST_CLIENT_SECRET}' | base64 -d)} \
	k6 run deploy/load/k6-script.js \
	  --out json=docs/defense/load-raw.json \
	  --summary-export docs/defense/load-summary.json

logs: ## логи backend в кластере
	kubectl -n $(NS) logs -f deploy/backend

clean: ## снести compose-стек (с томами) и кластер; секреты *.env НЕ трогает
	$(COMPOSE) down -v --remove-orphans 2>/dev/null || true
	$(MAKE) k8s-down
	@echo "Сгенерированные секреты (deploy/compose/.env, deploy/k8s/overlays/*/secrets/*.env) сохранены."
