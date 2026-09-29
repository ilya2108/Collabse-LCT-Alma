#!/bin/sh
# Регенерация ConfigMap keycloak-realm из канонического deploy/keycloak/realm-crm.json.
# Запускать после любой правки realm-JSON (обёрнуто в `make realm-sync`);
# так реалм существует в одном экземпляре и в git нет расхождений compose/k8s.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC="$REPO_ROOT/deploy/keycloak/realm-crm.json"
DST="$REPO_ROOT/deploy/k8s/base/keycloak/realm-configmap.yaml"

command -v kubectl >/dev/null 2>&1 || { echo "sync-realm-configmap: требуется kubectl" >&2; exit 1; }

{
    echo "# АВТОГЕНЕРИРОВАНО скриптом deploy/scripts/sync-realm-configmap.sh (make realm-sync)"
    echo "# из канонического deploy/keycloak/realm-crm.json — НЕ править вручную."
    echo "# Секреты confidential-клиентов подставляются обёрткой import-realm.sh при старте"
    echo "# (плейсхолдеры \${env.*} + envFrom Secret crm-keycloak-clients в Deployment:"
    echo "# KC 26 сам плейсхолдеры при --import-realm НЕ заменяет)."
    kubectl create configmap keycloak-realm \
        --from-file=realm-crm.json="$SRC" \
        --from-file=import-realm.sh="$REPO_ROOT/deploy/keycloak/import-realm.sh" \
        --dry-run=client -o yaml
} > "$DST"

echo "sync-realm-configmap: обновлён $DST"
