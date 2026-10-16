"""Регрессия F2: доступ 4 базовых ролей к КАЖДОМУ эндпоинту не изменился.

`LEGACY_ENDPOINT_ROLES` — снимок прежней матрицы доступа по ролям (колонка «Роли сейчас»
таблицы §4.4 спецификации F2), по строке на эндпоинт, пути без `/api/v1`. Тест обходит
все маршруты приложения (включая ленивые include FastAPI ≥ 0.141), собирает права из
зависимостей (`require_permission(...).required_permission`) и сверяет доступ каждой
базовой роли по статическому каталогу со снимком. Новые маршруты (F1, F2, F3) обязаны
объявить ожидаемый доступ базовых ролей в `NEW_ENDPOINT_ROLES`.

Вторая часть — таблицы истинности проверок ВНУТРИ обработчиков: прежние условия по
ролям переписаны здесь лямбдами и сверяются с новыми условиями по правам.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant

from app.core.access import compute_access, static_catalog
from app.core.errors import ApiError
from app.core.rbac_catalog import PERMISSION_CODES, SYSTEM_ROLE_CODES, WORKFLOW_TYPES
from app.main import create_app
from app.models.service import FileObject
from app.models.user import AppUser
from app.modules.crm.deps import ensure_can_write_owned
from app.modules.files.service import ensure_can_download
from app.modules.notifications.service import _bot_scope_is_all
from app.modules.reporting.service import report_scope

A, H, K, OBS = "admin", "head_kam", "kam", "observer"
ALL = frozenset({A, H, K, OBS})
AHK = frozenset({A, H, K})
AH = frozenset({A, H})
AO = frozenset({A, OBS})
AHO = frozenset({A, H, OBS})
ADMIN = frozenset({A})

# --- снимок прежней матрицы (до F2) ------------------------------------------------------

LEGACY_ENDPOINT_ROLES: dict[tuple[str, str], frozenset[str]] = {
    # auth, ui
    ("GET", "/auth/me"): ALL,
    ("GET", "/events/stream"): ALL,
    ("GET", "/ui/presets"): ALL,
    ("POST", "/ui/presets"): ALL,
    ("PATCH", "/ui/presets/{preset_id}"): ALL,
    ("DELETE", "/ui/presets/{preset_id}"): ALL,
    ("GET", "/search"): ALL,
    ("GET", "/flags"): ALL,
    # админка
    ("GET", "/admin/users"): AH,
    ("PATCH", "/admin/users/{user_id}"): ADMIN,
    ("POST", "/admin/users/sync"): ADMIN,
    ("GET", "/admin/settings"): ADMIN,
    ("PUT", "/admin/settings"): ADMIN,
    ("GET", "/admin/feature-flags"): ADMIN,
    ("PATCH", "/admin/feature-flags/{name}"): ADMIN,
    ("GET", "/admin/audit-log"): AO,
    ("GET", "/admin/integration/events"): AO,
    ("POST", "/admin/integration/events/{event_id}/retry"): ADMIN,
    ("GET", "/admin/dictionaries"): ADMIN,
    ("GET", "/admin/dictionaries/{name}/items"): ADMIN,
    ("POST", "/admin/dictionaries/{name}/import"): ADMIN,
    ("POST", "/admin/dictionaries/{name}/items"): ADMIN,
    ("PATCH", "/admin/dictionaries/{name}/items/{item_id}"): ADMIN,
    ("DELETE", "/admin/dictionaries/{name}/items/{item_id}"): ADMIN,
    # договоры
    ("GET", "/contracts"): ALL,
    ("GET", "/contracts/{contract_id}"): ALL,
    ("GET", "/contracts/{contract_id}/files"): ALL,
    ("POST", "/contracts"): AHK,
    ("PATCH", "/contracts/{contract_id}"): AHK,
    ("POST", "/contracts/{contract_id}/files"): AHK,
    ("DELETE", "/contracts/{contract_id}"): ADMIN,
    ("DELETE", "/contracts/{contract_id}/files/{file_id}"): AH,
    # продукты
    ("GET", "/products"): ALL,
    ("GET", "/products/{product_id}"): ALL,
    ("POST", "/products"): AH,
    ("PATCH", "/products/{product_id}"): AH,
    ("DELETE", "/products/{product_id}"): ADMIN,
    # программы
    ("GET", "/programs"): ALL,
    ("GET", "/programs/{program_id}"): ALL,
    ("POST", "/programs"): AHK,
    ("PATCH", "/programs/{program_id}"): AHK,
    ("DELETE", "/programs/{program_id}"): ADMIN,
    ("PATCH", "/programs/{program_id}/priority"): AHK,
    ("POST", "/programs/priorities/reorder"): AHK,
    # вузы и контакты
    ("GET", "/universities"): ALL,
    ("GET", "/universities/{university_id}"): ALL,
    ("GET", "/universities/{university_id}/contacts"): ALL,
    ("POST", "/universities"): AHK,
    ("PATCH", "/universities/{university_id}"): AHK,
    ("POST", "/universities/{university_id}/contacts"): AHK,
    ("PATCH", "/universities/{university_id}/contacts/{contact_id}"): AHK,
    ("DELETE", "/universities/{university_id}"): ADMIN,
    ("DELETE", "/universities/{university_id}/contacts/{contact_id}"): AH,
    # файлы
    ("GET", "/files/{file_id}/download"): ALL,
    # импорт и экспорт
    ("POST", "/import/sessions"): AHK,
    ("GET", "/import/sessions"): AHK,
    ("GET", "/import/sessions/{session_id}"): AHK,
    ("PUT", "/import/sessions/{session_id}/mapping"): AHK,
    ("POST", "/import/sessions/{session_id}/validate"): AHK,
    ("POST", "/import/sessions/{session_id}/apply"): AHK,
    ("DELETE", "/import/sessions/{session_id}"): AHK,
    ("POST", "/export/jobs"): ALL,
    ("GET", "/export/jobs"): ALL,
    ("GET", "/export/jobs/{job_id}"): ALL,
    ("GET", "/export/jobs/{job_id}/download"): ALL,
    # уведомления
    ("GET", "/notifications/settings"): ALL,
    ("PUT", "/notifications/settings"): ALL,
    ("POST", "/notifications/test"): ALL,
    ("GET", "/notifications/channels"): ALL,
    ("GET", "/notifications"): ALL,
    ("POST", "/notifications/read"): ALL,
    ("POST", "/notifications/telegram/link-code"): ALL,
    ("DELETE", "/notifications/telegram/link"): ALL,
    ("GET", "/notifications/rules"): AH,
    ("POST", "/notifications/rules"): ADMIN,
    ("PATCH", "/notifications/rules/{rule_id}"): ADMIN,
    ("DELETE", "/notifications/rules/{rule_id}"): ADMIN,
    # отчёты
    ("GET", "/reports/funnel"): ALL,
    ("GET", "/reports/kam-workload"): ALL,
    ("GET", "/reports/stuck"): ALL,
    ("GET", "/reports/universities-coverage"): ALL,
    ("GET", "/reports/programs-demand"): ALL,
    ("GET", "/reports/talent-pool-funnel"): ALL,
    ("GET", "/reports/dynamics"): ALL,
    ("GET", "/reports/dashboard"): ALL,
    # заявки
    ("GET", "/requests"): ALL,
    ("GET", "/requests/board"): ALL,
    ("GET", "/requests/{request_id}"): ALL,
    ("GET", "/requests/{request_id}/transitions"): ALL,
    ("GET", "/requests/{request_id}/history"): ALL,
    ("GET", "/requests/{request_id}/comments"): ALL,
    ("POST", "/requests"): AHK,
    ("PATCH", "/requests/{request_id}"): AHK,
    ("POST", "/requests/{request_id}/assign"): AHK,
    ("POST", "/requests/{request_id}/transitions"): AHK,
    ("POST", "/requests/{request_id}/comments"): AHK,
    ("DELETE", "/requests/{request_id}/comments/{comment_id}"): AHK,
    ("POST", "/requests/{request_id}/reopen"): ADMIN,
    ("DELETE", "/requests/{request_id}"): ADMIN,
    # студенты и активности
    ("GET", "/students"): ALL,
    ("GET", "/students/{student_id}"): ALL,
    ("GET", "/students/{student_id}/history"): ALL,
    ("POST", "/students"): AHK,
    ("PATCH", "/students/{student_id}"): AHK,
    ("POST", "/students/{student_id}/status"): AHK,
    ("POST", "/students/{student_id}/reveal"): AHK,
    ("DELETE", "/students/{student_id}"): ADMIN,
    ("POST", "/students/{student_id}/files"): AHK,
    ("GET", "/students/{student_id}/files"): AHK,
    ("DELETE", "/students/{student_id}/files/{file_id}"): AH,
    ("GET", "/activities"): ALL,
    ("POST", "/activities"): AH,
    ("PATCH", "/activities/{activity_id}"): AH,
    ("DELETE", "/activities/{activity_id}"): AH,
    ("POST", "/activities/{activity_id}/participants"): AHK,
    ("DELETE", "/activities/{activity_id}/participants/{student_id}"): AHK,
    # конструктор процессов
    ("GET", "/workflows"): ALL,
    ("GET", "/workflows/{workflow_id}"): ALL,
    ("POST", "/workflows/{workflow_id}/impact-preview"): AH,
    ("POST", "/workflows/{workflow_id}/changes"): AH,
    ("GET", "/workflows/{workflow_id}/changes"): AHO,
    ("GET", "/workflows/changes/{change_id}"): AHO,
    ("POST", "/workflows/changes/{change_id}/approve"): ADMIN,
    ("POST", "/workflows/changes/{change_id}/reject"): ADMIN,
}

# --- новые маршруты: ожидаемый доступ базовых ролей ---------------------------------------

NEW_ENDPOINT_ROLES: dict[tuple[str, str], frozenset[str]] = {
    # F2: роли и права
    ("GET", "/roles"): ALL,
    ("GET", "/admin/permissions"): ADMIN,
    ("GET", "/admin/roles"): ADMIN,
    ("POST", "/admin/roles"): ADMIN,
    ("GET", "/admin/roles/{role_id}"): ADMIN,
    ("PATCH", "/admin/roles/{role_id}"): ADMIN,
    ("DELETE", "/admin/roles/{role_id}"): ADMIN,
    ("POST", "/admin/roles/{role_id}/reset"): ADMIN,
    ("PUT", "/admin/users/{user_id}/roles"): ADMIN,
    # F1: активность КАМов и файлы заявки
    ("GET", "/reports/kam-activity"): ALL,
    ("GET", "/reports/kam-activity/summary"): ALL,
    ("GET", "/reports/interactions"): ALL,  # «Взаимодействия за период» (ТЗ ФТ4)
    ("GET", "/requests/{request_id}/files"): ALL,
    ("POST", "/requests/{request_id}/files"): AHK,
    ("DELETE", "/requests/{request_id}/files/{file_id}"): AHK,
    # волна 4: передача портфеля КАМа (заявки — requests:assign_any, вузы —
    # universities:assign внутри обработчика)
    ("POST", "/requests/transfer-portfolio"): AH,
    # P1-1: справочник ИТ-направлений — читают все (фильтры отчётов, карточки программ и
    # продуктов), правит admin:dictionaries
    ("GET", "/it-directions"): ALL,
    ("GET", "/it-directions/{direction_id}"): ALL,
    ("POST", "/it-directions"): ADMIN,
    ("PATCH", "/it-directions/{direction_id}"): ADMIN,
    ("DELETE", "/it-directions/{direction_id}"): ADMIN,
}

_API_PREFIX = "/api/v1"


def _is_service_route(path: str) -> bool:
    """Не для людей: вебхуки (HMAC/svc_integration), /internal/* (токен или svc_*), health."""
    if not path.startswith(_API_PREFIX):
        return True
    rest = path[len(_API_PREFIX) :]
    return rest.startswith(("/internal/", "/health")) or (
        rest.startswith("/integrations/") and rest.endswith("/webhook")
    )


class RouteAccess:
    def __init__(self, method: str, path: str, dependant: Dependant) -> None:
        self.method = method
        self.path = path
        self.permissions: set[str] = set()
        # зависимости, проверяющие роли напрямую (атрибут `required_roles` прежней
        # зависимости по ролям, удалённой вместе с шимами): доступ задают только права
        self.role_dependencies: set[str] = set()
        self.authenticated = False
        self._collect(dependant)

    def _collect(self, dependant: Dependant) -> None:
        for dependency in dependant.dependencies:
            call: Any = dependency.call
            name = getattr(call, "__name__", "")
            if hasattr(call, "required_permission"):
                self.permissions.add(call.required_permission)
            if hasattr(call, "required_roles"):
                self.role_dependencies.add(name or repr(call))
            if name == "get_current_user":
                self.authenticated = True
            self._collect(dependency)

    def allows(self, role: str) -> bool:
        access = compute_access([role], static_catalog())
        return self.authenticated and all(access.has(permission) for permission in self.permissions)


def _iter_routes() -> Iterator[RouteAccess]:
    app = create_app()
    for route in app.routes:
        contexts: list[tuple[str, Any, Dependant]] = []
        if hasattr(route, "effective_route_contexts"):
            contexts = [
                (ctx.path, ctx.methods, ctx.dependant) for ctx in route.effective_route_contexts()
            ]
        elif hasattr(route, "dependant") and hasattr(route, "methods"):
            contexts = [(route.path, route.methods, route.dependant)]
        for path, methods, dependant in contexts:
            if _is_service_route(path):
                continue
            for method in sorted(methods or ()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                yield RouteAccess(method, path[len(_API_PREFIX) :], dependant)


ROUTES = list(_iter_routes())
EXPECTED = {**LEGACY_ENDPOINT_ROLES, **NEW_ENDPOINT_ROLES}


def _ids(route: RouteAccess) -> str:
    return f"{route.method} {route.path}"


def test_snapshot_size() -> None:
    # 124 эндпоинта для людей до F2 (оценка «132» в спецификации посчитана с запасом;
    # обход маршрутов ниже подтверждает, что снимок полный)
    assert len(LEGACY_ENDPOINT_ROLES) == 124


@pytest.mark.parametrize("route", ROUTES, ids=_ids)
def test_route_declared(route: RouteAccess) -> None:
    """(a) маршрут → объявление: новый эндпоинт обязан объявить доступ базовых ролей."""
    assert (route.method, route.path) in EXPECTED, (
        "новый маршрут без объявления в LEGACY_ENDPOINT_ROLES/NEW_ENDPOINT_ROLES"
    )


@pytest.mark.parametrize("route", ROUTES, ids=_ids)
def test_base_roles_access_unchanged(route: RouteAccess) -> None:
    """(b) доступ каждой базовой роли совпадает со снимком."""
    expected = EXPECTED.get((route.method, route.path))
    if expected is None:
        pytest.skip("не объявлен — см. test_route_declared")
    actual = frozenset(role for role in SYSTEM_ROLE_CODES if route.allows(role))
    assert actual == expected


@pytest.mark.parametrize("route", ROUTES, ids=_ids)
def test_no_role_dependencies(route: RouteAccess) -> None:
    """(c) доступ к маршрутам задают только права: проверок ролей в зависимостях нет."""
    assert not route.role_dependencies, (
        "проверка ролей в зависимости — используйте require_permission"
    )


@pytest.mark.parametrize("route", ROUTES, ids=_ids)
def test_permissions_known(route: RouteAccess) -> None:
    """(d) права маршрутов есть в каталоге; маршруты для людей требуют входа."""
    assert route.permissions <= PERMISSION_CODES
    assert route.authenticated, "маршрут для людей без get_current_user"


def test_every_human_route_found() -> None:
    found = {(route.method, route.path) for route in ROUTES}
    # снимок прежних эндпоинтов полностью присутствует в приложении
    assert set(LEGACY_ENDPOINT_ROLES) <= found


# --------------------------------------------------------------------------------------
# Проверки внутри обработчиков: прежние условия по ролям против новых по правам
# --------------------------------------------------------------------------------------

ROLES = SYSTEM_ROLE_CODES


def _user(role: str) -> AppUser:
    return AppUser(id=uuid.uuid4(), keycloak_sub=uuid.uuid4(), username=role, roles=[role])


def _access(role: str):  # noqa: ANN202
    return compute_access([role], static_catalog())


def _legacy_has_any_role(roles: list[str], allowed: tuple[str, ...]) -> bool:
    return bool(set(roles) & set(allowed))


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("pipeline", WORKFLOW_TYPES)
def test_assign_other_on_create(role: str, pipeline: str) -> None:
    # create_request: назначить ответственным другого — прежде admin/head_kam
    legacy = _legacy_has_any_role([role], (A, H))
    assert _access(role).has("requests:assign_any", pipeline) is legacy


@pytest.mark.parametrize("actor", (A, H, K))
@pytest.mark.parametrize("current", ROLES)
@pytest.mark.parametrize("to_self", [True, False])
def test_assign_free_request(actor: str, current: str, to_self: bool) -> None:
    def legacy(actor_roles: list[str], current_roles: list[str], to_self: bool) -> bool:
        if _legacy_has_any_role(actor_roles, (A, H)):
            return True
        if not to_self:
            return False
        return "kam" not in current_roles  # заявка «свободна»

    def new(actor_role: str, current_role: str, to_self: bool) -> bool:
        if _access(actor_role).has("requests:assign_any", "b2b"):
            return True
        if not to_self:
            return False
        return not _access(current_role).has("requests:kam", "b2b")

    assert new(actor, current, to_self) is legacy([actor], [current], to_self)


@pytest.mark.parametrize("role", (A, H, K))
@pytest.mark.parametrize("pipeline", WORKFLOW_TYPES)
def test_delete_foreign_comment(role: str, pipeline: str) -> None:
    assert _access(role).has("comments:delete_any", pipeline) is (role == A)


@pytest.mark.parametrize("role", (A, H, K))
@pytest.mark.parametrize("own", [True, False, None])
def test_write_foreign_university_or_contract(role: str, own: bool | None) -> None:
    user = _user(role)
    owner = None if own is None else (user.id if own else uuid.uuid4())
    legacy_allowed = _legacy_has_any_role([role], (A, H)) or owner is None or owner == user.id
    try:
        ensure_can_write_owned(user, owner)
        allowed = True
    except ApiError:
        allowed = False
    assert allowed is legacy_allowed


@pytest.mark.parametrize("role", ROLES)
def test_assign_university_kam(role: str) -> None:
    # смена КАМа вуза (PATCH/POST /universities, передача портфеля) — право Руководителя:
    # прежде КАМ мог сменить ответственного у своего или ничьего вуза — это и закрыто
    assert _access(role).has("universities:assign") is (role in (A, H))


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("pipeline", WORKFLOW_TYPES)
def test_transfer_portfolio_requests(role: str, pipeline: str) -> None:
    # передача портфеля переназначает заявки процесса — как POST /assign чужому
    assert _access(role).has("requests:assign_any", pipeline) is (role in (A, H))


_FILE_TYPES = ("contract", "student", "deal", "deal_comment", "import_session", "report", "other")


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("entity_type", _FILE_TYPES)
@pytest.mark.parametrize("own", [True, False])
def test_file_download(role: str, entity_type: str, own: bool) -> None:
    def legacy(roles: list[str], entity_type: str, own: bool) -> bool:
        is_admin = _legacy_has_any_role(roles, (A,))
        if entity_type in ("import_session", "report"):
            return is_admin or own
        observer_only = "observer" in roles and not _legacy_has_any_role(roles, (A, H, K))
        return not (
            observer_only and entity_type in ("contract", "student", "deal", "deal_comment")
        )

    user = _user(role)
    file_object = FileObject(
        id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        uploaded_by=user.id if own else uuid.uuid4(),
    )
    try:
        ensure_can_download(user, file_object)
        allowed = True
    except ApiError:
        allowed = False
    assert allowed is legacy([role], entity_type, own)


@pytest.mark.parametrize("role", ROLES)
def test_others_imports_and_exports_visibility(role: str) -> None:
    assert _access(role).has("files:others") is (role == A)


@pytest.mark.parametrize("role", (A, H, K))
def test_dictionary_import_via_wizard(role: str) -> None:
    assert _access(role).has("import:dictionaries") is (role == A)


@pytest.mark.parametrize("role", ROLES)
def test_client_pii_masking(role: str) -> None:
    # serializers.serialize_deal: прежде маскировалось при роли observer
    mask = not _access(role).has("clients:pii")
    assert mask is (role == OBS)


@pytest.mark.parametrize("role", ROLES)
def test_student_card_files(role: str) -> None:
    observer_only = role == OBS
    assert _access(role).has("students:files") is (not observer_only)


@pytest.mark.parametrize("role", ROLES)
def test_bot_scope_and_waiting_approval(role: str) -> None:
    user = _user(role)
    assert _bot_scope_is_all(user, stuck_only=True) is (role in (A, H, OBS))
    assert _bot_scope_is_all(user, stuck_only=False) is False
    # прежний is_manager (head_kam/admin) — счётчик пакетов на согласовании
    assert user.access.has("workflow:change") is (role in (A, H))


@pytest.mark.parametrize("role", ROLES)
def test_report_scope(role: str) -> None:
    user = _user(role)
    scope = report_scope(user)
    assert scope.workflow_types == WORKFLOW_TYPES
    assert scope.own_user_id == (user.id if role == K else None)


@pytest.mark.parametrize("role", (A, H))
@pytest.mark.parametrize("pipeline", WORKFLOW_TYPES)
def test_apply_now_of_package_author(role: str, pipeline: str) -> None:
    # прежде: безопасный пакет применялся сразу, если автор — admin
    assert _access(role).has("workflow:approve", pipeline) is (role == A)
