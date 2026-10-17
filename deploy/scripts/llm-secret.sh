#!/bin/sh
# Перенос ключа GigaChat в gitignored-секрет: оверлей k8s или compose.
#
# Использование:
#   ./deploy/scripts/llm-secret.sh <src.env> [<overlay-dir>|compose]
#   make llm-secret LLM_SRC=~/secrets/gigachat.env                      # k3d
#   make llm-secret LLM_SRC=~/secrets/gigachat.env OVERLAY=deploy/k8s/overlays/vm
#   make llm-secret LLM_SRC=~/secrets/gigachat.env OVERLAY=compose      # deploy/compose/.env
#
# Из <src.env> берутся ТОЛЬКО GIGACHAT_CREDENTIALS, GIGACHAT_SCOPE, GIGACHAT_MODEL_STRONG,
# GIGACHAT_MODEL_FAST (остальные строки игнорируются). Цель:
#   <overlay>/secrets/crm-llm.env — если файла нет, сначала создаётся из crm-llm.env.example
#     (со случайным LLM_GATEWAY_TOKEN), как в gen-secrets.sh;
#   deploy/compose/.env — если файла нет, сначала создаётся из .env.example (gen-dotenv.sh).
# Значения НИКОГДА не выводятся (ни stdout, ни stderr, ни трассировка): печатаются только
# имена обновлённых ключей. Значения не попадают и в argv/окружение дочерних процессов —
# awk читает исходный файл сам. Итоговый файл — chmod 600.
set -eu
set +x # трассировка показала бы значения — выключена, даже если скрипт запущен через sh -x

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
. "$SCRIPT_DIR/genlib.sh"

SRC="${1:-}"
TARGET_ARG="${2:-deploy/k8s/overlays/k3d}"
KEYS="GIGACHAT_CREDENTIALS GIGACHAT_SCOPE GIGACHAT_MODEL_STRONG GIGACHAT_MODEL_FAST"

if [ -z "$SRC" ]; then
    echo "llm-secret: использование: llm-secret.sh <src.env> [<overlay-dir>|compose]" >&2
    echo "  (make llm-secret LLM_SRC=<файл с GIGACHAT_*> [OVERLAY=...|compose])" >&2
    exit 2
fi
if [ ! -r "$SRC" ]; then
    echo "llm-secret: файл $SRC не найден или недоступен для чтения" >&2
    exit 2
fi

if [ "$TARGET_ARG" = "compose" ]; then
    TARGET="$REPO_ROOT/deploy/compose/.env"
    EXAMPLE="$REPO_ROOT/deploy/compose/.env.example"
else
    case "$TARGET_ARG" in
        /*) OVERLAY_DIR="$TARGET_ARG" ;;
        *) OVERLAY_DIR="$REPO_ROOT/$TARGET_ARG" ;;
    esac
    if [ ! -d "$OVERLAY_DIR/secrets" ]; then
        echo "llm-secret: каталог $TARGET_ARG/secrets не найден" >&2
        exit 2
    fi
    TARGET="$OVERLAY_DIR/secrets/crm-llm.env"
    EXAMPLE="$OVERLAY_DIR/secrets/crm-llm.env.example"
fi

umask 077
if [ ! -f "$TARGET" ]; then
    fill_env_file "$EXAMPLE" "$TARGET" >/dev/null
    echo "llm-secret: создан ${TARGET#"$REPO_ROOT"/} из ${EXAMPLE##*/}"
fi

TMP="$(mktemp "$TARGET.XXXXXX")"
TMP2="$(mktemp "$TARGET.XXXXXX")"
UPDATED="$(mktemp)"
trap 'rm -f "$TMP" "$TMP2" "$UPDATED"' EXIT

# 1-й файл — источник: последнее вхождение KEY=... (допускаются `export `, кавычки, CRLF).
# 2-й файл — цель: строки KEY=... заменяются, недостающие ключи дописываются в конец.
awk -v keys="$KEYS" -v updated="$UPDATED" -v sq="'" '
    BEGIN { n = split(keys, list, " "); for (i = 1; i <= n; i++) wanted[list[i]] = 1 }
    function parse(line,    eq, k, v, q) {
        sub(/\r$/, "", line)
        sub(/^[ \t]*export[ \t]+/, "", line)
        eq = index(line, "=")
        if (eq == 0) return ""
        k = substr(line, 1, eq - 1)
        gsub(/^[ \t]+|[ \t]+$/, "", k)
        if (!(k in wanted)) return ""
        v = substr(line, eq + 1)
        gsub(/^[ \t]+|[ \t]+$/, "", v)
        q = substr(v, 1, 1)
        if (length(v) >= 2 && (q == "\"" || q == sq) && substr(v, length(v), 1) == q)
            v = substr(v, 2, length(v) - 2)
        value_of = v
        return k
    }
    FILENAME == ARGV[1] { # не FNR == NR: с пустым источником цель приняли бы за источник
        k = parse($0)
        if (k != "" && value_of != "") { found[k] = value_of }
        next
    }
    {
        k = parse($0)
        if (k != "" && (k in found)) {
            print k "=" found[k]
            done[k] = 1
            next
        }
        print
    }
    END {
        for (i = 1; i <= n; i++) {
            k = list[i]
            if (!(k in found)) continue
            if (!(k in done)) print k "=" found[k]
            print k > updated
        }
    }
' "$SRC" "$TARGET" > "$TMP"

if ! grep -qx "GIGACHAT_CREDENTIALS" "$UPDATED"; then
    echo "llm-secret: в $SRC нет непустого GIGACHAT_CREDENTIALS — цель не изменена" >&2
    exit 1
fi

# Токен шлюза: пустой, общеизвестный dev-дефолт или незаполненный плейсхолдер заменяется
# случайным. Шлюз с настоящим ключом dev-токен не принимает, а старый deploy/compose/.env
# (до сводок ИИ) этого ключа не содержит. Значение генерируется встроенным printf — не
# попадает ни в вывод, ни в argv дочерних процессов.
if ! grep -Eq '^LLM_GATEWAY_TOKEN=[^[:space:]]' "$TMP" \
    || grep -Eq '^LLM_GATEWAY_TOKEN=(dev-llm-token|__GEN_[A-Z0-9_]*__)[[:space:]]*$' "$TMP"; then
    grep -v '^LLM_GATEWAY_TOKEN=' "$TMP" > "$TMP2" || true
    printf 'LLM_GATEWAY_TOKEN=%s\n' "$(gen_value HEX)" >> "$TMP2"
    mv "$TMP2" "$TMP"
    echo "LLM_GATEWAY_TOKEN" >> "$UPDATED"
fi

chmod 600 "$TMP"
mv "$TMP" "$TARGET"
chmod 600 "$TARGET"
echo "llm-secret: ${TARGET#"$REPO_ROOT"/} — обновлены: $(tr '\n' ' ' < "$UPDATED")(значения не выводятся)"
