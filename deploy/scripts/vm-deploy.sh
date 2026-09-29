#!/usr/bin/env bash
# Деплой CRM на публичный VPS (k3s) — запускается С ЛОКАЛЬНОЙ машины.
# Предусловие: на VPS уже отработал vm-install.sh (make vm-install).
#
#   deploy/scripts/vm-deploy.sh          # полный цикл: build → import → apply → smoke
#   deploy/scripts/vm-deploy.sh smoke    # только smoke-проверки публичных URL
#
# Mac (arm64) → VPS (x86_64): образы собираются docker buildx --platform
# linux/amd64 в docker-архивы (тег :vm — не пересекается с локальными :dev),
# уезжают по scp и импортируются в containerd k3s (k3s ctr images import).
set -euo pipefail

# --- параметры (переопределяются окружением) ---------------------------------
VM_IP="${VM_IP:-168.113.158.10}"
VM_HOST="${VM_HOST:-root@${VM_IP}}"
VM_DIR="${VM_DIR:-/opt/crm}"

APP_HOST="crm.${VM_IP//./-}.nip.io"   # crm.168-113-158-10.nip.io
ID_HOST="id.${APP_HOST}"
LMS_HOST="lms.${APP_HOST}"
CMS_HOST="cms.${APP_HOST}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMG_DIR="${IMG_DIR:-$REPO_ROOT/.vm-images}"   # gitignored
TAG=vm
NS=crm
KCTL="k3s kubectl"                            # kubectl на VPS

# имя_образа:контекст_сборки
IMAGES="backend:backend frontend:frontend notification:services/notification lms-stub:services/lms-stub cms-stub:services/cms-stub"

smoke() {
    echo "== smoke: публичные URL =="
    for url in \
        "https://${APP_HOST}/" \
        "https://${APP_HOST}/api/v1/openapi.json" \
        "https://${ID_HOST}/realms/crm/.well-known/openid-configuration" \
        "https://${LMS_HOST}/" \
        "https://${CMS_HOST}/"; do
        code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 20 "$url")" \
            || { echo "SMOKE FAIL: $url" >&2; exit 1; }
        echo "  $code  $url"
    done
    echo "smoke: OK — стенд доступен снаружи: https://${APP_HOST}"
}

if [ "${1:-}" = "smoke" ]; then
    smoke
    exit 0
fi

cd "$REPO_ROOT"

echo "== 1/7 сборка образов linux/amd64 → docker-архивы =="
# Экспорт в tar (-o type=docker,dest=...) не работает на дефолтном docker-драйвере —
# нужен builder с драйвером docker-container (создаётся один раз, переиспользуется).
docker buildx inspect alma-amd64 >/dev/null 2>&1 ||     docker buildx create --name alma-amd64 --driver docker-container >/dev/null
mkdir -p "$IMG_DIR"
for pair in $IMAGES; do
    name="${pair%%:*}"; ctx="${pair#*:}"
    echo "-- crm/${name}:${TAG} (${ctx})"
    docker buildx build --builder alma-amd64 --platform linux/amd64 \
        -t "crm/${name}:${TAG}" \
        -o "type=docker,dest=${IMG_DIR}/${name}.tar" \
        "$ctx"
done

echo "== 2/7 доставка и импорт образов в containerd k3s =="
ssh "$VM_HOST" "mkdir -p '$VM_DIR/images'"
for pair in $IMAGES; do
    name="${pair%%:*}"
    scp "${IMG_DIR}/${name}.tar" "$VM_HOST:$VM_DIR/images/${name}.tar"
    ssh "$VM_HOST" "k3s ctr images import '$VM_DIR/images/${name}.tar' >/dev/null && echo '  imported crm/${name}:${TAG}'"
done

