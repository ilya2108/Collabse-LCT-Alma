"""Демо-данные вкладки «Просрочки» (`python -m app.tools.seed_sla`, из `run_seed`).

Поверх сида активности КАМов (`app.tools.seed_activity`) добавляет:
1. **Сценарные заявки (11)** с разной картиной просрочек: «вялый» kam12 — задержка больше
   30 дней, «вялый» kam15 — две собственные просрочки из трёх заявок в работе, «пропавший»
   kam18 — просрочки без действий после срока (одна — на «Новой», где норма задана для
   этапа), kam2 (учётка с входом) — небольшие просрочки по B2B и B2C, kam10 — ожидание
   оплаты B2C. Две заявки kam19 переданы kam05 за 5 дней до якоря — как в ручке передачи
   портфеля: ответственный, строки истории до передачи с автором kam19 и аудит
   `request.assigned` (`after.reason='portfolio_transfer'`, автор — head@demo). Одна
   передана уже просроченной (у kam05 — «получена просроченной»), вторая — до срока. Так
   у kam05 одна собственная просрочка и одна полученная: с полученной он прошёл бы правило
   «Просрочек больше, чем по команде», без неё — нет (полученные в правило не входят).
2. **Журнал напоминаний** по всем визитам стенда (сценарным и из сида активности), у
   которых срок истёк в последние 120 дней. Ритм — как у `check_stuck_requests`: прогоны в
   `entered + (N+1) сут + 1 ч + k·repeat сут`, пока визит активен и момент раньше якоря;
   в каждом прогоне — напоминание ответственному с ключом `stuck_dedup_key`, а при
   `days ≥ ceil(1,5·N)` — эскалация руководителю (`payload.escalation=true`). Получатель —
   владелец на момент отправки (по переданным заявкам до передачи — kam19, после — kam05).
   Вставка — `ON CONFLICT DO NOTHING` по частичному `uq_notification_dedup`.
3. **Реакции**: от первого доставленного напоминания эпизода КАМ пишет комментарий —
   активные (high) с вероятностью 80 % через 1–6 ч, средние — 60 % через 4–30 ч, вялые —
   35 % через 2–5 сут; «пропавший» и уволившийся не реагируют. Побочный эффект: у части
   КАМов чуть растёт число комментариев во вкладках «Активность».

Доставка: у демо-учёток нет привязки Telegram (а прописывать фиктивный `tg_chat_id`
нельзя — relay начал бы слать живые сообщения), поэтому записи журнала помечаются
доставленными (`status='sent'`, `sent_at`), кроме получателей `UNDELIVERED` — у них
`skipped` с текстом продового кода («Адрес доставки не настроен у получателя»): так видно
«доставлено n из m» и эпизоды «не доставлено». Привязавший Telegram получает адрес-чат.

Все даты — от якоря сида (`app.tools.seed.demo_now()`), совместимо с `make demo-reset`:
сброс очищает `notification`, а аудит передач сохраняется (как весь журнал аудита).
Повторный запуск ничего не дублирует: заявки — по заголовку, напоминания — по
`dedup_key`, комментарии — по паре (заявка, текст), передача пишется только вместе с
новой заявкой. План сценариев строит чистая функция `build_sla_plan(now)` с фиксированным
зерном. Существующие заявки сид не меняет — только добавляет журнал и комментарии.
"""

from __future__ import annotations

import asyncio
import random
import sys
import uuid
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EVENT_REQUEST_STUCK
from app.core.outbox import stuck_dedup_key
from app.models import (
    AppUser,
    AuditLog,
    Counterparty,
    Deal,
    DealComment,
    DealStageHistory,
    Notification,
    NotificationRule,
    University,
    Workflow,
    WorkflowStage,
)
from app.modules.reporting import sla
from app.modules.workflow.service import (
    _global_stuck_default,
    _repeat_every_days,
    escalation_after_days,
)
from app.tools.seed import demo_now
from app.tools.seed_activity import HEAD, KAMS, UNTOUCHED_UNIVERSITIES, _load_context

