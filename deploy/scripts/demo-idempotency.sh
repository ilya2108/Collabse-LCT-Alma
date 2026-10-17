#!/usr/bin/env bash
# Демо сквозного Idempotency-Key (api-contract.md §1.6) против живого стенда.
# Запуск: make demo-idempotency (секрет клиента crm-loadtest достаётся из кластера).
#
# Сценарий на комментариях заявки (видны в карточке — «в системе одна запись»):
#   1. POST комментария с ключом K → 201;
#   2. тот же POST с K → тот же ответ + Idempotent-Replayed: true, дубля нет;
#   3. K с другим телом → 422 idempotency_key_reused;
#   4. два параллельных POST с новым ключом K2 → 201 и (409 idempotency_in_progress
#      или повтор ответа) — но не две записи;
#   5. GET комментариев: ровно две демо-записи (по одной на ключ); затем удаление.
#
# Токен — через confidential-клиент crm-loadtest (Direct Access Grants включён только
# у него), обычный пользователь без обхода прав. Все параметры — из окружения,
# паролей в скрипте нет:
#   LOADTEST_CLIENT_SECRET  секрет клиента crm-loadtest (обязателен)
#   DEMO_PASSWORD           пароль DEMO_USER (обязателен)
#   DEMO_USER               по умолчанию kam1@demo
#   BASE_URL / KC_URL       по умолчанию http://crm.local и http://id.crm.local (make hosts);
#                           VPS: https://crm.<IP>.nip.io и https://id.crm.<IP>.nip.io
#   KC_REALM                по умолчанию crm
set -euo pipefail

BASE_URL="${BASE_URL:-http://crm.local}"
KC_URL="${KC_URL:-http://id.crm.local}"
KC_REALM="${KC_REALM:-crm}"
CLIENT_ID="${DEMO_CLIENT_ID:-crm-loadtest}"
DEMO_USER="${DEMO_USER:-kam1@demo}"
: "${LOADTEST_CLIENT_SECRET:?нужен секрет клиента crm-loadtest — make demo-idempotency берёт его из кластера}"
: "${DEMO_PASSWORD:?задайте пароль демо-пользователя: DEMO_PASSWORD=... make demo-idempotency}"

command -v jq >/dev/null 2>&1 || { echo "нужен jq (brew install jq / apt install jq)"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "нужен curl"; exit 1; }

API="$BASE_URL/api/v1"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILED=0

new_key() {
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    else
        cat /proc/sys/kernel/random/uuid
    fi
}

step() { printf '\n== %s\n' "$*"; }

check() { # check <условие-описание> <фактическое> <ожидаемое>
    if [ "$2" = "$3" ]; then
        printf '   OK      %s: %s\n' "$1" "$2"
    else
        printf '   ОШИБКА  %s: %s (ожидалось %s)\n' "$1" "$2" "$3"
        FAILED=1
    fi
}

# --- токен: секрет и пароль идут через файлы, а не через аргументы curl (не видны в ps) ---
umask 077
printf '%s' "$LOADTEST_CLIENT_SECRET" >"$TMP/client_secret"
printf '%s' "$DEMO_PASSWORD" >"$TMP/password"
curl -fsS -X POST "$KC_URL/realms/$KC_REALM/protocol/openid-connect/token" \
    --data-urlencode grant_type=password \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret@$TMP/client_secret" \
    --data-urlencode "username=$DEMO_USER" \
    --data-urlencode "password@$TMP/password" >"$TMP/token.json"
printf 'Authorization: Bearer %s\n' "$(jq -r .access_token "$TMP/token.json")" >"$TMP/auth.h"

# call <метод> <путь> <ключ|-> <json|-> <имя> → печатает HTTP-статус;
# тело — $TMP/<имя>.json, заголовки ответа — $TMP/<имя>.h
call() {
    local method=$1 path=$2 key=$3 body=$4 name=$5
    local args=(-sS -o "$TMP/$name.json" -D "$TMP/$name.h" -w '%{http_code}' -X "$method"
        -H "@$TMP/auth.h" -H 'Accept: application/json')
    if [ "$key" != "-" ]; then args+=(-H "Idempotency-Key: $key"); fi
    if [ "$body" != "-" ]; then args+=(-H 'Content-Type: application/json' --data-binary "$body"); fi
    curl "${args[@]}" "$API$path"
}

