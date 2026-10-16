#!/usr/bin/env bash
# Сброс демо-стенда к эталону перед показом (make demo-reset / make vm-demo-reset).
#
# Что делает (k8s): приостанавливает CronJob stuck-check (эскалации не уходят посреди
# пересева), создаёт Job из шаблона cronjob/demo-reset (deploy/k8s/base/cronjobs/
# demo-reset-cronjob.yaml) с DEMO_ANCHOR_DATE, ждёт завершения, печатает его лог и
# возвращает CronJob в прежнее состояние — даже если сброс упал.
# Сам сброс — `python -m app.tools.seed --reset`: одна транзакция, при ошибке стенд
# остаётся прежним. app_user (keycloak_sub, привязки Telegram) и журнал аудита не
# очищаются; роли F2 и демо-роли kam_b2b/kam_b2c — к исходным; даты — от якоря.
#
#   deploy/scripts/demo-reset.sh                     # k8s (kubectl текущего контекста)
#   ANCHOR=2026-10-23 deploy/scripts/demo-reset.sh   # данные «на 09:00 МСК 23.10»
#   MODE=compose deploy/scripts/demo-reset.sh        # dev-стек docker compose
#   make vm-demo-reset ANCHOR=2026-10-23             # VPS: скрипт запускается на VM
#                                                    # из /opt/crm с KUBECTL="k3s kubectl"
#
# Параметры окружения:
#   MODE      k8s (по умолчанию) | compose
#   ANCHOR    «сейчас» демо-данных: пусто — момент запуска; ГГГГ-ММ-ДД — 09:00 МСК этого дня
#   NS        namespace (crm)
#   KUBECTL   команда kubectl (kubectl)
#   TIMEOUT   ожидание Job, с (600)
set -euo pipefail

MODE="${MODE:-k8s}"
ANCHOR="${ANCHOR:-}"
NS="${NS:-crm}"
KUBECTL="${KUBECTL:-kubectl}"
TIMEOUT="${TIMEOUT:-600}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compose/docker-compose.dev.yml"
# CronJob'ы, которые не должны срабатывать посреди сброса
PAUSE_CRONJOBS="${PAUSE_CRONJOBS:-stuck-check}"

if [[ -n "$ANCHOR" && ! "$ANCHOR" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}([T\ ].*)?$ ]]; then
    echo "demo-reset: ANCHOR=«$ANCHOR» — ожидается ГГГГ-ММ-ДД или ISO дата-время" >&2
    exit 2
fi

if [[ "$MODE" == "compose" ]]; then
    echo "== demo-reset: compose-стек (якорь: ${ANCHOR:-сейчас}) =="
    docker compose -f "$COMPOSE_FILE" exec -T -e DEMO_ANCHOR_DATE="$ANCHOR" backend \
        uv run python -m app.tools.seed --reset
    echo "Готово. Зависшие на глазах у жюри: make stuck-check"
    exit 0
fi
if [[ "$MODE" != "k8s" ]]; then
    echo "demo-reset: MODE=$MODE — поддерживаются k8s и compose" >&2
    exit 2
fi

kctl() { $KUBECTL -n "$NS" "$@"; }

# --- CronJob'ы на паузу, по выходу — вернуть как было --------------------------------
declare -a RESUME=()
resume_cronjobs() {
    for name in "${RESUME[@]:-}"; do
        [[ -z "$name" ]] && continue
        kctl patch cronjob "$name" --type merge -p '{"spec":{"suspend":false}}' >/dev/null \
            && echo "   cronjob/$name снова по расписанию"
    done
}
trap resume_cronjobs EXIT

echo "== demo-reset: namespace $NS, якорь: ${ANCHOR:-сейчас} =="
kctl get cronjob demo-reset >/dev/null 2>&1 || {
    echo "demo-reset: нет cronjob/demo-reset — примените манифесты (make k8s-apply / make vm-deploy)" >&2
    exit 1
}
for name in $PAUSE_CRONJOBS; do
    suspended="$(kctl get cronjob "$name" -o jsonpath='{.spec.suspend}' 2>/dev/null || true)"
    if [[ -n "$(kctl get cronjob "$name" -o name 2>/dev/null || true)" && "$suspended" != "true" ]]; then
        kctl patch cronjob "$name" --type merge -p '{"spec":{"suspend":true}}' >/dev/null
        RESUME+=("$name")
        echo "   cronjob/$name приостановлен на время сброса"
    fi
done

# --- Job из шаблона; DEMO_ANCHOR_DATE — через set env --local (create --from не умеет env) ---
JOB="demo-reset-$(date +%Y%m%d%H%M%S)"
kctl create job "$JOB" --from=cronjob/demo-reset --dry-run=client -o yaml \
    | $KUBECTL set env --local -f - -o yaml DEMO_ANCHOR_DATE="$ANCHOR" \
    | kctl apply -f - >/dev/null
echo "   job/$JOB создан, ждём завершения (до ${TIMEOUT} с)…"

status=0
if ! kctl wait --for=condition=complete "job/$JOB" --timeout="${TIMEOUT}s" >/dev/null 2>&1; then
    status=1
fi
kctl logs "job/$JOB" --tail=50 || true
if [[ $status -ne 0 ]]; then
    echo "demo-reset: job/$JOB не завершился успешно — стенд в прежнем состоянии (транзакция откатилась)" >&2
    exit 1
fi
echo "Готово: стенд сброшен к эталону. Проверка зависших на глазах у жюри:"
echo "   $KUBECTL -n $NS create job stuck-demo-\$(date +%s) --from=cronjob/stuck-check"