PLAN_SEED = 20261017
JOURNAL_DAYS = 120  # журнал — по визитам, чей срок истёк в последние 120 дней
TRANSFER_DAYS = 5  # передача портфеля kam19 → kam05 — за 5 дней до якоря
RUN_OFFSET = timedelta(hours=1)  # прогон проверки — через час после смены суток
SEND_DELAY = timedelta(minutes=1)
UNDELIVERED = frozenset({"kam09@demo"})  # пример «не доставлено»: нет адреса доставки
SKIPPED_REASON = "Адрес доставки не настроен у получателя"
INSERT_BATCH = 500

# профиль КАМа → (вероятность реакции, задержка от, до — часы)
REACTION_PROFILES: dict[str, tuple[float, float, float]] = {
    "high": (0.8, 1, 6),
    "medium": (0.6, 4, 30),
    "low": (0.35, 48, 120),
}
REACTION_BODIES: tuple[str, ...] = (
    "Получил напоминание, связался с вузом — жду ответ до конца недели.",
    "Напоминание принято: отправил повторное письмо контактному лицу.",
    "Созвонился после напоминания, договорились о следующем шаге.",
    "Уточнил статус у вуза после напоминания, обновлю этап по итогам.",
)

B2B_ROUTE: tuple[str, ...] = ("new", "first_contact", "negotiation", "contract_prep")
B2C_ROUTE: tuple[str, ...] = ("new", "consultation", "proposal", "payment_pending")


@dataclass(frozen=True)
class ScenarioPlan:
    topic: str  # заголовок — «{вуз или клиент} — {тема}»
    kam: str  # ответственный сейчас
    pipeline: str
    stage: str
    days_on_stage: float
    previous: str | None = None  # прежний владелец (передача портфеля)
    # (этап, дней назад) — входы в этапы по порядку, последний — текущий этап
    path: tuple[tuple[str, float], ...] = ()
    # (дней назад, автор, текст)
    comments: tuple[tuple[float, str, str], ...] = field(default=())


# (тема, КАМ сейчас, прежний КАМ, процесс, этап, дней на этапе, комментарии)
_SCENARIOS: tuple[tuple[str, str, str | None, str, str, float, tuple], ...] = (
    (
        "цифровая кафедра, согласование бюджета",
        "kam12@demo",
        None,
        "b2b",
        "negotiation",
        48,
        ((12, "kam12@demo", "Вуз пересматривает бюджет кафедры, ответ обещали позже."),),
    ),
    ("летняя школа, проект договора", "kam12@demo", None, "b2b", "contract_prep", 23, ()),
    (
        "стажировки в индустрии, первый звонок",
        "kam18@demo",
        None,
        "b2b",
        "first_contact",
        62,
        (),
    ),
    (
        "сетевая программа, входящая заявка",
        "kam18@demo",
        None,
        "b2b",
        "new",
        19,
        ((17, "kam18@demo", "Заявка с сайта, контакт проректора уточняю."),),
    ),
    (
        "корпоративная школа Python, условия группы",
        "kam15@demo",
        None,
        "b2b",
        "negotiation",
        31,
        ((20, "kam15@demo", "Вуз просит сдвинуть старт группы на весну."),),
    ),
    (
        "MLOps для инженеров, согласование договора",
        "kam05@demo",
        "kam19@demo",
        "b2b",
        "contract_prep",
        27,
        ((10, "kam19@demo", "Юристы вуза держат проект договора вторую неделю."),),
    ),
    (
        "хакатон для студентов, партнёрство",
        "kam05@demo",
        "kam19@demo",
        "b2b",
        "negotiation",
        12,
        ((2, "kam05@demo", "Принял заявку после передачи, назначил созвон с деканом."),),
    ),
    (
        "ИИ-инженер, коммерческое предложение",
        "kam2@demo",
        None,
        "b2c",
        "proposal",
        17,
        ((1, "kam2@demo", "Клиент сравнивает предложение с другими курсами."),),
    ),
    ("повышение квалификации ППС, знакомство", "kam2@demo", None, "b2b", "first_contact", 16, ()),
    (
        "аналитик данных с нуля, счёт на оплату",
        "kam10@demo",
        None,
        "b2c",
        "payment_pending",
        22,
        (),
    ),
    # третья заявка kam15 в работе — в пределах нормы: его доля собственных просрочек
    # (2 из 3) проходит правило отбора
    (
        "анализ данных для магистров, проект договора",
        "kam15@demo",
        None,
        "b2b",
        "contract_prep",
        9,
        (),
    ),
)


