"""Пакетный перенос заявок удаляемых этапов и сводные уведомления
(workflow-engine.md §6.3–6.5, §6.8).

Вызывается внутри транзакции применения пакета под эксклюзивным advisory-локом воронки.
Сессия не должна держать загруженные `Deal` этой воронки — bulk UPDATE их не обновит
(в identity map ORM останутся старые `stage_id`/`xmin`).

- `migrate_deleted_stages` — один SQL-оператор на удаляемый этап: CTE
  `UPDATE deal … RETURNING` сразу кормит `INSERT INTO deal_stage_history … SELECT`, наружу
  возвращаются только агрегаты по ответственным (десятки строк). Число обращений к БД не
  зависит от числа заявок, Python не держит N ORM-объектов. `stage_entered_at` не
  трогается: перестройка схемы не «омолаживает» заявку (§6.3 п.5).
- `emit_migration_digests` — вместо уведомления на каждую заявку каждый получатель
  `(пользователь | прямой адрес, канал)`, который раньше получил бы хотя бы одно
  уведомление, получает ровно одно сводное; `payload.count` — сколько заявок он увидел бы
  раньше. Тип события прежний (`request.transitioned`): работают те же правила (сидовое и
  созданные админами) и те же личные настройки («Переход заявки по этапу», only_mine), что в
  `core.outbox.emit_event_notifications`, но получатели считаются по группам «целевой
  этап × ответственный», а не по заявкам. Получатели правила `role` — та же
  `core.outbox.role_rule_members` (активные с ролью, видящие процесс воронки), «только
  свои» — по ответственному группы. Эквивалентность со старым поштучным путём —
  tests/db/test_workflow_bulk_migration.py::test_digest_equivalent_to_legacy.

SSE `request.transitioned` и `integration_event` (LMS handover) при переносе не
публикуются — как и раньше: административная перестройка не запускает передачу в LMS.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EVENT_REQUEST_TRANSITIONED

# приватные хелперы outbox — осознанно: строки пишутся тем же кодом (адрес, skipped,
# ON CONFLICT по dedup_key), а получатели ролей считаются той же функцией, что и у
# поштучных уведомлений
from app.core.outbox import _address_for, _insert_notification, role_rule_members, sees_deal
from app.models.service import AppSetting, NotificationRule
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.notifications.prefs import delivery_decision, merge_settings, settings_key
from app.modules.workflow.graph import ApplyPlan

DIGEST_SUBJECT = "Заявки перенесены"
DIGEST_SAMPLE_IDS = 20  # id заявок в payload (ссылки из ленты)
DIGEST_SAMPLE_TITLES = 3  # «Например: …» в тексте
DIGEST_MAX_PAIRS = 5  # строк «этап удалён — N перенесено» в тексте; остальное — одной строкой
DIGEST_NAME_LIMIT = 80  # названия этапов/заявок/воронки в тексте обрезаются
SKIPPED_CHANNEL_DISABLED = "Канал отключён настройками получателя"
DIRECT_RECIPIENT_TYPES = ("tg_username", "email")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------
# Перенос заявок
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MovedGroup:
    """Заявки одного ответственного, перенесённые с одного удаляемого этапа."""

    from_stage_id: uuid.UUID
    from_stage_name: str
    to_stage_id: uuid.UUID
    to_stage_name: str
    responsible_user_id: uuid.UUID
    count: int
    sample_ids: tuple[uuid.UUID, ...] = ()  # ≤ 20, по title
    sample_titles: tuple[str, ...] = ()  # ≤ 3, по title


@dataclass
class MigrationOutcome:
    migrated_total: int = 0
    by_status: list[dict[str, Any]] = field(default_factory=list)  # [{"from","to","count"}]
    groups: list[MovedGroup] = field(default_factory=list)  # для сводок; legacy-путь — []


_UUID = PG_UUID(as_uuid=True)

MIGRATE_STAGE_SQL = sa.text(
    """
