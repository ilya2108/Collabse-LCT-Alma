"""Юнит-тесты guard'ов перехода и каскада порогов зависания (без БД).

Форсируются только граф + allowed_roles + requires_comment (Р-4);
каскад порога — rule → stage → workflow → app_setting (Р-5), эскалация 1.5× (Р-6).
Права на переход (F2): `requests:transition` в процессе заявки (чужая — ещё
`requests:write_any`) и роли пользователя, способные выполнять переходы в процессе,
пересекаются с allowed_roles ребра — так же проверяет движок (`transition_deal`).
"""

from __future__ import annotations

import uuid

import pytest

from app.core.access import RoleCatalog, RoleGrant, compute_access, static_catalog
from app.core.rbac_catalog import SYSTEM_ROLES, WORKFLOW_TYPES
from app.models.service import NotificationRule
from app.modules.workflow.service import (
    comment_satisfies,
    effective_stuck_threshold,
    escalation_after_days,
    transition_role_allowed,
    user_can_write_deal,
)

KAM_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()

KAM = SYSTEM_ROLES["kam"].permissions
SEED_ROLES = ["kam", "head_kam", "admin"]  # allowed_roles переходов из сида
CONTRACT_SIGN_ROLES = ["head_kam", "admin"]  # tr-5 b2b: подписание договора


def _catalog(*grants: RoleGrant) -> RoleCatalog:
    roles = dict(static_catalog().roles)
    roles.update({grant.code: grant for grant in grants})
    return RoleCatalog(roles=roles, version=1, loaded_at=0.0)


def _grant(code: str, workflow_types: tuple[str, ...], permissions=KAM) -> RoleGrant:  # noqa: ANN001
    return RoleGrant(
        code=code,
        name=code,
        is_system=False,
        permissions=frozenset(permissions),
        workflow_types=frozenset(workflow_types),
        record_scope="all",
    )


DEMO = _catalog(
    _grant("kam_b2b", ("b2b",)),
    _grant("kam_b2c", ("b2c",)),
    _grant("viewer", WORKFLOW_TYPES, {"requests:read"}),
)


def engine_allows(
    roles: list[str],
    *,
    pipeline: str,
    allowed_roles: list[str] | None,
    own: bool = True,
    catalog: RoleCatalog = DEMO,
) -> bool:
    """Повтор проверок `transition_deal` по правам (без БД)."""
    access = compute_access(roles, catalog)
    responsible = KAM_ID if own else OTHER_ID
    return access.can_act_on_deal(
        "requests:transition", pipeline=pipeline, responsible_user_id=responsible, user_id=KAM_ID
    ) and transition_role_allowed(access.transition_roles(pipeline), allowed_roles)


class TestWriteAccess:
    def test_admin_and_head_kam_write_any(self) -> None:
        assert user_can_write_deal(["admin"], OTHER_ID, KAM_ID)
        assert user_can_write_deal(["head_kam"], OTHER_ID, KAM_ID)

    def test_kam_writes_only_own(self) -> None:
        assert user_can_write_deal(["kam"], KAM_ID, KAM_ID)
        assert not user_can_write_deal(["kam"], OTHER_ID, KAM_ID)

    def test_observer_never_writes(self) -> None:
        assert not user_can_write_deal(["observer"], KAM_ID, KAM_ID)

    @pytest.mark.parametrize("pipeline", WORKFLOW_TYPES)
    def test_wrapper_is_static_catalog_of_system_roles(self, pipeline: str) -> None:
        # обёртка совместимости: системные роли действуют в обоих процессах
        assert user_can_write_deal(["kam"], KAM_ID, KAM_ID, pipeline=pipeline)
        assert not user_can_write_deal(["kam"], OTHER_ID, KAM_ID, pipeline=pipeline)
        # своих ролей статический каталог не знает — правки нет
        assert not user_can_write_deal(["kam_b2c"], KAM_ID, KAM_ID, pipeline=pipeline)


class TestRoleGuard:
    def test_empty_allowed_roles_means_any(self) -> None:
        assert transition_role_allowed(["kam"], [])
        assert transition_role_allowed(["kam"], None)

    def test_role_must_intersect(self) -> None:
        assert transition_role_allowed(["kam"], ["kam", "head_kam", "admin"])
        assert not transition_role_allowed(["kam"], ["head_kam", "admin"])  # tr-5 из seed

    def test_accepts_transition_roles_set(self) -> None:
        assert transition_role_allowed(frozenset({"kam_b2c"}), ["kam", "kam_b2c"])
        assert not transition_role_allowed(frozenset(), ["kam"])


