#!/bin/sh
# Генерация deploy/compose/.env из deploy/compose/.env.example (цель `make dev-env`).
# Заполняются только PII-ключи и прочие __GEN_*__ плейсхолдеры; существующий
# .env не перезаписывается — ключ шифрования ПДн стабилен между запусками.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/genlib.sh"

COMPOSE_DIR="$SCRIPT_DIR/../compose"

echo "gen-dotenv: deploy/compose/.env"
fill_env_file "$COMPOSE_DIR/.env.example" "$COMPOSE_DIR/.env"
