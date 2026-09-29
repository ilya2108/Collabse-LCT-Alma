#!/usr/bin/env bash
# Идемпотентный bootstrap ГОЛОГО VPS (Ubuntu x86_64) под k3s.
# Запускается НА VPS от root. С локальной машины: make vm-install
# (ssh $VM_HOST 'bash -s' < deploy/scripts/vm-install.sh).
# Docker на VPS уже есть — он k3s не нужен (k3s несёт containerd),
# но docker-архивы образов импортируются напрямую: k3s ctr images import.
set -euo pipefail

SWAP_FILE="${SWAP_FILE:-/swapfile}"
SWAP_SIZE_MB="${SWAP_SIZE_MB:-2048}"

echo "== swap ${SWAP_SIZE_MB}M =="
if swapon --show --noheadings 2>/dev/null | grep -q .; then
    echo "swap уже включён:"
    swapon --show
else
    if [ ! -f "$SWAP_FILE" ]; then
        # fallocate быстрее; dd — фолбэк для ФС без поддержки (ext3 и т.п.)
        fallocate -l "${SWAP_SIZE_MB}M" "$SWAP_FILE" 2>/dev/null \
            || dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$SWAP_SIZE_MB" status=none
        chmod 600 "$SWAP_FILE"
        mkswap "$SWAP_FILE"
    fi
    swapon "$SWAP_FILE"
    echo "swap включён"
fi
if ! grep -qF "$SWAP_FILE" /etc/fstab; then
    echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
    echo "запись в /etc/fstab добавлена"
fi

echo "== k3s =="
if command -v k3s >/dev/null 2>&1; then
    echo "k3s уже установлен: $(k3s --version | head -1)"
    systemctl is-active --quiet k3s || systemctl start k3s
else
    # kubeconfig 644 — kubectl работает без sudo-плясок; traefik/servicelb
    # остаются включёнными (штатный ingress на 80/443 хоста)
    curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --write-kubeconfig-mode 644" sh -
fi

echo "== ожидание node Ready =="
deadline=$(( $(date +%s) + 300 ))
until k3s kubectl wait --for=condition=Ready node --all --timeout=10s >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "vm-install: node не стал Ready за 5 минут" >&2
        k3s kubectl get node || true
        exit 1
    fi
    echo "  ...жду node Ready"
    sleep 5
done
k3s kubectl get node
echo "vm-install: готово (дальше — make vm-deploy с локальной машины)"
