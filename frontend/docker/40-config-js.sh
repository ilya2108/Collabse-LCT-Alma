#!/bin/sh
# Генерация рантайм-конфига /config.js из шаблона (12-factor: один образ — любое окружение).
# Скрипт выполняется штатным entrypoint'ом nginx (каталог /docker-entrypoint.d).
set -eu

TEMPLATE=/usr/share/nginx/html/config.js.template
TARGET=/usr/share/nginx/html/config.js

# В k8s config.js монтируется из ConfigMap read-only — смонтированный файл имеет приоритет.
if [ -f "$TARGET" ] && ! ( : >> "$TARGET" ) 2>/dev/null; then
    echo "40-config-js: $TARGET смонтирован извне (read-only) — генерация пропущена"
    exit 0
fi

envsubst '${KEYCLOAK_URL} ${KEYCLOAK_REALM} ${KEYCLOAK_CLIENT_ID} ${API_BASE}' \
    < "$TEMPLATE" > "$TARGET"
echo "40-config-js: сгенерирован $TARGET (KEYCLOAK_URL=${KEYCLOAK_URL})"