WITH moved AS (
    UPDATE deal AS d
       SET stage_id = :to_id, last_activity_at = now(), updated_at = now()
     WHERE d.workflow_id = :wf AND d.stage_id = :from_id AND d.status = 'open'
    RETURNING d.id, d.title, d.responsible_user_id
), hist AS (
    INSERT INTO deal_stage_history (deal_id, from_stage_id, to_stage_id, from_stage_name,
                                    to_stage_name, kind, reason, comment, actor_user_id,
                                    is_return)
    SELECT m.id, :from_id, :to_id, :from_name, :to_name,
           'system_migration', 'stage_deleted', :comment, NULL, false
      FROM moved AS m
    RETURNING 1
)
SELECT m.responsible_user_id,
       count(*) AS cnt,
       (array_agg(m.id ORDER BY m.title, m.id))[1:20] AS sample_ids,
       (array_agg(m.title ORDER BY m.title, m.id))[1:3] AS sample_titles,
       (SELECT count(*) FROM hist) AS history_rows
  FROM moved AS m
 GROUP BY m.responsible_user_id
 ORDER BY m.responsible_user_id
"""
).bindparams(
    sa.bindparam("wf", type_=_UUID),
    sa.bindparam("from_id", type_=_UUID),
    sa.bindparam("to_id", type_=_UUID),
    sa.bindparam("from_name", type_=sa.Text),
    sa.bindparam("to_name", type_=sa.Text),
    sa.bindparam("comment", type_=sa.Text),
)


def migration_comment(from_name: str, to_name: str) -> str:
    """Комментарий строки истории — тот же текст, что писал поштучный перенос."""
    return f"Статус «{from_name}» удалён администратором, заявка перенесена в «{to_name}»"


def _as_uuid(value: Any) -> uuid.UUID:
    return value if type(value) is uuid.UUID else uuid.UUID(str(value))


async def migrate_deleted_stages(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    plan: ApplyPlan,
    stage_rows: dict[uuid.UUID, WorkflowStage],
) -> MigrationOutcome:
    """Переносит открытые заявки каждого удаляемого этапа в цель `migrate_to_status_id`
    и пишет строку истории `system_migration` на каждую заявку — одним оператором на этап.

    Имена этапов берутся из `stage_rows` — уже после переименований этого же пакета.
    Пункты без цели переноса пропускаются: V-8 под тем же локом гарантирует, что открытых
    заявок там нет. Расхождение «перенесено ≠ строк истории» — RuntimeError (инвариант
    I-9), транзакция применения откатывается целиком.
    """
    outcome = MigrationOutcome()
    for item in plan.deleted_stages:
        if item.migrate_to_status_id is None:
            continue
        from_row = stage_rows[item.stage_id]
        to_row = stage_rows[item.migrate_to_status_id]
        rows = (
            await session.execute(
                MIGRATE_STAGE_SQL,
                {
                    "wf": workflow_id,
                    "from_id": from_row.id,
                    "to_id": to_row.id,
                    "from_name": from_row.name,
                    "to_name": to_row.name,
                    "comment": migration_comment(item.stage_name, to_row.name),
                },
            )
        ).all()
        moved = sum(int(row.cnt) for row in rows)
        history_rows = int(rows[0].history_rows) if rows else 0
        if moved != history_rows:
            raise RuntimeError("инвариант I-9: история не совпала с переносом")
        if moved == 0:
            continue
        outcome.migrated_total += moved
        outcome.by_status.append({"from": item.stage_name, "to": to_row.name, "count": moved})
        outcome.groups.extend(
            MovedGroup(
                from_stage_id=from_row.id,
                from_stage_name=from_row.name,
                to_stage_id=to_row.id,
                to_stage_name=to_row.name,
                responsible_user_id=_as_uuid(row.responsible_user_id),
                count=int(row.cnt),
                sample_ids=tuple(_as_uuid(v) for v in (row.sample_ids or ())),
                sample_titles=tuple(str(v) for v in (row.sample_titles or ())),
            )
            for row in rows
        )
    return outcome


# --------------------------------------------------------------------------------------
# Сводные уведомления: чистая часть (юнит-тестируется без БД)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DigestRow:
    """Одна сводка: получатель `(пользователь, канал)` или `(прямой адрес, канал)`."""

    rule_id: uuid.UUID | None
    recipient_user_id: uuid.UUID | None
    channel: str
    address: str | None  # прямой адрес правила tg_username/email; у пользователя — None
    skipped_reason: str | None
    groups: tuple[MovedGroup, ...]
    only_own: bool  # все заявки сводки — самого получателя (ссылка с фильтром kam)

    @property
    def count(self) -> int:
        return sum(group.count for group in self.groups)


def _rule_order(rule: NotificationRule) -> tuple[datetime, str]:
    return (rule.created_at or _EPOCH, str(rule.id))


def rules_for_target(
    rules: Iterable[NotificationRule], workflow_id: uuid.UUID, stage_id: uuid.UUID
) -> list[NotificationRule]:
    """Правила `request.transitioned`, сработавшие бы на заявку воронки `workflow_id`,
    уже стоящую на целевом этапе `stage_id` (поштучный путь сужал правила по новому
    `deal.stage_id` — `core.outbox._matching_rules`). Порядок входа сохраняется."""
    return [
        rule
        for rule in rules
        if rule.event_type == EVENT_REQUEST_TRANSITIONED
        and rule.is_active
        and (rule.workflow_id is None or rule.workflow_id == workflow_id)
        and (rule.stage_id is None or rule.stage_id == stage_id)
    ]


def _rule_recipients(
    rule: NotificationRule,
    group: MovedGroup,
    users: dict[uuid.UUID, AppUser],
    role_members: dict[str, list[AppUser]],
    pipeline: str | None = None,
) -> list[AppUser]:
    """Повтор `core.outbox._resolve_rule_users` для заявки ответственного группы.

    `role_members` уже отфильтрованы по процессу воронки (`role_rule_members`); при
    известном `pipeline` участник роли с областью «только свои» получает лишь группы, где
    он ответственный (`core.outbox.sees_deal` — как у поштучного пути)."""
    if rule.recipient_type == "responsible":
        user = users.get(group.responsible_user_id)
        return [user] if user is not None else []
    if rule.recipient_type == "manager_of_responsible":
        responsible = users.get(group.responsible_user_id)
        if responsible is None or responsible.manager_id is None:
            return []
        manager = users.get(responsible.manager_id)
        return [manager] if manager is not None else []
    if rule.recipient_type == "role":
        members = role_members.get(rule.recipient_value or "", ())
        if pipeline is None:
            return list(members)
        return [m for m in members if sees_deal(m, pipeline, group.responsible_user_id)]
    if rule.recipient_type == "user":
        try:
            user_id = uuid.UUID(rule.recipient_value or "")
        except ValueError:
            return []
        user = users.get(user_id)
        return [user] if user is not None else []
    return []


@dataclass
class _Bucket:
    rank: int
    rule_id: uuid.UUID
    skipped_reason: str | None = None
    groups: list[MovedGroup] = field(default_factory=list)
    keys: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)

    def add(self, group: MovedGroup, rank: int, rule_id: uuid.UUID) -> None:
        if rank < self.rank:
            self.rank, self.rule_id = rank, rule_id
        key = (group.from_stage_id, group.responsible_user_id)
        if key not in self.keys:  # группы не пересекаются: заявка не считается дважды
            self.keys.add(key)
            self.groups.append(group)


def plan_migration_digests(
    groups: list[MovedGroup],
    rules: list[NotificationRule],
    *,
    workflow_id: uuid.UUID,
    users: dict[uuid.UUID, AppUser],
    role_members: dict[str, list[AppUser]],
    prefs: dict[uuid.UUID, dict[str, Any]],
    pipeline: str | None = None,
) -> list[DigestRow]:
    """Сводки по тем же правилам и настройкам, что поштучный `emit_event_notifications`.

    - `users` — ответственные групп, их руководители и пользователи из правил `user`;
      `role_members` — активные участники ролей из правил `role`, видящие процесс воронки
      (`pipeline` — её код: «только свои» участника роли сверяется с ответственным группы);
    - корзина `(user.id, channel)` / `(channel, address)` хранит множество групп;
      `rule_id` — первого по порядку `(created_at, id)` правила, давшего корзину;
    - `delivery_decision` применяется к каждой группе (`is_responsible` — получатель и есть
      ответственный группы): `drop` выкидывает группу, `skip_channel` даёт строку
      skipped (остаётся в in-app ленте);
    - прямые адреса правил tg_username/email — без личных настроек, все группы.
    """
    ordered = sorted(rules, key=_rule_order)
    rank_of = {id(rule): rank for rank, rule in enumerate(ordered)}
    user_buckets: dict[tuple[uuid.UUID, str], _Bucket] = {}
    address_buckets: dict[tuple[str, str], _Bucket] = {}
    for group in groups:
        for rule in rules_for_target(ordered, workflow_id, group.to_stage_id):
            rank = rank_of[id(rule)]
            if rule.recipient_type in DIRECT_RECIPIENT_TYPES and rule.recipient_value:
                key = (rule.channel, rule.recipient_value)
                bucket = address_buckets.setdefault(key, _Bucket(rank=rank, rule_id=rule.id))
                bucket.add(group, rank, rule.id)
                continue
            for user in _rule_recipients(rule, group, users, role_members, pipeline):
                decision = delivery_decision(
                    prefs.get(user.id),
                    event_type=EVENT_REQUEST_TRANSITIONED,
                    channel=rule.channel,
                    is_responsible=user.id == group.responsible_user_id,
                )
                if decision == "drop":
                    continue
                bucket = user_buckets.setdefault(
                    (user.id, rule.channel), _Bucket(rank=rank, rule_id=rule.id)
                )
                if decision == "skip_channel":
                    bucket.skipped_reason = SKIPPED_CHANNEL_DISABLED
                bucket.add(group, rank, rule.id)

    result: list[DigestRow] = []
    for (user_id, channel), bucket in user_buckets.items():
        result.append(
            DigestRow(
                rule_id=bucket.rule_id,
                recipient_user_id=user_id,
                channel=channel,
                address=None,
                skipped_reason=bucket.skipped_reason,
                groups=tuple(bucket.groups),
                only_own=all(g.responsible_user_id == user_id for g in bucket.groups),
            )
        )
    for (channel, address), bucket in address_buckets.items():
        result.append(
            DigestRow(
                rule_id=bucket.rule_id,
                recipient_user_id=None,
                channel=channel,
                address=address,
                skipped_reason=None,
                groups=tuple(bucket.groups),
                only_own=False,
            )
        )
    return result


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")  # 10 000 (неразрывный пробел)


def ru_requests(n: int) -> str:
    """«1 заявка / 2 заявки / 5 заявок»."""
    return f"{_fmt_int(n)} {_plural(n, 'заявка', 'заявки', 'заявок')}"


def _ru_moved(n: int) -> str:
    return _plural(n, "перенесена", "перенесены", "перенесено")


def _clip(value: str, limit: int = DIGEST_NAME_LIMIT) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def render_digest(
    row: DigestRow,
    *,
    workflow_name: str,
    workflow_code: str,
    change_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> tuple[str, str, dict[str, Any]]:
    """(subject, body, payload) сводки. Тело готово к отправке как есть (notification-
    service берёт `body`); длина ограничена при любом числе удалённых этапов."""
    pairs: dict[tuple[uuid.UUID, uuid.UUID], list[Any]] = {}
    for group in row.groups:
        pair = pairs.setdefault(
            (group.from_stage_id, group.to_stage_id),
            [group.from_stage_name, group.to_stage_name, 0],
        )
        pair[2] += group.count
    total = row.count

    lines = [f"Схема «{_clip(workflow_name)}» изменена администратором."]
    listed = list(pairs.values())
    for from_name, to_name, count in listed[:DIGEST_MAX_PAIRS]:
        lines.append(
            f"• этап «{_clip(from_name)}» удалён — {ru_requests(count)} "
            f"{_ru_moved(count)} в «{_clip(to_name)}»"
        )
    rest = listed[DIGEST_MAX_PAIRS:]
    if rest:
        rest_count = sum(item[2] for item in rest)
        lines.append(
            f"• ещё {len(rest)} {_plural(len(rest), 'этап', 'этапа', 'этапов')} — "
            f"{ru_requests(rest_count)} {_ru_moved(rest_count)}"
        )
    titles = [title for group in row.groups for title in group.sample_titles]
    titles = titles[:DIGEST_SAMPLE_TITLES]
    if titles:
        sample = ", ".join(f"«{_clip(title)}»" for title in titles)
        if total == 1:
            lines.append(f"Заявка: {sample}.")
        else:
            more = total - len(titles)
            lines.append(f"Например: {sample}" + (f" и ещё {_fmt_int(more)}." if more > 0 else "."))
    lines.append("Срок нахождения на этапе сохранён — счётчик «зависания» не сброшен.")

    targets: dict[uuid.UUID, str] = {}
    for group in row.groups:
        targets.setdefault(group.to_stage_id, group.to_stage_name)
    query: dict[str, str] = {"wf": workflow_code}
    if row.only_own and row.recipient_user_id is not None:
        query["kam"] = str(row.recipient_user_id)
    if len(targets) == 1:
        query["status"] = str(next(iter(targets)))
    request_ids = [str(i) for group in row.groups for i in group.sample_ids]

    payload: dict[str, Any] = {
        "digest": True,
        "migrated": True,
        "workflow_id": str(workflow_id),
        "change_request_id": str(change_id),
        "count": total,
        "by_status": [
            {"from": from_name, "to": to_name, "count": count}
            for from_name, to_name, count in listed
        ],
        "request_ids": request_ids[:DIGEST_SAMPLE_IDS],
        "title": ru_requests(total),
        "url": f"/board?{urlencode(query)}",
    }
    if len(targets) == 1:
        payload["status"] = next(iter(targets.values()))
    return DIGEST_SUBJECT, "\n".join(lines), payload


# --------------------------------------------------------------------------------------
# Сводные уведомления: загрузка получателей и запись outbox
# --------------------------------------------------------------------------------------


async def _load_users(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, AppUser]:
    ids = sorted(set(user_ids), key=str)
    if not ids:
        return {}
    rows = await session.execute(select(AppUser).where(AppUser.id.in_(ids)))
    return {user.id: user for user in rows.scalars().all()}


async def _load_prefs(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Личные настройки получателей одним запросом (семантика `load_prefs_for_users`)."""
    keys = {settings_key(user_id): user_id for user_id in set(user_ids)}
    if not keys:
        return {}
    rows = await session.execute(
        select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(sorted(keys)))
    )
    stored = {keys[key]: value for key, value in rows.all()}
    return {user_id: merge_settings(stored.get(user_id)) for user_id in keys.values()}


