"""Транзакционные outbox'ы и relay-доставка (data-model.md §9.3–9.4, api-contract.md §13.7).

Три журнала пишутся В ОДНОЙ транзакции с бизнес-изменением:
- `notification` — получатели вычисляются backend'ом по `notification_rule`;
  relay доставляет в notification-service `POST :8010/api/v1/send`
  (X-Internal-Token, идемпотентность по `notification_id`). Получатели правила `role`
  (`role_rule_members`) — активные пользователи с ролью, видящие заявку события
  (область B2B/B2C и «только свои», F2);
- `integration_event` (outbound) — конверт IntegrationEvent; outbox в PG — источник
  истины, доставку выполняет транспорт `IntegrationTransport` (app.core.integration_transport,
  сейчас HTTP + HMAC к заглушкам; корпоративная шина — новая реализация того же протокола),
  ретраи 1м → 5м → 30м → 2ч → 6ч (только сеть/5xx), 4xx и исчерпание попыток → DLQ
  (`status='dead'`), просмотр/ручной retry — в админке;
- `audit_log` — см. app.core.audit.

Relay-воркеры — asyncio-таски в lifespan приложения (период 5 с); принудительный
прогон — `POST /internal/jobs/outbox-relay`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ensure_role_catalog
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.events import (
    EVENT_INTEGRATION_DELIVERY_FAILED,
    SSE_NOTIFICATION_CREATED,
    notification_template,
)
from app.core.integration_transport import (
    HttpIntegrationTransport,
    IntegrationTransport,
    sign_payload,  # noqa: F401 — реэкспорт (§13.6)
)
from app.core.sse import publish_event
from app.models.deal import Deal
from app.models.service import ExternalLink, IntegrationEvent, Notification, NotificationRule
from app.models.user import AppUser

logger = logging.getLogger("app.outbox")

# расписание ретраев интеграций (api-contract.md §13.7): 1м → 5м → 30м → 2ч → 6ч
INTEGRATION_RETRY_DELAYS_SECONDS: tuple[int, ...] = (60, 300, 1800, 7200, 21600)

# нотификации: relay ретраит pending каждый проход; после стольких неудач — failed.
# Лимит расходуют только НЕтранзиентные ошибки (HTTP 4xx — битый конверт): сетевые
# сбои и 5xx/429 (обычный rolling-restart notification-service длится дольше, чем
# 5 проходов × 5 секунд) оставляют строку pending до восстановления сервиса
NOTIFICATION_MAX_ATTEMPTS = 5

RELAY_INTERVAL_SECONDS = 5.0
RELAY_BATCH_SIZE = 50


def next_integration_retry_delay(failed_attempts: int) -> int | None:
    """Задержка перед следующей попыткой после `failed_attempts` неудач (1-based).

    None — попытки исчерпаны (событие уходит в DLQ).
    """
    if failed_attempts < 1:
        return INTEGRATION_RETRY_DELAYS_SECONDS[0]
    if failed_attempts > len(INTEGRATION_RETRY_DELAYS_SECONDS):
        return None
    return INTEGRATION_RETRY_DELAYS_SECONDS[failed_attempts - 1]


def stuck_dedup_key(
    deal_id: uuid.UUID, stage_id: uuid.UUID, level: str, days_on_stage: int, repeat_every_days: int
) -> str:
    """`stuck:{deal}:{stage}:{level}:{bucket}` — повтор не чаще repeat_every_days (Р-6)."""
    bucket = days_on_stage // max(repeat_every_days, 1)
    return f"stuck:{deal_id}:{stage_id}:{level}:{bucket}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Outbox нотификаций: вычисление получателей и запись строк
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectRecipient:
    """Получатель без правила (например, ответственный при LMS-событии)."""

    user: AppUser
    channel: str = "telegram"


def _address_for(user: AppUser, channel: str) -> str | None:
    """Вычисленный адрес доставки; None — канал для пользователя не настроен."""
    if channel == "telegram":
        return str(user.tg_chat_id) if user.tg_chat_id else None
    if channel in ("email", "max"):
        return user.email
    return None


def sees_deal(user: AppUser, pipeline: str, responsible_user_id: uuid.UUID | None) -> bool:
    """Заявка процесса `pipeline` с этим ответственным — в области пользователя
    (`requests:read` в процессе; при «только свои» — он ответственный). Права — по снимку
    каталога ролей: вызывающий заранее делает `ensure_role_catalog`."""
    return user.access.deal_scope("requests:read", user.id).allows(pipeline, responsible_user_id)


async def role_rule_members(
    session: AsyncSession,
    role_code: str,
    *,
    pipeline: str | None,
    responsible_user_id: uuid.UUID | None = None,
) -> list[AppUser]:
    """Получатели правила `role`: активные пользователи с ролью `role_code`.

    Процесс события известен (`pipeline`) — только те, у кого `requests:read` в нём
    (роль «КАМ B2C» не получает уведомления о заявках B2B); известен и ответственный —
    ещё и с учётом «только свои» (заявка в области получателя, `sees_deal`). Общая функция
    поштучных уведомлений (`_resolve_rule_users`) и сводок переноса заявок
    (`modules.workflow.migration.emit_migration_digests`) — их эквивалентность проверяет
    tests/db/test_workflow_bulk_migration.py::test_digest_equivalent_to_legacy.
    """
    if not role_code:
        return []
    await ensure_role_catalog(session)
    rows = await session.execute(
        select(AppUser)
        .where(AppUser.roles.any(role_code), AppUser.is_active.is_(True))
        .order_by(AppUser.id)
    )
    users = list(rows.scalars().all())
    if pipeline is None:
        return users
    if responsible_user_id is None:
        return [user for user in users if user.access.has("requests:read", pipeline)]
    return [user for user in users if sees_deal(user, pipeline, responsible_user_id)]


async def _resolve_rule_users(
    session: AsyncSession, rule: NotificationRule, deal: Deal | None
) -> list[AppUser]:
    if rule.recipient_type == "responsible":
        if deal is None:
            return []
        user = await session.get(AppUser, deal.responsible_user_id)
        return [user] if user else []
    if rule.recipient_type == "manager_of_responsible":
        if deal is None:
            return []
        responsible = await session.get(AppUser, deal.responsible_user_id)
        if responsible is None or responsible.manager_id is None:
            return []
        manager = await session.get(AppUser, responsible.manager_id)
        return [manager] if manager else []
    if rule.recipient_type == "role":
        return await role_rule_members(
            session,
            rule.recipient_value or "",
            pipeline=deal.pipeline if deal is not None else None,
            responsible_user_id=deal.responsible_user_id if deal is not None else None,
        )
    if rule.recipient_type == "user":
        try:
            user = await session.get(AppUser, uuid.UUID(rule.recipient_value or ""))
        except ValueError:
            return []
        return [user] if user else []
    # tg_username / email — прямой адрес без app_user
    return []


async def _matching_rules(
    session: AsyncSession, event_type: str, deal: Deal | None
) -> list[NotificationRule]:
    stmt = select(NotificationRule).where(
        NotificationRule.event_type == event_type, NotificationRule.is_active.is_(True)
    )
    rules = list((await session.execute(stmt)).scalars().all())
    if deal is None:
        return [r for r in rules if r.workflow_id is None and r.stage_id is None]
    return [
        r
        for r in rules
        if (r.workflow_id is None or r.workflow_id == deal.workflow_id)
        and (r.stage_id is None or r.stage_id == deal.stage_id)
    ]


async def _insert_notification(
    session: AsyncSession,
    *,
    rule_id: uuid.UUID | None,
    event_type: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    recipient_user_id: uuid.UUID | None,
    channel: str,
    address: str | None,
    subject: str | None,
    body: str,
    payload: dict[str, Any] | None,
    dedup_key: str | None,
    skipped_reason: str | None = None,
) -> bool:
    """True — строка outbox создана (в т.ч. skipped без адреса); False — подавлена dedup_key."""
    status = "pending"
    last_error = None
    if skipped_reason:
        # канал отключён настройками получателя — строка остаётся в in-app ленте
        status = "skipped"
        last_error = skipped_reason
    if not address:
        # канал не настроен (нет tg_chat_id/email) — фиксируем факт, relay не трогает
        status = "skipped"
        last_error = last_error or "Адрес доставки не настроен у получателя"
        address = str(recipient_user_id) if recipient_user_id else "-"
    values = {
        "id": uuid.uuid4(),
        "rule_id": rule_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "recipient_user_id": recipient_user_id,
        "channel": channel,
        "address": address,
        "subject": subject,
        "body": body,
        "payload": payload,
        "dedup_key": dedup_key,
        "status": status,
        "last_error": last_error,
    }
    if dedup_key is not None:
        result = await session.execute(
            pg_insert(Notification)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(Notification.id)
        )
        return result.scalar_one_or_none() is not None
    await session.execute(pg_insert(Notification).values(**values))
    return True


async def emit_event_notifications(
    session: AsyncSession,
    *,
    event_type: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    subject: str | None,
    body: str,
    payload: dict[str, Any] | None = None,
    deal: Deal | None = None,
    recipient_types: tuple[str, ...] | None = None,
    direct_recipients: list[DirectRecipient] | None = None,
    dedup_key: str | None = None,
    exclude_user_ids: tuple[uuid.UUID, ...] = (),
) -> int:
    """Пишет строки outbox `notification` в ТЕКУЩЕЙ транзакции (commit — у вызывающего).

    Получатели — по активным `notification_rule` события (сужение workflow/stage — по
    заявке) либо `direct_recipients` без правила. `dedup_key` (уникальный индекс)
    подавляет дубли: при нескольких получателях ключ дополняется id пользователя.
    Возвращает число реально созданных pending-строк.
    """
    targets: list[tuple[uuid.UUID | None, AppUser, str]] = []  # (rule_id, user, channel)
    # прямые адреса из правил tg_username/email — без app_user (data-model.md §9.4)
    direct_addresses: list[tuple[uuid.UUID, str, str]] = []  # (rule_id, channel, address)
    if direct_recipients:
        targets.extend((None, item.user, item.channel) for item in direct_recipients)
    else:
        rules = await _matching_rules(session, event_type, deal)
        if recipient_types is not None:
            rules = [r for r in rules if r.recipient_type in recipient_types]
        if not rules and event_type == EVENT_INTEGRATION_DELIVERY_FAILED:
            # дефолтного правила в сиде нет, но админ обязан узнать о DLQ (§13.7):
            # фолбэк кодом — всем активным админам в telegram (код admin системный и
            # неизменяемый — Р-F2-9)
            admins = await session.execute(
                select(AppUser).where(AppUser.roles.any("admin"), AppUser.is_active.is_(True))
            )
            targets.extend((None, user, "telegram") for user in admins.scalars().all())
        for rule in rules:
            if rule.recipient_type in ("tg_username", "email") and rule.recipient_value:
                direct_addresses.append((rule.id, rule.channel, rule.recipient_value))
                continue
            users = await _resolve_rule_users(session, rule, deal)
            targets.extend((rule.id, user, rule.channel) for user in users)

    # дедупликация получателей внутри одного события (user+channel)
    seen: set[tuple[uuid.UUID, str]] = set()
    unique_targets: list[tuple[uuid.UUID | None, AppUser, str]] = []
    for rule_id, user, channel in targets:
        if user.id in exclude_user_ids or (user.id, channel) in seen:
            continue
        seen.add((user.id, channel))
        unique_targets.append((rule_id, user, channel))

    # персональные настройки получателей (api-contract.md §9.1): событие можно
    # выключить целиком, канал — перевести в skipped (останется в in-app ленте)
    from app.modules.notifications.prefs import delivery_decision, load_prefs_for_users

    prefs = await load_prefs_for_users(session, [user.id for _, user, _ in unique_targets])

    created = 0
    multiple = len(unique_targets) + len(direct_addresses) > 1
    for rule_id, user, channel in unique_targets:
        decision = delivery_decision(
            prefs.get(user.id),
            event_type=event_type,
            channel=channel,
            is_responsible=(user.id == deal.responsible_user_id if deal is not None else None),
        )
        if decision == "drop":
            continue
        row_dedup = None
        if dedup_key is not None:
            row_dedup = f"{dedup_key}:{user.id}" if multiple else dedup_key
        inserted = await _insert_notification(
            session,
            rule_id=rule_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            recipient_user_id=user.id,
            channel=channel,
            address=_address_for(user, channel),
            subject=subject,
            body=body,
            payload=payload,
            dedup_key=row_dedup,
            skipped_reason=(
                "Канал отключён настройками получателя" if decision == "skip_channel" else None
            ),
        )
        if inserted:
            created += 1

    # прямые адреса (@ник / email из правила): без app_user и без личных настроек
    seen_direct: set[tuple[str, str]] = set()
    for rule_id, channel, address in direct_addresses:
        if (channel, address) in seen_direct:
            continue
        seen_direct.add((channel, address))
        row_dedup = None
        if dedup_key is not None:
            row_dedup = f"{dedup_key}:{address}" if multiple else dedup_key
        inserted = await _insert_notification(
            session,
            rule_id=rule_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            recipient_user_id=None,
            channel=channel,
            address=address,
            subject=subject,
            body=body,
            payload=payload,
            dedup_key=row_dedup,
        )
        if inserted:
            created += 1
    return created


# --------------------------------------------------------------------------------------
# Outbox интеграций
# --------------------------------------------------------------------------------------


async def emit_integration_event(
    session: AsyncSession,
    *,
    system: str,
    event_type: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    envelope: dict[str, Any],
) -> IntegrationEvent:
    """Пишет outbound-событие в ТЕКУЩЕЙ транзакции; доставит фоновый relay."""
    event = IntegrationEvent(
        event_id=uuid.UUID(envelope["event_id"]),
        direction="outbound",
        system=system,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=envelope,
        status="pending",
    )
    session.add(event)
    return event


async def _store_external_ids(
    session: AsyncSession, event: IntegrationEvent, response_body: dict[str, Any]
) -> None:
    """Сохраняет внешние id из ответа заглушки в `external_link` (api-contract.md §13.2/13.4)."""
    pairs = []
    if event.entity_type == "deal" and response_body.get("lms_enrollment_id"):
        pairs.append(("deal", event.entity_id, "lms", str(response_body["lms_enrollment_id"])))
    if event.entity_type == "program" and response_body.get("cms_external_id"):
        pairs.append(("program", event.entity_id, "cms", str(response_body["cms_external_id"])))
    for entity_type, entity_id, system, external_id in pairs:
        if entity_id is None:
            continue
        await session.execute(
            pg_insert(ExternalLink)
            .values(
                entity_type=entity_type,
                entity_id=entity_id,
                system=system,
                external_id=external_id,
            )
            .on_conflict_do_update(
                index_elements=[
                    ExternalLink.entity_type,
                    ExternalLink.entity_id,
                    ExternalLink.system,
                ],
                set_={"external_id": external_id},
            )
        )


async def _mark_integration_dead(
    session: AsyncSession, event: IntegrationEvent, error: str
) -> None:
    event.status = "dead"
    event.last_error = error[:2000]
    event.next_retry_at = None
    await emit_event_notifications(
        session,
        event_type=EVENT_INTEGRATION_DELIVERY_FAILED,
        entity_type="integration_event",
        entity_id=event.id,
        subject="Интеграция: доставка не удалась",
        body=(
            f"Событие {event.event_type} ({event.system}) не доставлено и помещено в DLQ: "
            f"{error[:200]}"
        ),
        payload={"integration_event_id": str(event.id), "event_type": event.event_type},
    )


async def deliver_integration_event(
    session: AsyncSession, event: IntegrationEvent, transport: IntegrationTransport
) -> bool:
    """Одна попытка доставки outbound-события через транспорт; исход переводится
    в состояние события (delivered / retrying / dead). True — доставлено."""
    outcome = await transport.deliver(event)
    if outcome.status == "delivered":
        event.status = "delivered"
        event.processed_at = _now()
        event.last_error = None
        event.next_retry_at = None
        if outcome.response_body:
            await _store_external_ids(session, event, outcome.response_body)
        return True
    error = outcome.error or "Ошибка доставки"
    if outcome.status == "dead":
        # ошибка контракта или нет адресата — не ретраим (§13.7), чинится через админку
        await _mark_integration_dead(session, event, error)
        return False
    await _register_integration_failure(session, event, error)
    return False


async def _register_integration_failure(
    session: AsyncSession, event: IntegrationEvent, error: str
) -> None:
    event.attempts += 1
    delay = next_integration_retry_delay(event.attempts)
    if delay is None:
        await _mark_integration_dead(session, event, error)
        return
    event.status = "retrying"
    event.last_error = error[:2000]
    event.next_retry_at = _now() + timedelta(seconds=delay)


async def run_integration_relay_pass(session: AsyncSession) -> int:
    """Один проход по outbound-очереди; возвращает число доставленных событий."""
    stmt = (
        select(IntegrationEvent)
        .where(
            IntegrationEvent.direction == "outbound",
            IntegrationEvent.status.in_(("pending", "retrying")),
            or_(
                IntegrationEvent.next_retry_at.is_(None),
                IntegrationEvent.next_retry_at <= _now(),
            ),
        )
        .order_by(IntegrationEvent.created_at)
        .limit(RELAY_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    events = list((await session.execute(stmt)).scalars().all())
    if not events:
        return 0
    delivered = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        transport = HttpIntegrationTransport(client)
        for event in events:
            if await deliver_integration_event(session, event, transport):
                delivered += 1
    await session.commit()
    return delivered


# --------------------------------------------------------------------------------------
# Relay нотификаций → notification-service
# --------------------------------------------------------------------------------------


def build_send_envelope(notification: Notification) -> dict[str, Any]:
    """Тело `POST /api/v1/send` notification-service (api-contract.md §9.4)."""
    recipient: dict[str, Any] = {
        "user_id": str(notification.recipient_user_id) if notification.recipient_user_id else None,
        "channels": [notification.channel],
        "telegram_chat_id": None,
        "email": None,
        # вычисленный адрес как есть: chat_id / email / @ник (правила tg_username)
        "address": notification.address,
    }
    if notification.channel == "telegram":
        try:
            recipient["telegram_chat_id"] = int(notification.address)
        except (TypeError, ValueError):
            recipient["telegram_chat_id"] = None
    elif notification.channel in ("email", "max"):
        recipient["email"] = notification.address
    payload = dict(notification.payload or {})
    payload.setdefault("subject", notification.subject)
    payload.setdefault("body", notification.body)
    return {
        "notification_id": str(notification.id),
        "event": notification.event_type,
        "created_at": notification.created_at.isoformat()
        if notification.created_at
        else _now().isoformat(),
        "recipients": [recipient],
        "payload": payload,
        "template": notification_template(notification.event_type),
    }


async def run_notification_relay_pass(session: AsyncSession) -> int:
    """Один проход по pending-нотификациям; возвращает число доставленных."""
    settings = get_settings()
    stmt = (
        select(Notification)
        .where(Notification.status == "pending")
        .order_by(Notification.created_at)
        .limit(RELAY_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return 0
    sent = 0
    sse_frames: list[dict[str, Any]] = []
    url = f"{settings.notification_base_url.rstrip('/')}/api/v1/send"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for notification in rows:
            try:
                response = await client.post(
                    url,
                    json=build_send_envelope(notification),
                    headers={"X-Internal-Token": settings.internal_api_token},
                )
                ok = response.status_code < 300
                error = None if ok else f"HTTP {response.status_code}: {response.text[:300]}"
                # 5xx/429 — недоступность/перегрузка получателя, а не битый конверт
                transient = response.status_code >= 500 or response.status_code == 429
            except httpx.HTTPError as exc:
                ok, error, transient = False, f"Сетевая ошибка: {exc}", True
            if ok:
                notification.status = "sent"
                notification.sent_at = _now()
                notification.last_error = None
                sent += 1
                if notification.recipient_user_id is not None:
                    sse_frames.append(
                        {
                            "notification_id": str(notification.id),
                            "user_id": str(notification.recipient_user_id),
                            "event": notification.event_type,
                        }
                    )
            else:
                notification.attempts += 1
                notification.last_error = error
                # транзиентные сбои не переводят очередь в failed: рестарт
                # notification-service не должен необратимо ронять накопленное
                if not transient and notification.attempts >= NOTIFICATION_MAX_ATTEMPTS:
                    notification.status = "failed"
    await session.commit()
    # «колокольчик» UI — строго после коммита (api-contract.md §2.2)
    for frame in sse_frames:
        await publish_event(SSE_NOTIFICATION_CREATED, frame)
    return sent


# --------------------------------------------------------------------------------------
# Фоновые циклы (lifespan)
# --------------------------------------------------------------------------------------


async def outbox_relay_loop() -> None:
    """Периодический relay обоих outbox'ов; ошибки не роняют цикл."""
    factory = get_session_factory()
    while True:
        try:
            async with factory() as session:
                await run_notification_relay_pass(session)
            async with factory() as session:
                await run_integration_relay_pass(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — фоновый цикл обязан пережить любой сбой
            logger.exception("Outbox relay: сбой прохода, продолжаем")
        await asyncio.sleep(RELAY_INTERVAL_SECONDS)


async def run_relay_once() -> dict[str, int]:
    """Принудительный прогон (POST /internal/jobs/outbox-relay) — для отладки/демо."""
    started = time.monotonic()
    factory = get_session_factory()
    async with factory() as session:
        notifications_sent = await run_notification_relay_pass(session)
    async with factory() as session:
        integration_delivered = await run_integration_relay_pass(session)
    return {
        "notifications_sent": notifications_sent,
        "integration_delivered": integration_delivered,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