def build_sla_plan(now: datetime) -> list[ScenarioPlan]:
    """Сценарии с путями по этапам (дни — от якоря `now`); фиксированное зерно."""
    del now  # даты — дни назад от якоря; сам момент подставляется при записи
    rng = random.Random(PLAN_SEED)
    plans: list[ScenarioPlan] = []
    for topic, kam, previous, pipeline, stage, days, comments in _SCENARIOS:
        route = B2B_ROUTE if pipeline == "b2b" else B2C_ROUTE
        steps = list(route[: route.index(stage) + 1])
        moments = [float(days)]
        for _ in steps[:-1]:
            moments.append(moments[-1] + round(rng.uniform(2.0, 5.5), 2))  # в пределах нормы
        path = tuple(zip(steps, reversed(moments), strict=True))
        plans.append(
            ScenarioPlan(
                topic=topic,
                kam=kam,
                pipeline=pipeline,
                stage=stage,
                days_on_stage=float(days),
                previous=previous,
                path=path,
                comments=tuple(comments),
            )
        )
    return plans


def reminder_runs(
    entered: datetime,
    norm_days: int,
    repeat_days: int,
    *,
    until: datetime,
) -> list[datetime]:
    """Моменты прогонов проверки зависших для визита: с `entered + (N+1) сут + 1 ч` каждые
    `repeat` суток, пока раньше `until` (`min(конец визита, якорь)`)."""
    runs: list[datetime] = []
    moment = entered + timedelta(days=norm_days + 1) + RUN_OFFSET
    step = timedelta(days=max(repeat_days, 1))
    while moment < until:
        runs.append(moment)
        moment += step
    return runs


def reaction_delay(username: str, rng: random.Random) -> timedelta | None:
    """Через сколько КАМ отреагирует на напоминание (None — не отреагирует)."""
    persona = next((p for name, _full, p, _active in KAMS if name == username), "medium")
    profile = REACTION_PROFILES.get(persona)
    if profile is None:
        return None
    probability, low, high = profile
    if rng.random() >= probability:
        return None
    return timedelta(hours=rng.uniform(low, high))


def _rng(*parts: Any) -> random.Random:
    return random.Random(zlib.crc32("|".join(str(part) for part in parts).encode("utf-8")))


# --------------------------------------------------------------------------------------
# Сценарные заявки и передача
# --------------------------------------------------------------------------------------