async def emit_migration_digests(
    session: AsyncSession,
    *,
    outcome: MigrationOutcome,
    workflow: Workflow,
    change_id: uuid.UUID,
) -> int:
    """Пишет сводки в outbox `notification` в ТЕКУЩЕЙ транзакции применения (нет
    переноса — нет уведомления). Возвращает число созданных строк (включая skipped).

    Строки: `event_type='request.transitioned'`, `entity_type='workflow_change_request'`,
    `entity_id=change_id`, `dedup_key='wfmig:{change_id}:{user_id|address}:{channel}'`.
    """
    if not outcome.groups:
        return 0
    rules = list(
        (
            await session.execute(
                select(NotificationRule)
                .where(
                    NotificationRule.event_type == EVENT_REQUEST_TRANSITIONED,
                    NotificationRule.is_active.is_(True),
                    or_(
                        NotificationRule.workflow_id.is_(None),
                        NotificationRule.workflow_id == workflow.id,
                    ),
                )
                .order_by(NotificationRule.created_at, NotificationRule.id)
            )
        )
        .scalars()
        .all()
    )
    if not rules:
        return 0

    users = await _load_users(session, (g.responsible_user_id for g in outcome.groups))
    manager_ids = {u.manager_id for u in users.values() if u.manager_id is not None}
    users.update(await _load_users(session, manager_ids - users.keys()))
    rule_user_ids: set[uuid.UUID] = set()
    for rule in rules:
        if rule.recipient_type == "user":
            try:
                rule_user_ids.add(uuid.UUID(rule.recipient_value or ""))
            except ValueError:
                continue
    users.update(await _load_users(session, rule_user_ids - users.keys()))
    role_members: dict[str, list[AppUser]] = {}
    role_codes = {r.recipient_value for r in rules if r.recipient_type == "role"}
    for code in sorted(c for c in role_codes if c):
        role_members[code] = await role_rule_members(session, code, pipeline=workflow.code)

    known: dict[uuid.UUID, AppUser] = dict(users)
    for members in role_members.values():
        known.update((member.id, member) for member in members)
    prefs = await _load_prefs(session, known.keys())

    digests = plan_migration_digests(
        outcome.groups,
        rules,
        workflow_id=workflow.id,
        users=users,
        role_members=role_members,
        prefs=prefs,
        pipeline=workflow.code,
    )
    created = 0
    for row in digests:
        subject, body, payload = render_digest(
            row,
            workflow_name=workflow.name,
            workflow_code=workflow.code,
            change_id=change_id,
            workflow_id=workflow.id,
        )
        if row.recipient_user_id is not None:
            address = _address_for(known[row.recipient_user_id], row.channel)
            recipient_key = str(row.recipient_user_id)
        else:
            address = row.address
            recipient_key = row.address or "-"
        inserted = await _insert_notification(
            session,
            rule_id=row.rule_id,
            event_type=EVENT_REQUEST_TRANSITIONED,
            entity_type="workflow_change_request",
            entity_id=change_id,
            recipient_user_id=row.recipient_user_id,
            channel=row.channel,
            address=address,
            subject=subject,
            body=body,
            payload=payload,
            dedup_key=f"wfmig:{change_id}:{recipient_key}:{row.channel}",
            skipped_reason=row.skipped_reason,
        )
        if inserted:
            created += 1
    return created