replayed() { # значение Idempotent-Replayed или «—»
    local value
    value="$(grep -i '^idempotent-replayed:' "$TMP/$1.h" | tr -d '\r' | awk '{print $2}' || true)"
    printf '%s' "${value:-—}"
}

text_body() { jq -nc --arg t "$1" '{text: $t}'; }

step "Заявка для демо (самая свежая из видимых $DEMO_USER)"
status="$(call GET '/requests?limit=1&sort=-created_at' - - list)"
check "GET /requests" "$status" 200
REQUEST_ID="$(jq -r '.items[0].id // empty' "$TMP/list.json")"
[ -n "$REQUEST_ID" ] || { echo "   у пользователя нет видимых заявок — запустите make seed"; exit 1; }
echo "   заявка $REQUEST_ID"
COMMENTS="/requests/$REQUEST_ID/comments"
MARK="Демо Idempotency-Key $(date +%H:%M:%S)"
K1="$(new_key)"
K2="$(new_key)"

step "1. POST комментария, Idempotency-Key: $K1"
status="$(call POST "$COMMENTS" "$K1" "$(text_body "$MARK: первый ключ")" first)"
FIRST_ID="$(jq -r .id "$TMP/first.json")"
check "статус" "$status" 201
echo "   комментарий $FIRST_ID, Idempotent-Replayed: $(replayed first)"

step "2. Тот же запрос с тем же ключом (ретрай после таймаута, двойной клик)"
status="$(call POST "$COMMENTS" "$K1" "$(text_body "$MARK: первый ключ")" again)"
check "статус" "$status" 201
check "тот же комментарий" "$(jq -r .id "$TMP/again.json")" "$FIRST_ID"
check "Idempotent-Replayed" "$(replayed again)" true

step "3. Тот же ключ, другое тело"
status="$(call POST "$COMMENTS" "$K1" "$(text_body "$MARK: другое тело")" reused)"
check "статус" "$status" 422
check "код ошибки" "$(jq -r .error.code "$TMP/reused.json")" idempotency_key_reused
echo "   $(jq -r .error.message "$TMP/reused.json")"

step "4. Два параллельных POST с новым ключом $K2"
call POST "$COMMENTS" "$K2" "$(text_body "$MARK: второй ключ")" par1 >"$TMP/par1.code" &
call POST "$COMMENTS" "$K2" "$(text_body "$MARK: второй ключ")" par2 >"$TMP/par2.code" &
wait
for name in par1 par2; do
    code="$(cat "$TMP/$name.code")"
    detail="$(jq -r '.error.code // .id' "$TMP/$name.json")"
    echo "   $name: HTTP $code, $detail, Idempotent-Replayed: $(replayed "$name")"
done
echo "   (409 idempotency_in_progress — первый ещё выполнялся; повтор ответа — уже завершился)"

step "5. Сколько демо-комментариев в заявке"
status="$(call GET "$COMMENTS" - - comments)"
check "GET комментариев" "$status" 200
jq -r --arg m "$MARK" '[.items[] | select(.text | startswith($m)) | .id] | unique | .[]' \
    "$TMP/comments.json" >"$TMP/ids"
check "записей на два ключа" "$(wc -l <"$TMP/ids" | tr -d ' ')" 2

step "Уборка: удаление демо-комментариев"
while read -r comment_id; do
    [ -n "$comment_id" ] || continue
    status="$(call DELETE "$COMMENTS/$comment_id" - - "del-$comment_id")"
    echo "   DELETE $comment_id → HTTP $status"
done <"$TMP/ids"

if [ "$FAILED" -ne 0 ]; then
    printf '\nЕсть расхождения с контрактом §1.6 — см. строки ОШИБКА выше.\n'
    exit 1
fi
printf '\nГотово: повтор с тем же ключом не создаёт дублей; в Swagger (%s/docs) заголовок\nIdempotency-Key виден у каждой мутации.\n' "$API"
