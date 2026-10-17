"""Readiness зависит только от PostgreSQL: отказ общего KeyDB не снимает поды с балансировки.

Весь набор тестов и так идёт с заведомо недоступным KeyDB (tests/conftest.py), здесь —
явная фиксация контракта /healthz/ready (api-contract.md §1.7).
"""

from __future__ import annotations

import pytest
from starlette.responses import Response

from app.core import health


class _Cache:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    async def ping(self) -> bool:
        return self._ok


def _patch(monkeypatch: pytest.MonkeyPatch, *, pg: bool, keydb: bool) -> None:
    async def check_postgres() -> bool:
        return pg

    monkeypatch.setattr(health, "_check_postgres", check_postgres)
    monkeypatch.setattr(health, "get_cache", lambda: _Cache(keydb))


@pytest.mark.parametrize(
    ("pg", "keydb", "code", "status"),
    [
        (True, True, 200, "ok"),
        (True, False, 200, "degraded"),  # кэш упал — CRM работает, мониторинг видит
        (False, True, 503, "unavailable"),
        (False, False, 503, "unavailable"),
    ],
)
async def test_readiness_depends_only_on_postgres(
    monkeypatch: pytest.MonkeyPatch, pg: bool, keydb: bool, code: int, status: str
) -> None:
    _patch(monkeypatch, pg=pg, keydb=keydb)
    response = Response()
    body = await health.readiness(response)
    assert response.status_code == code
    assert body == {"status": status, "checks": {"postgres": pg, "keydb": keydb}}


def test_readiness_probe_paths_registered() -> None:
    paths = {getattr(route, "path", None) for route in health.router.routes}
    assert {"/readyz", "/healthz/ready"} <= paths
