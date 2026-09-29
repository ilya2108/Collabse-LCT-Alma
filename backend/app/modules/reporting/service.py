"""JSON-агрегаты отчётов для дашбордов ECharts (api-contract.md §7.2, ux.md §6).

Единый формат: `{"report", "generated_at", "params", "series"|"rows"+"columns"}`.
Кэш — KeyDB `crm:v1:dash:{code}:{hash(params + visibility_scope)}`, TTL 60 с
(data-model.md §10); PNG-экспорт графиков — обязанность фронта (`getDataURL`).
Зона видимости: kam видит агрегаты только по своим заявкам; admin/head_kam/observer —
по всем (§7.2, §12.2).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.models.crm import Contract, Program, University, UniversityContact
from app.models.deal import Deal, DealStageHistory
from app.models.talent import Student
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.workflow.service import _global_stuck_default  # каскад порога (Р-5)

REPORT_CACHE_TTL_SECONDS = 60

REPORT_CODES = (
    "funnel",
    "kam-workload",
    "stuck",
    "universities-coverage",
    "programs-demand",
    "talent-pool-funnel",
    "dynamics",
    "dashboard",
)

FUNNEL_LABELS = {
    "candidate": "Кандидат",
    "studying": "Обучается",
    "graduate": "Выпускник",
    "talent_pool": "Пул талантов",
}
# фиолетовая гамма воронки студентов (ux.md §6.1, виджет 6)
FUNNEL_COLORS = {
    "candidate": "#d3adf7",
    "studying": "#b37feb",
    "graduate": "#9254de",
    "talent_pool": "#722ed1",
}


@dataclass(frozen=True)
class ReportParams:
    """Фильтры панели дашборда; неиспользуемые отчётом поля игнорируются."""

    workflow_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    kam_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    university_id: uuid.UUID | None = None
    program_id: uuid.UUID | None = None
    region: str | None = None
    federal_project_id: uuid.UUID | None = None
    threshold_days: int | None = None

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            key.replace("date_from", "from").replace("date_to", "to"): (
                str(value) if value is not None else None
            )
            for key, value in payload.items()
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def scope_kam_id(viewer: AppUser) -> uuid.UUID | None:
    """kam без «широких» ролей видит агрегаты только по своей зоне (§7.2)."""
    wide = {"admin", "head_kam", "observer"}
    if wide & set(viewer.roles or []):
        return None
    return viewer.id


def _envelope(code: str, params: ReportParams, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "report": code,
        "generated_at": _now().isoformat(),
        "params": params.as_json(),
        **body,
    }


def _deal_filters(stmt: sa.Select, params: ReportParams, scope: uuid.UUID | None) -> sa.Select:
    if params.date_from:
        stmt = stmt.where(Deal.created_at >= params.date_from)
    if params.date_to:
        stmt = stmt.where(Deal.created_at < params.date_to + timedelta(days=1))
    if params.kam_id:
        stmt = stmt.where(Deal.responsible_user_id == params.kam_id)
    if params.product_id:
        stmt = stmt.where(Deal.product_id == params.product_id)
    if params.university_id:
        stmt = stmt.where(Deal.university_id == params.university_id)
    if scope is not None:
        stmt = stmt.where(Deal.responsible_user_id == scope)
    return stmt.where(Deal.status != "archived")


async def _workflow_with_stages(
    session: AsyncSession, workflow_type: str
) -> tuple[Workflow | None, list[WorkflowStage]]:
    workflow = (
        await session.execute(select(Workflow).where(Workflow.code == workflow_type))
    ).scalar_one_or_none()
    if workflow is None:
        return None, []
    stages = (
        (
            await session.execute(
                select(WorkflowStage)
                .where(
                    WorkflowStage.workflow_id == workflow.id,
                    WorkflowStage.deleted_at.is_(None),
                )
                .order_by(WorkflowStage.sort_order)
            )
        )
        .scalars()
        .all()
    )
    return workflow, list(stages)


# --------------------------------------------------------------------------------------
# Отчёты
# --------------------------------------------------------------------------------------


async def report_funnel(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    workflow_type = params.workflow_type or "b2b"
    workflow, stages = await _workflow_with_stages(session, workflow_type)
    if workflow is None:
        return {"series": []}
    stmt = _deal_filters(
        select(Deal.stage_id, func.count(), func.coalesce(func.sum(Deal.amount), 0)).where(
            Deal.workflow_id == workflow.id
        ),
        params,
        scope,
    ).group_by(Deal.stage_id)
    counts = {
        stage_id: (count, amount)
        for stage_id, count, amount in (await session.execute(stmt)).all()
    }
    series = []
    for stage in stages:
        count, amount = counts.get(stage.id, (0, 0))
        series.append(
            {
                "key": stage.code,
                "label": stage.name,
                "color": stage.color,
                "value": int(count),
                "amount": format(amount, "f"),
            }
        )
    return {"series": series}


async def report_kam_workload(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    users_stmt = select(AppUser).where(AppUser.roles.any("kam"), AppUser.is_active.is_(True))
    if scope is not None:
        users_stmt = users_stmt.where(AppUser.id == scope)
    kams = list((await session.execute(users_stmt)).scalars().all())

    base = (
        select(
            Deal.responsible_user_id,
            Deal.stage_id,
            WorkflowStage.code,
            WorkflowStage.name,
            WorkflowStage.color,
            func.count(),
        )
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(Deal.status == "open")
        .group_by(
            Deal.responsible_user_id,
            Deal.stage_id,
            WorkflowStage.code,
            WorkflowStage.name,
            WorkflowStage.color,
        )
    )
    if params.workflow_type:
        base = base.where(Workflow.code == params.workflow_type)
    by_stage: dict[uuid.UUID, list[dict[str, Any]]] = {}
    totals: dict[uuid.UUID, int] = {}
    for user_id, stage_id, code, name, color, count in (await session.execute(base)).all():
        by_stage.setdefault(user_id, []).append(
            {
                "status_id": str(stage_id),
                "code": code,
                "label": name,
                "color": color,
                "count": int(count),
            }
        )
        totals[user_id] = totals.get(user_id, 0) + int(count)

    default_days = await _global_stuck_default(session)
    stuck_stmt = (
        select(Deal.responsible_user_id, func.count())
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(
            Deal.status == "open",
            WorkflowStage.is_terminal.is_(False),
            func.now() - Deal.stage_entered_at
            > func.make_interval(
                0, 0, 0,
                func.coalesce(
                    WorkflowStage.stuck_threshold_days,
                    Workflow.stuck_threshold_days,
                    default_days,
                ),
            ),
        )
        .group_by(Deal.responsible_user_id)
    )
    if params.workflow_type:
        stuck_stmt = stuck_stmt.where(Workflow.code == params.workflow_type)
    stuck = dict((await session.execute(stuck_stmt)).all())

    uni_stmt = (
        select(Deal.responsible_user_id, func.count(func.distinct(Deal.university_id)))
        .where(Deal.status == "open", Deal.university_id.is_not(None))
        .group_by(Deal.responsible_user_id)
    )
    universities = dict((await session.execute(uni_stmt)).all())

    rows = [
        {
            "kam_id": str(kam.id),
            "kam": kam.full_name or kam.username,
            "kam_name": kam.full_name or kam.username,
            "active_requests": totals.get(kam.id, 0),
            "universities": int(universities.get(kam.id, 0)),
            "stuck": int(stuck.get(kam.id, 0)),
            "by_status": sorted(
                by_stage.get(kam.id, []), key=lambda item: item["label"]
            ),
        }
        for kam in kams
    ]
    rows.sort(key=lambda item: -item["active_requests"])
    return {
        "rows": rows,
        "columns": [
            {"key": "kam", "label": "КАМ"},
            {"key": "active_requests", "label": "Активные заявки"},
            {"key": "universities", "label": "Вузы в работе"},
            {"key": "stuck", "label": "Зависшие"},
        ],
    }


async def report_stuck(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    default_days = await _global_stuck_default(session)
    threshold_expr = (
        sa.literal(params.threshold_days)
        if params.threshold_days is not None
        else func.coalesce(
            WorkflowStage.stuck_threshold_days,
            Workflow.stuck_threshold_days,
            default_days,
        )
    )
    stmt = (
        select(Deal, WorkflowStage, Workflow, AppUser, threshold_expr.label("threshold"))
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .join(AppUser, AppUser.id == Deal.responsible_user_id)
        .where(
            Deal.status == "open",
            WorkflowStage.is_terminal.is_(False),
            func.now() - Deal.stage_entered_at > func.make_interval(0, 0, 0, threshold_expr),
        )
        .order_by(Deal.stage_entered_at)
        .limit(500)
    )
    if params.workflow_type:
        stmt = stmt.where(Workflow.code == params.workflow_type)
    if scope is not None:
        stmt = stmt.where(Deal.responsible_user_id == scope)
    if params.kam_id:
        stmt = stmt.where(Deal.responsible_user_id == params.kam_id)
    now = _now()
    rows = []
    for deal, stage, _workflow, assignee, threshold in (await session.execute(stmt)).all():
        days = max((now - deal.stage_entered_at).days, 0)
        rows.append(
            {
                "request_id": str(deal.id),
                "title": deal.title,
                "workflow_type": deal.pipeline,
                "status": {"id": str(stage.id), "code": stage.code, "name": stage.name},
                "assignee": {
                    "id": str(assignee.id),
                    "full_name": assignee.full_name or assignee.username,
                },
                "stuck_days": days,
                "threshold_days": int(threshold),
                "status_updated_at": deal.stage_entered_at.isoformat(),
            }
        )
    return {
        "rows": rows,
        "columns": [
            {"key": "title", "label": "Заявка"},
            {"key": "status", "label": "Этап"},
            {"key": "assignee", "label": "Ответственный"},
            {"key": "stuck_days", "label": "Дней на этапе"},
            {"key": "threshold_days", "label": "Порог, дней"},
        ],
    }


async def report_universities_coverage(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    active_contract = (
        select(Contract.id)
        .where(Contract.university_id == University.id, Contract.status == "active")
        .exists()
    )
    open_deal = (
        select(Deal.id)
        .where(Deal.university_id == University.id, Deal.status == "open")
        .exists()
    )
    has_contact = (
        select(UniversityContact.id)
        .where(UniversityContact.university_id == University.id)
        .exists()
    )
    # одно labeled-выражение и в SELECT, и в GROUP BY: два независимых
    # func.coalesce(...) под asyncpg рендерятся разными bind-параметрами
    # ($1 и $4), и Postgres отвечает GroupingError «must appear in GROUP BY»
    region_expr = func.coalesce(University.region, "Без региона").label("region")
    stmt = (
        select(
            region_expr,
            func.count(),
            func.count().filter(active_contract),
            func.count().filter(open_deal),
            func.count().filter(~has_contact),
        )
        .where(University.is_active.is_(True))
        .group_by(region_expr)
        .order_by(func.count().desc())
    )
    if params.region:
        stmt = stmt.where(University.region == params.region)
    rows = [
        {
            "region": region,
            "total": int(total),
            "with_active_contract": int(with_contract),
            "in_progress": int(in_progress),
            "without_contact": int(without_contact),
        }
        for region, total, with_contract, in_progress, without_contact in (
            await session.execute(stmt)
        ).all()
    ]
    return {
        "rows": rows,
        "columns": [
            {"key": "region", "label": "Регион"},
            {"key": "total", "label": "Вузов"},
            {"key": "with_active_contract", "label": "С активным договором"},
            {"key": "in_progress", "label": "В работе"},
            {"key": "without_contact", "label": "Без контакта"},
        ],
    }


async def report_programs_demand(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    requests_count = (
        select(func.count())
        .where(Deal.program_id == Program.id, Deal.status != "archived")
        .scalar_subquery()
    )
    open_count = (
        select(func.count())
        .where(Deal.program_id == Program.id, Deal.status == "open")
        .scalar_subquery()
    )
    stmt = (
        select(Program, requests_count, open_count)
        .where(Program.is_active.is_(True))
        .order_by(Program.priority_rank, Program.name)
        .limit(20)
    )
    if params.product_id:
        stmt = stmt.where(Program.product_id == params.product_id)
    rows = [
        {
            "program_id": str(program.id),
            "name": program.name,
            "priority_rank": program.priority_rank,
            "requests_count": int(total),
            "open_requests": int(open_),
        }
        for program, total, open_ in (await session.execute(stmt)).all()
    ]
    return {
        "rows": rows,
        "columns": [
            {"key": "name", "label": "Программа"},
            {"key": "priority_rank", "label": "Приоритет (меньше = выше)"},
            {"key": "requests_count", "label": "Заявок всего"},
            {"key": "open_requests", "label": "Заявок открыто"},
        ],
    }


async def report_talent_pool_funnel(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    stmt = select(Student.funnel_status, func.count()).group_by(Student.funnel_status)
    if params.university_id:
        stmt = stmt.where(Student.university_id == params.university_id)
    if params.federal_project_id:
        stmt = stmt.where(Student.federal_project_id == params.federal_project_id)
    counts = dict((await session.execute(stmt)).all())
    series = [
        {
            "key": status,
            "label": FUNNEL_LABELS[status],
            "color": FUNNEL_COLORS[status],
            "value": int(counts.get(status, 0)),
        }
        for status in ("candidate", "studying", "graduate", "talent_pool")
    ]
    return {"series": series}


async def report_dynamics(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    date_to = params.date_to or _now().date()
    date_from = params.date_from or (date_to - timedelta(weeks=12))

    created_stmt = _deal_filters(
        select(func.date_trunc("week", Deal.created_at).label("week"), func.count()),
        ReportParams(
            workflow_type=params.workflow_type,
            date_from=date_from,
            date_to=date_to,
            kam_id=params.kam_id,
            product_id=params.product_id,
        ),
        scope,
    ).group_by(sa.text("week"))
    if params.workflow_type:
        created_stmt = created_stmt.join(
            Workflow, Workflow.id == Deal.workflow_id
        ).where(Workflow.code == params.workflow_type)
    created_rows = (await session.execute(created_stmt)).all()
    created = {week.date(): int(count) for week, count in created_rows}

    closed_stmt = (
        select(func.date_trunc("week", DealStageHistory.created_at).label("week"), func.count())
        .join(WorkflowStage, WorkflowStage.id == DealStageHistory.to_stage_id)
        .join(Deal, Deal.id == DealStageHistory.deal_id)
        .where(
            WorkflowStage.is_terminal.is_(True),
            DealStageHistory.created_at >= date_from,
            DealStageHistory.created_at < date_to + timedelta(days=1),
        )
        .group_by(sa.text("week"))
    )
    if params.workflow_type:
        closed_stmt = closed_stmt.join(
            Workflow, Workflow.id == Deal.workflow_id
        ).where(Workflow.code == params.workflow_type)
    if scope is not None:
        closed_stmt = closed_stmt.where(Deal.responsible_user_id == scope)
    if params.kam_id:
        closed_stmt = closed_stmt.where(Deal.responsible_user_id == params.kam_id)
    closed = {week.date(): int(count) for week, count in (await session.execute(closed_stmt)).all()}

    # непрерывная сетка недель от начала периода
    weeks: list[date] = []
    cursor = date_from - timedelta(days=date_from.weekday())
    while cursor <= date_to:
        weeks.append(cursor)
        cursor += timedelta(weeks=1)
    series = [
        {
            "key": "created",
            "label": "Создано",
            "points": [
                {"date": week.isoformat(), "value": created.get(week, 0)} for week in weeks
            ],
        },
        {
            "key": "closed",
            "label": "Завершено",
            "points": [
                {"date": week.isoformat(), "value": closed.get(week, 0)} for week in weeks
            ],
        },
    ]
    return {"series": series}


async def _kpi(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    """KPI-строка дашборда: активные, завершено за период, конверсия, зависшие."""
    active_stmt = select(func.count()).select_from(Deal).where(Deal.status == "open")
    if params.workflow_type:
        active_stmt = active_stmt.join(Workflow, Workflow.id == Deal.workflow_id).where(
            Workflow.code == params.workflow_type
        )
    if scope is not None:
        active_stmt = active_stmt.where(Deal.responsible_user_id == scope)
    if params.kam_id:
        active_stmt = active_stmt.where(Deal.responsible_user_id == params.kam_id)
    active = (await session.execute(active_stmt)).scalar_one()

    async def _closed_between(status: str, start: date | None, end: date | None) -> int:
        stmt = select(func.count()).select_from(Deal).where(Deal.status == status)
        if params.workflow_type:
            stmt = stmt.join(Workflow, Workflow.id == Deal.workflow_id).where(
                Workflow.code == params.workflow_type
            )
        if scope is not None:
            stmt = stmt.where(Deal.responsible_user_id == scope)
        if params.kam_id:
            stmt = stmt.where(Deal.responsible_user_id == params.kam_id)
        if start:
            stmt = stmt.where(Deal.updated_at >= start)
        if end:
            stmt = stmt.where(Deal.updated_at < end + timedelta(days=1))
        return int((await session.execute(stmt)).scalar_one())

    date_to = params.date_to or _now().date()
    date_from = params.date_from or (date_to - timedelta(days=30))
    period_days = max((date_to - date_from).days, 1)
    prev_from = date_from - timedelta(days=period_days)
    prev_to = date_from - timedelta(days=1)

    won = await _closed_between("won", date_from, date_to)
    lost = await _closed_between("lost", date_from, date_to)
    prev_won = await _closed_between("won", prev_from, prev_to)
    prev_lost = await _closed_between("lost", prev_from, prev_to)
    closed_total = won + lost
    conversion = round(won * 100 / closed_total, 1) if closed_total else None
    prev_closed_total = prev_won + prev_lost
    prev_conversion = (
        round(prev_won * 100 / prev_closed_total, 1) if prev_closed_total else None
    )

    stuck_body = await report_kam_workload(session, params, scope)
    stuck_total = sum(row["stuck"] for row in stuck_body["rows"])

    # форма фронтенда (frontend/src/shared/api/types.ts DashboardKpi):
    # каждая метрика — {value, delta}; delta = null, когда сравнивать не с чем
    return {
        "active": {"value": int(active), "delta": None},
        "completed": {"value": won, "delta": won - prev_won},
        "conversion": {
            "value": conversion if conversion is not None else 0.0,
            "delta": round(conversion - prev_conversion, 1)
            if conversion is not None and prev_conversion is not None
            else None,
        },
        "stuck": {"value": stuck_total, "delta": None},
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
    }


async def report_dashboard(
    session: AsyncSession, params: ReportParams, scope: uuid.UUID | None
) -> dict[str, Any]:
    """Агрегат всех виджетов одним запросом (ux.md §6.3)."""
    # секции — в форме frontend DashboardReport: funnel/dynamics/talent_pool_funnel
    # оборачиваются в {series}, kam_workload/programs_demand — в {rows}
    return {
        "kpi": await _kpi(session, params, scope),
        "funnel": {"series": (await report_funnel(session, params, scope))["series"]},
        "dynamics": {"series": (await report_dynamics(session, params, scope))["series"]},
        "kam_workload": {
            "rows": (await report_kam_workload(session, params, scope))["rows"]
        },
        "programs_demand": {
            "rows": (await report_programs_demand(session, params, scope))["rows"]
        },
        "talent_pool_funnel": {
            "series": (await report_talent_pool_funnel(session, params, scope))["series"]
        },
    }


_BUILDERS = {
    "funnel": report_funnel,
    "kam-workload": report_kam_workload,
    "stuck": report_stuck,
    "universities-coverage": report_universities_coverage,
    "programs-demand": report_programs_demand,
    "talent-pool-funnel": report_talent_pool_funnel,
    "dynamics": report_dynamics,
    "dashboard": report_dashboard,
}


def _cache_suffix(code: str, params: ReportParams, scope: uuid.UUID | None) -> str:
    digest = hashlib.sha1(
        orjson.dumps({"params": params.as_json(), "scope": str(scope)}, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    return f"dash:{code}:{digest}"


async def build_report(
    session: AsyncSession, code: str, viewer: AppUser, params: ReportParams
) -> dict[str, Any]:
    """Отчёт с кэшем cache-aside (TTL 60 с); недоступный KeyDB — чтение из PG."""
    builder = _BUILDERS[code]
    scope = scope_kam_id(viewer)
    cache = get_cache()
    suffix = _cache_suffix(code, params, scope)
    cached = await cache.get_json(suffix)
    if cached is not None:
        return cached
    body = await builder(session, params, scope)
    result = _envelope(code, params, body)
    await cache.set_json(suffix, result, REPORT_CACHE_TTL_SECONDS)
    return result
