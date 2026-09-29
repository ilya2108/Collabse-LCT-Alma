"""Юнит-тесты guard'ов перехода и каскада порогов зависания (без БД).

Форсируются только граф + allowed_roles + requires_comment (Р-4);
каскад порога — rule → stage → workflow → app_setting (Р-5), эскалация 1.5× (Р-6).
"""

from __future__ import annotations

import uuid

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


class TestWriteAccess:
    def test_admin_and_head_kam_write_any(self) -> None:
        assert user_can_write_deal(["admin"], OTHER_ID, KAM_ID)
        assert user_can_write_deal(["head_kam"], OTHER_ID, KAM_ID)

    def test_kam_writes_only_own(self) -> None:
        assert user_can_write_deal(["kam"], KAM_ID, KAM_ID)
        assert not user_can_write_deal(["kam"], OTHER_ID, KAM_ID)

    def test_observer_never_writes(self) -> None:
        assert not user_can_write_deal(["observer"], KAM_ID, KAM_ID)


class TestRoleGuard:
    def test_empty_allowed_roles_means_any(self) -> None:
        assert transition_role_allowed(["kam"], [])
        assert transition_role_allowed(["kam"], None)

    def test_role_must_intersect(self) -> None:
        assert transition_role_allowed(["kam"], ["kam", "head_kam", "admin"])
        assert not transition_role_allowed(["kam"], ["head_kam", "admin"])  # tr-5 из seed


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