echo "== 3/7 rsync deploy/ на VPS =="
# --exclude '*.env' — локальные секреты (k3d/minikube/compose) НЕ покидают машину;
# секреты оверлея vm живут только на VPS (шаг 4) и rsync их не трогает
rsync -az --exclude='*.env' --exclude='.vm-images' \
    "$REPO_ROOT/deploy/" "$VM_HOST:$VM_DIR/deploy/"

echo "== 4/7 генерация секретов оверлея vm (на VPS, идемпотентно) =="
ssh "$VM_HOST" "cd '$VM_DIR' && sh deploy/scripts/gen-secrets.sh deploy/k8s/overlays/vm"
# Телеграм-токен пользователь кладёт на VPS сам (/root/alma-secrets/telegram.env);
# подставляем его в секрет оверлея на сервере — локально токен не появляется.
ssh "$VM_HOST" "if [ -f /root/alma-secrets/telegram.env ]; then \
  for key in TELEGRAM_BOT_TOKEN TELEGRAM_PROXY_URL TELEGRAM_API_BASE; do \
    line=\$(grep \"^\$key=\" /root/alma-secrets/telegram.env || true); \
    if [ -n \"\$line\" ]; then \
      if grep -q \"^\$key=\" '$VM_DIR/deploy/k8s/overlays/vm/secrets/crm-telegram.env'; then \
        sed -i \"s|^\$key=.*|\$line|\" '$VM_DIR/deploy/k8s/overlays/vm/secrets/crm-telegram.env'; \
      else echo \"\$line\" >> '$VM_DIR/deploy/k8s/overlays/vm/secrets/crm-telegram.env'; fi; \
      echo \"telegram secret: \$key injected\"; \
    fi; \
  done; \
else echo 'telegram token: NOT FOUND — бот будет в stub-режиме'; fi"

echo "== 5/7 cert-manager + ClusterIssuer =="
ssh "$VM_HOST" "set -e
    $KCTL apply -f '$VM_DIR/deploy/k8s/vm-addons/cert-manager.yaml' >/dev/null
    for d in cert-manager cert-manager-cainjector cert-manager-webhook; do
        $KCTL -n cert-manager rollout status deploy/\$d --timeout=300s
    done
    # webhook может отдать ready чуть позже rollout'а — apply с ретраями
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if $KCTL apply -f '$VM_DIR/deploy/k8s/vm-addons/cluster-issuer.yaml'; then
            break
        fi
        echo '  ...webhook ещё не готов, повтор через 10 с'; sleep 10
    done"

echo "== 6/7 применение оверлея vm и ожидание готовности =="
ssh "$VM_HOST" "set -e
    cd '$VM_DIR'
    # Jobs неизменяемы — пересоздаём (Alembic/minio-init/seed идемпотентны)
    $KCTL delete job db-migrate seed-demo minio-init -n $NS --ignore-not-found
    $KCTL apply -k deploy/k8s/overlays/vm
    $KCTL -n $NS wait --for=condition=ready pod -l app.kubernetes.io/name=postgres --timeout=300s
    $KCTL -n $NS wait --for=condition=complete job/db-migrate --timeout=300s
    $KCTL -n $NS wait --for=condition=complete job/minio-init --timeout=180s
    $KCTL -n $NS wait --for=condition=available deploy/keycloak --timeout=600s
    $KCTL -n $NS wait --for=condition=available deploy/backend --timeout=300s
    $KCTL -n $NS wait --for=condition=complete job/seed-demo --timeout=300s
    echo '-- ожидание выпуска LE-сертификатов (http01)'
    $KCTL -n $NS wait --for=condition=Ready certificate --all --timeout=600s
    $KCTL -n $NS get certificate"

echo "== 7/7 smoke =="
smoke
echo
echo "Готово. Ссылки для судей:"
echo "  CRM:      https://${APP_HOST}"
echo "  Keycloak: https://${ID_HOST}"
echo "  LMS-стаб: https://${LMS_HOST}   CMS-стаб: https://${CMS_HOST}"
