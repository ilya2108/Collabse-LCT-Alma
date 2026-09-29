#!/bin/sh
# Генерация gitignored-секретов для kustomize secretGenerator.
#
# Использование: ./deploy/scripts/gen-secrets.sh <overlay-dir>
#   например:    ./deploy/scripts/gen-secrets.sh deploy/k8s/overlays/k3d
#
# Для каждого secrets/*.env.example создаётся secrets/*.env со случайными
# значениями вместо плейсхолдеров __GEN_*__. Существующие *.env не трогаются:
# повторный запуск ничего не перетирает — ключ шифрования ПДн (crm-pii)
# стабилен между `make k8s-apply`.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/genlib.sh"

OVERLAY="${1:?использование: gen-secrets.sh <overlay-dir>, например deploy/k8s/overlays/k3d}"
SECRETS_DIR="$OVERLAY/secrets"

if [ ! -d "$SECRETS_DIR" ]; then
    echo "gen-secrets: каталог $SECRETS_DIR не найден" >&2
    exit 1
fi

echo "gen-secrets: $SECRETS_DIR"
found=0
for example in "$SECRETS_DIR"/*.env.example; do
    [ -f "$example" ] || continue
    found=1
    fill_env_file "$example" "${example%.example}"
done

if [ "$found" -eq 0 ]; then
    echo "gen-secrets: в $SECRETS_DIR нет *.env.example" >&2
    exit 1
fi
