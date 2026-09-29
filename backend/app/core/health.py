"""Health-пробы (без аутентификации).

Канонические пути — api-contract.md §1.7 (`/healthz`, `/readyz`); k8s-манифесты
(deployment.md §3.3) ходят на `/healthz/live` и `/healthz/ready` — зарегистрированы
как алиасы, чтобы probes и контракт совпадали без переписывания манифестов.
Алиасы `/api/v1/health/*` — для проверки через ingress (crm.local маршрутизирует
на backend только префикс `/api`).

Liveness никогда не проверяет внешние зависимости (иначе падение PG каскадно
перезапускает поды); зависимостями управляет readiness.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.cache import get_cache
from app.core.db import get_engine

router = APIRouter(tags=["health"], include_in_schema=False)

_LIVE_PATHS = ("/healthz", "/healthz/live", "/health/live", "/api/v1/health/live")
_READY_PATHS = ("/readyz", "/healthz/ready", "/health/ready", "/api/v1/health/ready")


async def _select_one() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_postgres() -> bool:
    try:
        # asyncio.wait_for — совместимость с Python 3.10 (asyncio.timeout появился в 3.11)
        await asyncio.wait_for(_select_one(), timeout=3.0)
        return True
    except Exception:  # noqa: BLE001 — readiness должен пережить любую ошибку зависимости
        return False


async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def readiness(response: Response) -> dict[str, Any]:
    pg_ok = await _check_postgres()
    keydb_ok = await get_cache().ping()
    # KeyDB — необязательный уровень (cache-aside деградирует в PG), но контракт §1.7
    # включает его в readiness: под без кэша не должен принимать полный трафик
    ok = pg_ok and keydb_ok
    if not ok:
        response.status_code = 503
    return {
        "status": "ok" if ok else "unavailable",
        "checks": {"postgres": pg_ok, "keydb": keydb_ok},
    }


for path in _LIVE_PATHS:
    router.get(path)(liveness)
for path in _READY_PATHS:
    router.get(path)(readiness)
