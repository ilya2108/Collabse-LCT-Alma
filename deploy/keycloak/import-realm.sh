#!/bin/bash
# Обёртка импорта realm: Keycloak (Quarkus, 26.x) при `start[-dev] --import-realm`
# НЕ подставляет плейсхолдеры ${env.*} в realm-JSON (проверено эмпирически) —
# подставляем их сами из окружения контейнера и запускаем kc.sh.
#
# Использование (entrypoint/command контейнера):
#   /bin/bash /realm-src/import-realm.sh start-dev --import-realm ...
# Входы: REALM_SRC (default /realm-src/realm-crm.json, монтируется read-only),
#        REALM_DST (default /opt/keycloak/data/import/realm-crm.json, writable).
set -euo pipefail

SRC="${REALM_SRC:-/realm-src/realm-crm.json}"
DST="${REALM_DST:-/opt/keycloak/data/import/realm-crm.json}"

mkdir -p "$(dirname "$DST")"
cp "$SRC" "$DST"
for ph in $(grep -o '\${env\.[A-Za-z_][A-Za-z0-9_]*}' "$SRC" | sort -u); do
    var="${ph#\$\{env.}"
    var="${var%\}}"
    val="${!var:-}"
    if [ -z "$val" ]; then
        echo "import-realm: ВНИМАНИЕ — переменная $var не задана, плейсхолдер останется пустым" >&2
    fi
    # секреты — hex/латиница без спецсимволов sed; разделитель | не встречается
    sed -i "s|\${env\.$var}|$val|g" "$DST"
done

exec /opt/keycloak/bin/kc.sh "$@"
