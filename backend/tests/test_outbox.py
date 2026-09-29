"""Юнит-тесты outbox-инфраструктуры: ретраи, dedup зависших, конверты, HMAC."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core.events import notification_template
from app.core.outbox import (
    INTEGRATION_RETRY_DELAYS_SECONDS,
    build_send_envelope,
    next_integration_retry_delay,
    sign_payload,
    stuck_dedup_key,
)
from app.models.service import Notification
from app.modules.integrations.security import occurred_at_fresh, signature_valid


class TestRetrySchedule:
    """Backoff интеграций: 1м → 5м → 30м → 2ч → 6ч, затем DLQ (api-contract.md §13.7)."""

    def test_schedule_values(self) -> None:
        assert INTEGRATION_RETRY_DELAYS_SECONDS == (60, 300, 1800, 7200, 21600)

    def test_delays_by_attempt(self) -> None:
        assert next_integration_retry_delay(1) == 60
        assert next_integration_retry_delay(2) == 300
        assert next_integration_retry_delay(3) == 1800
        assert next_integration_retry_delay(4) == 7200
        assert next_integration_retry_delay(5) == 21600

    def test_exhausted_goes_to_dlq(self) -> None:
        assert next_integration_retry_delay(6) is None


class TestStuckDedup:
    """dedup_key = stuck:{deal}:{stage}:{level}:{bucket}, bucket = days // repeat (Р-6)."""

    def test_bucket_math(self) -> None:
        deal_id, stage_id = uuid.uuid4(), uuid.uuid4()
        key = stuck_dedup_key(deal_id, stage_id, "responsible", 15, 3)
        assert key == f"stuck:{deal_id}:{stage_id}:responsible:5"

    def test_same_bucket_same_key(self) -> None:
        deal_id, stage_id = uuid.uuid4(), uuid.uuid4()
        first = stuck_dedup_key(deal_id, stage_id, "manager", 15, 3)
        second = stuck_dedup_key(deal_id, stage_id, "manager", 17, 3)  # тот же бакет 5
        third = stuck_dedup_key(deal_id, stage_id, "manager", 18, 3)  # бакет 6
        assert first == second != third

    def test_levels_do_not_collide(self) -> None:
        deal_id, stage_id = uuid.uuid4(), uuid.uuid4()
        assert stuck_dedup_key(deal_id, stage_id, "responsible", 21, 3) != stuck_dedup_key(
            deal_id, stage_id, "manager", 21, 3
        )


class TestNotificationTemplates:
    def test_canonical_mapping(self) -> None:
        assert notification_template("request.stuck") == "request_stuck"
        assert notification_template("request.transitioned") == "request_transitioned"
        assert notification_template("workflow.changed") == "workflow_changed"
        assert notification_template("integration.lead_received") == "lead_received"

    def test_fallback_for_unknown_event(self) -> None:
        assert notification_template("lms.learning.completed") == "lms_learning_completed"


class TestSendEnvelope:
    def test_telegram_envelope(self) -> None:
        notification = Notification(
            id=uuid.uuid4(),
            event_type="request.stuck",
            channel="telegram",
            address="199401234",
            recipient_user_id=uuid.uuid4(),
            subject="Заявка зависла",
            body="🔥 …",
            payload={"request_id": "rq-1", "stuck_days": 15},
        )
        notification.created_at = datetime.now(timezone.utc)
        envelope = build_send_envelope(notification)
        assert envelope["notification_id"] == str(notification.id)
        assert envelope["event"] == "request.stuck"
        assert envelope["template"] == "request_stuck"
        recipient = envelope["recipients"][0]
        assert recipient["telegram_chat_id"] == 199401234
        assert recipient["channels"] == ["telegram"]
        assert envelope["payload"]["stuck_days"] == 15
        assert envelope["payload"]["body"] == "🔥 …"

    def test_email_envelope(self) -> None:
        notification = Notification(
            id=uuid.uuid4(),
            event_type="student.talent_pool_added",
            channel="email",
            address="head@corp.local",
            body="…",
        )
        notification.created_at = datetime.now(timezone.utc)
        recipient = build_send_envelope(notification)["recipients"][0]
        assert recipient["email"] == "head@corp.local"
        assert recipient["telegram_chat_id"] is None


class TestWebhookSecurity:
    def test_sign_and_verify_roundtrip(self) -> None:
        body = b'{"event_id": "x"}'
        header = sign_payload("secret", body)
        assert header.startswith("sha256=")
        assert signature_valid("secret", body, header)

    def test_wrong_secret_or_body_rejected(self) -> None:
        body = b'{"event_id": "x"}'
        header = sign_payload("secret", body)
        assert not signature_valid("other", body, header)
        assert not signature_valid("secret", b"{}", header)
        assert not signature_valid("secret", body, None)
        assert not signature_valid("secret", body, "md5=abc")

    def test_replay_window(self) -> None:
        now = datetime.now(timezone.utc)
        assert occurred_at_fresh(now - timedelta(minutes=9), now)
        assert not occurred_at_fresh(now - timedelta(minutes=11), now)