class TestEngineTransitionAccess:
    """Таблица истинности проверки движка: базовые роли — как прежде, свои — по процессу."""

    @pytest.mark.parametrize("pipeline", WORKFLOW_TYPES)
    @pytest.mark.parametrize("own", [True, False])
    @pytest.mark.parametrize("role", ["admin", "head_kam", "kam", "observer"])
    def test_base_roles_unchanged(self, role: str, own: bool, pipeline: str) -> None:
        for allowed in (SEED_ROLES, CONTRACT_SIGN_ROLES, []):
            legacy = user_can_write_deal(
                [role], KAM_ID, KAM_ID if own else OTHER_ID
            ) and transition_role_allowed([role], allowed)
            assert (
                engine_allows(
                    [role],
                    pipeline=pipeline,
                    allowed_roles=allowed,
                    own=own,
                    catalog=static_catalog(),
                )
                is legacy
            )

    def test_custom_role_needs_its_code_in_allowed_roles(self) -> None:
        # kam_b2c на переходе сида (только системные роли) — запрет; после сида демо-ролей
        # (код добавлен в allowed_roles) — можно
        assert not engine_allows(["kam_b2c"], pipeline="b2c", allowed_roles=SEED_ROLES)
        assert engine_allows(["kam_b2c"], pipeline="b2c", allowed_roles=[*SEED_ROLES, "kam_b2c"])
        assert engine_allows(["kam_b2c"], pipeline="b2c", allowed_roles=[])

    def test_custom_role_outside_its_process(self) -> None:
        # код роли в allowed_roles B2B не помогает: процесса нет в области роли
        assert not engine_allows(["kam_b2c"], pipeline="b2b", allowed_roles=["kam_b2c"])
        assert not engine_allows(["kam_b2c"], pipeline="b2b", allowed_roles=[])

    def test_union_of_process_roles_uses_role_of_that_process(self) -> None:
        roles = ["kam_b2b", "kam_b2c"]
        assert engine_allows(roles, pipeline="b2b", allowed_roles=["kam_b2b"])
        assert not engine_allows(roles, pipeline="b2b", allowed_roles=["kam_b2c"])
        assert engine_allows(roles, pipeline="b2c", allowed_roles=["kam_b2c"])

    def test_role_without_transition_right(self) -> None:
        # роль указана в переходе, но права на переходы у неё нет (сняли в редакторе)
        assert not engine_allows(["viewer"], pipeline="b2b", allowed_roles=["viewer"])
        assert not engine_allows(["viewer"], pipeline="b2b", allowed_roles=[])

    def test_foreign_deal_needs_write_any(self) -> None:
        assert not engine_allows(["kam_b2c"], pipeline="b2c", allowed_roles=[], own=False)
        assert engine_allows(["head_kam"], pipeline="b2c", allowed_roles=[], own=False)


class TestCommentGuard:
    def test_not_required(self) -> None:
        assert comment_satisfies(False, None)
        assert comment_satisfies(False, "")

    def test_required_rejects_empty(self) -> None:
        assert not comment_satisfies(True, None)
        assert not comment_satisfies(True, "")
        assert not comment_satisfies(True, "   ")

    def test_required_accepts_text(self) -> None:
        assert comment_satisfies(True, "Возврат: не хватает реквизитов")


class TestStuckCascade:
    def test_rule_overrides_everything(self) -> None:
        rule = NotificationRule(
            event_type="request.stuck",
            channel="telegram",
            recipient_type="responsible",
            params={"threshold_days": 10},
        )
        assert effective_stuck_threshold(rule, 7, 14, 14) == 10

    def test_stage_over_workflow(self) -> None:
        assert effective_stuck_threshold(None, 7, 14, 14) == 7

    def test_workflow_over_global(self) -> None:
        assert effective_stuck_threshold(None, None, 21, 14) == 21

    def test_global_default_last(self) -> None:
        assert effective_stuck_threshold(None, None, None, 14) == 14

    def test_rule_with_invalid_value_falls_through(self) -> None:
        rule = NotificationRule(
            event_type="request.stuck",
            channel="telegram",
            recipient_type="responsible",
            params={"threshold_days": 0},  # вне диапазона 1..60
        )
        assert effective_stuck_threshold(rule, None, 14, 14) == 14


class TestEscalation:
    def test_escalation_is_ceil_1_5x(self) -> None:
        assert escalation_after_days(14) == 21
        assert escalation_after_days(7) == 11  # 10.5 → вверх до дней
        assert escalation_after_days(1) == 2
