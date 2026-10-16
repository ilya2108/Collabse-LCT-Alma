"""Бюджет соединений PostgreSQL по манифестам (отчёт нагрузки, §5: 89 × 500 TooManyConnections).

Пул SQLAlchemy — на каждый процесс uvicorn, а процессов на под WEB_CONCURRENCY, поэтому:
(maxReplicas + maxSurge) × воркеры × (pool + overflow) + Keycloak + Jobs + резерв
суперпользователя < max_connections. Числа читаются из тех же файлов, что применяет
`kubectl apply -k`, и из умолчаний Settings — правка одного места без другого ломает тест.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "deploy" / "k8s" / "base"
JOBS_RESERVE = 6 + 1 + 2  # seed-demo (пул по умолчанию) + db-migrate + demo-reset
SUPERUSER_RESERVED = 3  # superuser_reserved_connections PostgreSQL по умолчанию


def _load(path: Path) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def _container(doc: dict[str, Any], name: str) -> dict[str, Any]:
    spec = doc["spec"]["template"]["spec"]
    return next(c for c in spec["containers"] if c["name"] == name)


def _env(container: dict[str, Any]) -> dict[str, str]:
    return {item["name"]: str(item.get("value", "")) for item in container.get("env", [])}


def _max_connections() -> int:
    (statefulset,) = _load(BASE / "postgres" / "statefulset.yaml")
    args = " ".join(_container(statefulset, "postgres").get("args", []))
    match = re.search(r"max_connections=(\d+)", args)
    return int(match.group(1)) if match else 100


def _keycloak_pool() -> int:
    (deployment,) = _load(BASE / "keycloak" / "deployment.yaml")
    return int(_env(_container(deployment, "keycloak")).get("KC_DB_POOL_MAX_SIZE", "100"))


def _backend_pool() -> tuple[int, int]:
    (configmap,) = _load(BASE / "config" / "app-configmap.yaml")
    return int(configmap["data"]["DB_POOL_SIZE"]), int(configmap["data"]["DB_MAX_OVERFLOW"])


def _workers() -> int:
    dockerfile = (REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")
    cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD")]
    assert cmd and "--workers" not in cmd[-1], "число воркеров — только WEB_CONCURRENCY"
    match = re.search(r"WEB_CONCURRENCY=(\d+)", dockerfile)
    assert match, "в Dockerfile задан WEB_CONCURRENCY"
    return int(match.group(1))


def _max_replicas(hpa_path: Path) -> int:
    (hpa,) = _load(hpa_path)
    return int(hpa["spec"]["maxReplicas"])


def _budget(max_replicas: int) -> int:
    pool, overflow = _backend_pool()
    surge = math.ceil(max_replicas * 0.25)  # RollingUpdate по умолчанию: maxSurge 25%
    backend = (max_replicas + surge) * _workers() * (pool + overflow)
    return backend + _keycloak_pool() + JOBS_RESERVE + SUPERUSER_RESERVED


@pytest.mark.parametrize(
    "hpa",
    [BASE / "backend" / "hpa.yaml", REPO / "deploy/k8s/overlays/vm/patches/hpa-vm.yaml"],
    ids=["base", "vm"],
)
def test_budget_fits_max_connections(hpa: Path) -> None:
    assert _budget(_max_replicas(hpa)) < _max_connections()


def test_settings_defaults_match_manifests() -> None:
    defaults = Settings.model_fields
    assert (defaults["db_pool_size"].default, defaults["db_max_overflow"].default) == (
        _backend_pool()
    )


def test_old_arithmetic_would_fail() -> None:
    """Состояние прогона 16.09: пул 10 + 10, max_connections 100 — 6 × 2 × 20 = 240."""
    assert 6 * _workers() * (10 + 10) > 100
    assert _keycloak_pool() <= 10


def test_shared_configmap_does_not_set_workers() -> None:
    # crm-config читает и notification (строго один consumer Telegram) — воркеры не здесь
    (configmap,) = _load(BASE / "config" / "app-configmap.yaml")
    assert "WEB_CONCURRENCY" not in configmap["data"]
