"""Смоук приложения: маппинг моделей, реестр роутов, health, 401-конверт.

Реестр включает маршруты доработок: отчёт «Активность КАМов» и файлы заявки (F1), роли и
права (F2), очистку ключей идемпотентности (F4), волну 4 — «Взаимодействия за период» и
передачу портфеля КАМа, карточку «Сигналы» дашборда. Перенос заявок (F3) новых маршрутов
не добавляет — он идёт через прежние `/workflows/*/changes`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import configure_mappers

import app.models  # noqa: F401 — регистрирует все модели
from app.main import app as fastapi_app

EXPECTED_ROUTES = [
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/universities"),
    ("POST", "/api/v1/universities"),
    ("PATCH", "/api/v1/universities/{university_id}"),
    ("GET", "/api/v1/universities/{university_id}/contacts"),
    ("GET", "/api/v1/contracts"),
    ("POST", "/api/v1/contracts"),
    ("GET", "/api/v1/products"),
    ("GET", "/api/v1/programs"),
    ("PATCH", "/api/v1/programs/{program_id}/priority"),
    ("POST", "/api/v1/programs/priorities/reorder"),
    ("GET", "/api/v1/requests"),
    ("POST", "/api/v1/requests/{request_id}/transitions"),
    ("POST", "/api/v1/requests/{request_id}/reopen"),
    # волна 4: передача портфеля КАМа (заявки и вузы)
    ("POST", "/api/v1/requests/transfer-portfolio"),
    # F1: файлы заявки
    ("GET", "/api/v1/requests/{request_id}/files"),
    ("POST", "/api/v1/requests/{request_id}/files"),
    ("DELETE", "/api/v1/requests/{request_id}/files/{file_id}"),
    ("GET", "/api/v1/workflows"),
    ("POST", "/api/v1/workflows/{workflow_id}/changes"),
    ("POST", "/api/v1/workflows/changes/{change_id}/approve"),
    ("POST", "/api/v1/import/sessions"),
    ("POST", "/api/v1/export/jobs"),
    ("GET", "/api/v1/reports/dashboard"),
    # карточка «Сигналы» на «Аналитике»
    ("GET", "/api/v1/reports/signals"),
    # F1: активность КАМов
    ("GET", "/api/v1/reports/kam-activity"),
    ("GET", "/api/v1/reports/kam-activity/summary"),
    # волна 4: «Взаимодействия за период» (ТЗ ФТ4)
    ("GET", "/api/v1/reports/interactions"),
    # «Просрочки»
    ("GET", "/api/v1/reports/sla"),
    ("GET", "/api/v1/reports/sla/kams/{kam_id}"),
    # «Сводка ИИ»
    ("POST", "/api/v1/reports/ai-summaries"),
    ("GET", "/api/v1/reports/ai-summaries/{job_id}"),
    ("GET", "/api/v1/students"),
    ("POST", "/api/v1/students/{student_id}/reveal"),
    ("GET", "/api/v1/activities"),
    ("GET", "/api/v1/notifications"),
    ("POST", "/api/v1/notifications/telegram/link-code"),
    ("POST", "/api/v1/internal/bot/bind"),
    ("GET", "/api/v1/files/{file_id}/download"),
    ("POST", "/api/v1/integrations/lms/webhook"),
    ("POST", "/api/v1/integrations/cms/webhook"),
    ("GET", "/api/v1/admin/users"),
    ("GET", "/api/v1/admin/audit-log"),
    # F2: роли и права
    ("GET", "/api/v1/roles"),
    ("GET", "/api/v1/admin/permissions"),
    ("GET", "/api/v1/admin/roles"),
    ("POST", "/api/v1/admin/roles"),
    ("GET", "/api/v1/admin/roles/{role_id}"),
    ("PATCH", "/api/v1/admin/roles/{role_id}"),
    ("DELETE", "/api/v1/admin/roles/{role_id}"),
    ("POST", "/api/v1/admin/roles/{role_id}/reset"),
    ("PUT", "/api/v1/admin/users/{user_id}/roles"),
    ("POST", "/api/v1/internal/jobs/check-stuck-requests"),
    # F4: очистка просроченных ключей идемпотентности (CronJob)
    ("POST", "/api/v1/internal/jobs/purge-idempotency-keys"),
    ("GET", "/api/v1/events/stream"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/healthz/live"),
    ("GET", "/healthz/ready"),
]


def _registered_routes() -> set[tuple[str, str]]:
    """Собирает (method, path) и из классических APIRoute, и из ленивых include
    FastAPI >= 0.141 (_IncludedRouter.effective_route_contexts)."""
    result: set[tuple[str, str]] = set()
    for route in fastapi_app.routes:
        if getattr(route, "path", None) and hasattr(route, "methods"):
            result.update((m, route.path) for m in route.methods)
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                result.update((m, ctx.path) for m in ctx.methods or ())
    return result


def test_all_models_map_cleanly() -> None:
    # ловит ошибки конфигурации мапперов (relationships, version_id_col=xmin) на импорте
    configure_mappers()


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_route_registered(method: str, path: str) -> None:
    assert (method, path) in _registered_routes()


def test_healthz_ok_without_dependencies() -> None:
    with TestClient(fastapi_app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/auth/me"),
        # новые маршруты для людей тоже закрыты входом (права проверяются после него)
        ("GET", "/api/v1/roles"),
        ("GET", "/api/v1/admin/roles"),
        ("GET", "/api/v1/reports/kam-activity"),
        ("GET", "/api/v1/reports/signals"),
        ("GET", "/api/v1/reports/interactions"),
        ("GET", "/api/v1/reports/sla"),
        ("POST", "/api/v1/reports/ai-summaries"),
        ("POST", "/api/v1/requests/transfer-portfolio"),
        ("GET", "/api/v1/requests/00000000-0000-0000-0000-000000000000/files"),
    ],
)
def test_route_requires_token(method: str, path: str) -> None:
    with TestClient(fastapi_app) as client:
        response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "job",
    ["check-stuck-requests", "purge-idempotency-keys"],
)
def test_internal_job_requires_internal_token(job: str) -> None:
    path = f"/api/v1/internal/jobs/{job}"
    # raise_server_exceptions=False: без PG вызов с верным токеном отвечает 500,
    # а не пробрасывает OSError в тест
    with TestClient(fastapi_app, raise_server_exceptions=False) as client:
        no_token = client.post(path)
        wrong = client.post(path, headers={"X-Internal-Token": "wrong"})
        ok = client.post(path, headers={"X-Internal-Token": "test-internal-token"})
    assert no_token.status_code == 401
    assert no_token.json()["error"]["code"] == "unauthorized"
    assert wrong.status_code == 401
    # верный токен проходит аутентификацию; без PG в юнит-окружении логика отвечает 500,
    # с поднятой БД — 200 с отчётом ({checked, stuck_found, ...} / {purged, duration_ms})
    assert ok.status_code in (200, 500)
