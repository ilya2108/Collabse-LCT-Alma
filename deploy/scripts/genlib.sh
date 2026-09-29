#!/bin/sh
# Общая библиотека генерации секретов (POSIX sh: работает на macOS и Linux).
#
# Плейсхолдеры в *.example-файлах: __GEN_<KIND>_<NAME>__, где KIND:
#   FERNET — ключ Fernet (urlsafe base64, 32 байта) — шифрование ПДн, 152-ФЗ
#   B64    — 32 случайных байта в base64 (ключ HMAC blind index и т.п.)
#   HEX    — 32 байта hex (межсервисные токены, client secrets)
#   PWD    — 16 байт hex (пароли БД/учёток)
# Одинаковый плейсхолдер в одном файле получает одно и то же значение
# (например, POTGRES_PASSWORD и его вхождение в DATABASE_URL).

gen_value() {
    # $1 — KIND
    case "$1" in
        FERNET)
            # Fernet.generate_key() == urlsafe_b64encode(os.urandom(32));
            # python+cryptography предпочтителен, openssl — эквивалентный фолбэк
            if python3 -c 'import cryptography' 2>/dev/null; then
                python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
            else
                openssl rand 32 | base64 | tr '+/' '-_'
            fi
            ;;
        B64) openssl rand -base64 32 ;;
        HEX) openssl rand -hex 32 ;;
        PWD) openssl rand -hex 16 ;;
        *)
            echo "genlib: неизвестный тип плейсхолдера '$1'" >&2
            return 1
            ;;
    esac
}

fill_env_file() {
    # $1 — example-файл, $2 — целевой файл.
    # Существующий целевой файл НЕ перезаписывается: ключи шифрования ПДн
    # должны быть стабильны между повторными запусками make.
    example="$1"
    target="$2"
    if [ -f "$target" ]; then
        echo "  = $target уже существует — пропущен (значения стабильны)"
        return 0
    fi
    tmp="$(mktemp)"
    cp "$example" "$tmp"
    for ph in $(grep -o '__GEN_[A-Z0-9]*_[A-Z0-9]*__' "$example" | sort -u); do
        kind="$(printf '%s' "$ph" | sed 's/^__GEN_//; s/_[A-Z0-9]*__$//')"
        value="$(gen_value "$kind")" || return 1
        tmp2="$(mktemp)"
        sed "s|$ph|$value|g" "$tmp" > "$tmp2"
        mv "$tmp2" "$tmp"
    done
    mv "$tmp" "$target"
    chmod 600 "$target"
    echo "  + $target сгенерирован"
}
