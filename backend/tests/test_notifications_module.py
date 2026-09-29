"""Модуль нотификаций: настройки-дефолты, фильтр доставки, коды привязки, правила."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import ApiError
from app.core.events import (
    EVENT_NOTIFICATION_TEST,
    NOTIFICATION_EVENT_TYPES,
    notification_template,
)
from app.models.service import Notification
from app.models.user import AppUser
from app.modules.notifications import prefs, service


class TestPrefsDefaults:
    def test_defaults_cover_event_registry(self) -> None:
        merged = prefs.merge_settings(None)
        assert set(merged["events"]) == set(NOTIFICATION_EVENT_TYPES)
        assert merged["version"] == 1
        assert merged["channels"]["in_app"]["enabled"] is True
        assert merged["channels"]["max"]["enabled"] is False

    def test_only_mine_flag_exists_only_where_meaningful(self) -> None:
        merged = prefs.merge_settings(None)
        assert merged["events"]["request.transitioned"]["only_mine"] is True
        assert "only_mine" not in merged["events"]["request.stuck"]

    def test_stored_overrides_and_garbage_dropped(self) -> None:
        stored = {
            "channels": {"telegram": {"enabled": False}, "fax": {"enabled": True}},
            "events": {
                "request.stuck": {"enabled": False},
                "unknown.event": {"enabled": True},
            },
            "version": 7,
        }
        merged = prefs.merge_settings(stored)
        assert merged["channels"]["telegram"]["enabled"] is False
        assert "fax" not in merged["channels"]
        assert merged["events"]["request.stuck"]["enabled"] is False
        assert "unknown.event" not in merged["events"]
        assert merged["version"] == 7

    def test_email_address_defaults_to_profile(self) -> None:
        user = AppUser(
            keycloak_sub=uuid.uuid4(), username="kam1", email="kam1@corp.local", roles=["kam"]
        )
        merged = prefs.merge_settings(None, user)
        assert merged["channels"]["email"]["address"] == "kam1@corp.local"
        assert merged["channels"]["telegram"]["linked"] is False


class TestDeliveryDecision:
    def test_event_disabled_drops_row(self) -> None:
        p = prefs.merge_settings({"events": {"request.stuck": {"enabled": False}}})
        assert (
            prefs.delivery_decision(
                p, event_type="request.stuck", channel="telegram", is_responsible=True
            )
            == "drop"
        )

    def test_channel_disabled_keeps_in_app_row(self) -> None:
        p = prefs.merge_settings({"channels": {"telegram": {"enabled": False}}})
        assert (
            prefs.delivery_decision(
                p, event_type="request.stuck", channel="telegram", is_responsible=True
            )
            == "skip_channel"
        )

    def test_only_mine_drops_for_foreign_deal(self) -> None:
        p = prefs.merge_settings(None)
        assert (
            prefs.delivery_decision(
                p,
                event_type="request.transitioned",
                channel="telegram",
                is_responsible=False,
            )
            == "drop"
        )
        assert (
            prefs.delivery_decision(
                p,
                event_type="request.transitioned",
                channel="telegram",
                is_responsible=True,
            )
            == "deliver"
        )

    def test_no_prefs_means_deliver(self) -> None:
        assert (
            prefs.delivery_decision(
                None, event_type="request.stuck", channel="telegram", is_responsible=None
            )
            == "deliver"
        )


class TestLinkCodeDegradation:
    """KEYDB_URL в тестах заведомо недоступен: код привязки обязан отвечать 503,
    а не молча выдавать непроверяемый код (Р-8: код живёт только в KeyDB)."""

    async def test_create_code_unavailable(self) -> None:
        user = AppUser(
            id=uuid.uuid4(), keycloak_sub=uuid.uuid4(), username="kam1", roles=["kam"]
        )
        with pytest.raises(ApiError) as exc_info:
            await service.create_link_code(user)
        assert exc_info.value.status_code == 503

    async def test_consume_code_unavailable(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            await service.consume_link_code("482913")
        assert exc_info.value.status_code == 503


class _FakeSession:
    """session.get всегда None — достаточно для веток валидации без БД."""

    async def get(self, model: object, pk: object) -> None:
        return None


class TestRuleValidation:
    async def test_full_valid_role_rule(self) -> None:
        payload = await service.validate_rule_payload(
            _FakeSession(),
            {
                "event_type": "request.stuck",
                "channel": "telegram",
                "recipient_type": "role",
                "recipient_value": "head_kam",
                "params": {"threshold_days": 10, "repeat_every_days": 3},
                "is_active": True,
            },
        )
        assert payload["recipient_value"] == "head_kam"
        assert payload["params"]["threshold_days"] == 10

    async def test_responsible_needs_no_value(self) -> None:
        payload = await service.validate_rule_payload(
            _FakeSession(),
            {
                "event_type": "request.transitioned",
                "channel": "telegram",
                "recipient_type": "responsible",
            },
        )
        assert payload["recipient_value"] is None

    async def test_tg_username_normalized(self) -> None:
        payload = await service.validate_rule_payload(
            _FakeSession(),
            {
                "event_type": "request.stuck",
                "channel": "telegram",
                "recipient_type": "tg_username",
                "recipient_value": "ivanov_p",
            },
        )
        assert payload["recipient_value"] == "@ivanov_p"

    async def test_unknown_event_rejected(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            await service.validate_rule_payload(
                _FakeSession(),
                {
                    "event_type": "deal.exploded",
                    "channel": "telegram",
                    "recipient_type": "responsible",
                },
            )
        assert exc_info.value.code == "validation_error"

    async def test_missing_recipient_value_rejected(self) -> None:
        with pytest.raises(ApiError):
            await service.validate_rule_payload(
                _FakeSession(),
                {
                    "event_type": "request.stuck",
                    "channel": "telegram",
                    "recipient_type": "role",
                },
            )

    async def test_threshold_bounds(self) -> None:
        with pytest.raises(ApiError):
            await service.validate_rule_payload(
                _FakeSession(),
                {
                    "event_type": "request.stuck",
                    "channel": "telegram",
                    "recipient_type": "responsible",
                    "params": {"threshold_days": 0},
                },
            )

    async def test_unknown_user_rejected(self) -> None:
        with pytest.raises(ApiError):
            await service.validate_rule_payload(
                _FakeSession(),
                {
                    "event_type": "request.stuck",
                    "channel": "telegram",
                    "recipient_type": "user",
                    "recipient_value": str(uuid.uuid4()),
                },
            )


class TestFeedReadMarkers:
    def _row(self, created_delta_minutes: int) -> Notification:
        row = Notification(
            id=uuid.uuid4(),
            event_type="request.stuck",
            channel="telegram",
            address="1",
            body="…",
        )
        row.created_at = datetime.now(timezone.utc) - timedelta(
            minutes=created_delta_minutes
        )
        return row

    def test_all_before_marks_older_rows(self) -> None:
        old, fresh = self._row(30), self._row(0)
        boundary = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        marker = {"read_ids": [], "all_before": boundary}
        assert service._is_read(old, marker) is True
        assert service._is_read(fresh, marker) is False

    def test_explicit_ids(self) -> None:
        row = self._row(5)
        marker = {"read_ids": [str(row.id)], "all_before": None}
        assert service._is_read(row, marker) is True

    def test_serialization_shape(self) -> None:
        row = self._row(0)
        item = service.serialize_feed_item(row, read=False)
        assert item["event"] == "request.stuck"
        assert item["read"] is False
        assert item["id"] == str(row.id)


class TestBotHelpers:
    def test_role_labels_cover_people_roles(self) -> None:
        from app.core.security import PEOPLE_ROLES

        assert set(service.ROLE_LABELS) == set(PEOPLE_ROLES)

    def test_bot_scope(self) -> None:
        kam = AppUser(keycloak_sub=uuid.uuid4(), username="k", roles=["kam"])
        head = AppUser(keycloak_sub=uuid.uuid4(), username="h", roles=["head_kam"])
        assert service._bot_scope_is_all(kam, stuck_only=True) is False
        assert service._bot_scope_is_all(head, stuck_only=True) is True
        assert service._bot_scope_is_all(head, stuck_only=False) is False


class TestTestMessageTemplate:
    def test_template_registered(self) -> None:
        assert notification_template(EVENT_NOTIFICATION_TEST) == "test_message"