async def _first_university(session: AsyncSession, kam: AppUser) -> University | None:
    return (
        await session.execute(
            select(University)
            .where(
                University.kam_user_id == kam.id,
                University.is_active.is_(True),
                # «закреплён, нет контакта» в отчёте КАМов — такие вузы не трогаем
                University.short_name.not_in(tuple(UNTOUCHED_UNIVERSITIES)),
            )
            .order_by(University.name)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _clients(session: AsyncSession) -> list[Counterparty]:
    return list(
        (
            await session.execute(
                select(Counterparty)
                .where(Counterparty.kind == "person")
                .order_by(Counterparty.display_name)
            )
        )
        .scalars()
        .all()
    )


async def _create_scenario(
    session: AsyncSession,
    plan: ScenarioPlan,
    *,
    index: int,
    now: datetime,
    workflows: Mapping[str, tuple[Workflow, Mapping[str, WorkflowStage]]],
    users: Mapping[str, AppUser],
    clients: Sequence[Counterparty],
) -> tuple[bool, bool]:
    """(создана заявка, записана передача)."""
    if plan.pipeline not in workflows:
        return False, False
    workflow, stages = workflows[plan.pipeline]
    owner = users.get(plan.kam)
    previous = users.get(plan.previous) if plan.previous else None
    head = users.get(HEAD)
    if owner is None or (plan.previous and previous is None):
        return False, False
    if any(code not in stages for code, _days in plan.path):
        return False, False  # этап удалён из схемы (демо перевода) — сценарий пропускаем
    creator = previous or owner
    university = None
    client = None
    if plan.pipeline == "b2b":
        university = await _first_university(session, creator)
        if university is None:
            return False, False
        place = university.short_name or university.name
    else:
        if not clients:
            return False, False
        client = clients[index % len(clients)]
        place = client.display_name
    title = f"{place} — {plan.topic}"
    if (await session.execute(select(Deal.id).where(Deal.title == title))).first():
        return False, False

    moments = [(code, now - timedelta(days=days)) for code, days in plan.path]
    last_code, last_at = moments[-1]
    comments = [(now - timedelta(days=days), author, body) for days, author, body in plan.comments]
    deal = Deal(
        pipeline=workflow.code,
        workflow_id=workflow.id,
        stage_id=stages[last_code].id,
        title=title,
        university_id=university.id if university else None,
        counterparty_id=client.id if client else None,
        responsible_user_id=owner.id,
        status="open",
        stage_entered_at=last_at,
        last_activity_at=max([last_at, *(at for at, _a, _b in comments)]),
        created_by=creator.id,
        created_at=moments[0][1],
        updated_at=last_at,
    )
    session.add(deal)
    await session.flush()
    prior: WorkflowStage | None = None
    for position, (code, at) in enumerate(moments):
        stage = stages[code]
        session.add(
            DealStageHistory(
                deal_id=deal.id,
                from_stage_id=prior.id if prior else None,
                to_stage_id=stage.id,
                from_stage_name=prior.name if prior else None,
                to_stage_name=stage.name,
                actor_user_id=creator.id,
                kind="manual",
                reason="created" if position == 0 else None,
                created_at=at,
            )
        )
        prior = stage
    for at, author, body in comments:
        writer = users.get(author)
        if writer is not None:
            session.add(
                DealComment(deal_id=deal.id, author_user_id=writer.id, body=body, created_at=at)
            )
    transferred = False
    if previous is not None:
        session.add(
            AuditLog(
                actor_user_id=head.id if head else None,
                action="request.assigned",
                entity_type="deal",
                entity_id=deal.id,
                before={"assignee_id": str(previous.id)},
                after={"assignee_id": str(owner.id), "reason": "portfolio_transfer"},
                created_at=now - timedelta(days=TRANSFER_DAYS),
            )
        )
        transferred = True
    await session.flush()
    return True, transferred


# --------------------------------------------------------------------------------------
# Журнал напоминаний и реакции
# --------------------------------------------------------------------------------------


async def _stage_infos(session: AsyncSession) -> dict[uuid.UUID, sla.StageInfo]:
    default_days = await _global_stuck_default(session)
    return await sla.fetch_stage_infos(session, ("b2b", "b2c"), default_days=default_days)


async def _open_deals(session: AsyncSession, since: datetime) -> dict[uuid.UUID, sla.DealInfo]:
    rows = await session.execute(
        select(
            Deal.id,
            Deal.title,
            Deal.pipeline,
            Deal.responsible_user_id,
            Deal.stage_id,
            Deal.stage_entered_at,
            Deal.created_at,
        ).where(
            Deal.status != "archived",
            or_(Deal.status == "open", Deal.stage_entered_at >= since),
        )
    )
    return {
        row.id: sla.DealInfo(
            deal_id=row.id,
            title=row.title,
            pipeline=row.pipeline,
            university_id=None,
            university_name=None,
            university_short=None,
            client=None,
            responsible_id=row.responsible_user_id,
            stage_id=row.stage_id,
            stage_entered_at=row.stage_entered_at,
            created_at=row.created_at,
        )
        for row in rows.all()
    }


async def _rules(session: AsyncSession) -> dict[str, NotificationRule | None]:
    rules = (
        (
            await session.execute(
                select(NotificationRule).where(
                    NotificationRule.event_type == EVENT_REQUEST_STUCK,
                    NotificationRule.is_active.is_(True),
                    NotificationRule.workflow_id.is_(None),
                    NotificationRule.stage_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_type = {rule.recipient_type: rule for rule in rules}
    return {
        "responsible": by_type.get("responsible"),
        "manager": by_type.get("manager_of_responsible"),
    }


def _row(
    *,
    deal: sla.DealInfo,
    stage: sla.StageInfo,
    level: str,
    days: int,
    repeat: int,
    recipient: AppUser,
    at: datetime,
    rule: NotificationRule | None,
) -> dict[str, Any]:
    undelivered = recipient.username in UNDELIVERED
    payload: dict[str, Any] = {
        "request_id": str(deal.deal_id),
        "title": deal.title,
        "status": stage.name,
        "stuck_days": days,
        "threshold_days": stage.norm_days,
        "url": f"/requests/{deal.deal_id}",
    }
    if level == "manager":
        payload["escalation"] = True
        subject = "Эскалация: заявка зависла"
        body = (
            f"⚠️ Эскалация: заявка «{deal.title}» вашего сотрудника висит на этапе "
            f"«{stage.name}» уже {days} дн. (порог {stage.norm_days} дн.)"
        )
    else:
        subject = "Заявка зависла"
        body = (
            f"🔥 Заявка «{deal.title}» висит на этапе «{stage.name}» {days} дн. "
            f"(порог {stage.norm_days} дн.)"
        )
    return {
        "id": uuid.uuid4(),
        "rule_id": rule.id if rule else None,
        "event_type": EVENT_REQUEST_STUCK,
        "entity_type": "deal",
        "entity_id": deal.deal_id,
        "recipient_user_id": recipient.id,
        "channel": "telegram",
        "address": str(recipient.id)
        if undelivered
        else str(recipient.tg_chat_id or f"demo:{recipient.username}"),
        "subject": subject,
        "body": body,
        "payload": payload,
        "dedup_key": stuck_dedup_key(deal.deal_id, stage.stage_id, level, days, repeat),
        "status": "skipped" if undelivered else "sent",
        "last_error": SKIPPED_REASON if undelivered else None,
        "sent_at": None if undelivered else at + SEND_DELAY,
        "created_at": at,
    }


async def _insert_notifications(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> int:
    inserted = 0
    for start in range(0, len(rows), INSERT_BATCH):
        batch = rows[start : start + INSERT_BATCH]
        result = await session.execute(
            pg_insert(Notification)
            .values(list(batch))
            .on_conflict_do_nothing()
            .returning(Notification.id)
        )
        inserted += len(result.all())
    return inserted


async def seed_reminders(session: AsyncSession, *, now: datetime) -> tuple[int, int, int]:
    """Журнал напоминаний и реакции по визитам стенда: (напоминаний, эскалаций, реакций)."""
    stages = await _stage_infos(session)
    window = now - timedelta(days=JOURNAL_DAYS)
    deals = await _open_deals(session, window)
    if not deals:
        return 0, 0, 0
    ids = list(deals)
    history = await sla.fetch_history(session, ids, now)
    changes = await sla.fetch_assignments(session, ids)
    rules = await _rules(session)
    repeat = _repeat_every_days(rules["responsible"])
    manager_repeat = _repeat_every_days(rules["manager"])
    users = {user.id: user for user in (await session.execute(select(AppUser))).scalars().all()}

    responsible_rows: list[dict[str, Any]] = []
    manager_rows: list[dict[str, Any]] = []
    first_delivered: list[tuple[sla.Visit, sla.DealInfo, AppUser, datetime, str]] = []
    for deal in deals.values():
        timeline = sla.OwnerTimeline(changes.get(deal.deal_id, ()), deal.responsible_id)
        visits = sla.build_visits(history.get(deal.deal_id, []), deal, stages, is_current=True)
        for visit in visits:
            due = visit.overdue_from
            if due is None or not window <= due < now:
                continue
            until = now if visit.active_end is None else min(visit.active_end, now)
            episode_start: tuple[AppUser, datetime] | None = None
            for segment in visit.segments:
                if segment.terminal:
                    break
                stage = stages.get(segment.stage_id)
                if stage is None:
                    continue
                end = until if segment.end is None else min(segment.end, until)
                for run in reminder_runs(visit.entered, stage.norm_days, repeat, until=end):
                    if run < segment.start:
                        continue
                    days = (run - visit.entered).days
                    if days <= stage.norm_days:
                        continue
                    owner = users.get(timeline.owner_at(run))
                    if owner is None or not owner.is_active:
                        continue
                    responsible_rows.append(
                        _row(
                            deal=deal,
                            stage=stage,
                            level="responsible",
                            days=days,
                            repeat=repeat,
                            recipient=owner,
                            at=run,
                            rule=rules["responsible"],
                        )
                    )
                    if episode_start is None and owner.username not in UNDELIVERED:
                        episode_start = (owner, run + SEND_DELAY)
                    manager = users.get(owner.manager_id) if owner.manager_id else None
                    if manager is not None and days >= escalation_after_days(stage.norm_days):
                        manager_rows.append(
                            _row(
                                deal=deal,
                                stage=stage,
                                level="manager",
                                days=days,
                                repeat=manager_repeat,
                                recipient=manager,
                                at=run,
                                rule=rules["manager"],
                            )
                        )
            if episode_start is not None:
                stage = stages.get(visit.last_segment.stage_id)
                first_delivered.append(
                    (visit, deal, episode_start[0], episode_start[1], stage.name if stage else "")
                )

    reminders = await _insert_notifications(session, responsible_rows)
    escalations = await _insert_notifications(session, manager_rows)
    reactions = await _seed_reactions(session, first_delivered, now=now)
    await session.flush()
    return reminders, escalations, reactions


async def _seed_reactions(
    session: AsyncSession,
    episodes: Sequence[tuple[sla.Visit, sla.DealInfo, AppUser, datetime, str]],
    *,
    now: datetime,
) -> int:
    created = 0
    for visit, deal, owner, delivered_at, stage_name in episodes:
        rng = _rng(PLAN_SEED, deal.title, visit.seq)
        delay = reaction_delay(owner.username, rng)
        if delay is None:
            continue
        moment = delivered_at + delay
        limit = now if visit.left is None else min(visit.left, now)
        if moment >= limit:
            continue
        body = f"{rng.choice(REACTION_BODIES)} (этап «{stage_name}»)"
        exists = (
            await session.execute(
                select(DealComment.id).where(
                    DealComment.deal_id == deal.deal_id, DealComment.body == body
                )
            )
        ).first()
        if exists:
            continue
        session.add(
            DealComment(deal_id=deal.deal_id, author_user_id=owner.id, body=body, created_at=moment)
        )
        created += 1
    return created


# --------------------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------------------


async def seed_sla_history(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Сценарии просрочек, передача, журнал напоминаний, реакции (идемпотентно, без commit)."""
    now = now or demo_now()
    workflows, _universities, _products, _programs, users = await _load_context(session)
    clients = await _clients(session)
    deals = transfers = 0
    for index, plan in enumerate(build_sla_plan(now)):
        created, transferred = await _create_scenario(
            session,
            plan,
            index=index,
            now=now,
            workflows=workflows,
            users=users,
            clients=clients,
        )
        deals += created
        transfers += transferred
    reminders, escalations, reactions = await seed_reminders(session, now=now)
    return {
        "deals": deals,
        "transfers": transfers,
        "reminders": reminders,
        "escalations": escalations,
        "reactions": reactions,
    }


async def run_seed_sla() -> int:
    from app.core.db import get_session_factory

    async with get_session_factory()() as session:
        users = (await session.execute(select(func.count()).select_from(AppUser))).scalar_one()
        if not users:
            print("seed_sla: сначала базовый сид — python -m app.tools.seed")  # noqa: T201
            return 1
        stats = await seed_sla_history(session)
        await session.commit()
    print(  # noqa: T201
        "seed_sla: сценарных заявок {deals}, передач {transfers}, напоминаний {reminders}, "
        "эскалаций {escalations}, реакций {reactions}".format(**stats)
    )
    return 0


def main() -> int:
    from app.core.db import dispose_engine

    async def _amain() -> int:
        try:
            return await run_seed_sla()
        finally:
            await dispose_engine()

    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
