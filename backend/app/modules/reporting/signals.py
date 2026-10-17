"""Карточка «Сигналы» на «Аналитике» (api-contract.md §7.2, ux.md §6.1): что требует
внимания руководителя прямо сейчас — `GET /reports/signals`.

Срез на текущий момент: период панели на сигналы не влияет (как у «Нагрузки по КАМам»),
остальные фильтры панели применяются там, где они осмысленны. Каждый сигнал — правило,
которое в системе уже есть, а не новая реализация:

- «Зависшие заявки» — `service._stuck_conditions` (KPI «Зависших», «Нагрузка по КАМам»,
  отчёт «Зависшие»): открытая заявка на нетерминальном этапе дольше нормы этапа (каскад
  этап → процесс), точное время от входа в этап (`stage_entered_at`; перевод схемы
  `system_migration` его не сбрасывает). Число равно фильтру доски `GET /requests?stuck=true`
  с теми же фильтрами. Измерения — как у KPI (`_apply_dims`);
- «Без ответственного КАМа» — `requests.service.unassigned_condition` (фильтр
  `GET /requests?unassigned=true`): открытая заявка, чей ответственный не ведёт портфель как
  КАМ процесса или деактивирован (лиды CMS у руководителя до распределения). Измерения — как
  у KPI;
- «Лицензии: истекают и истекли» — строки договора (`contract_product`, миграция 0008) у
  действующих договоров (`status='active'`) с вузами: «истекают» — последний день лицензии
  (`license_valid_to`, включительно) от сегодня до сегодня + `LICENSE_EXPIRING_DAYS` (МСК),
  «истекли» — последний день раньше сегодняшнего. Отменённая передача
  (`transfer_status='cancelled'`) не сигналит. Измерения: КАМ — КАМ вуза, вуз, ИТ-продукт
  строки (программа — её продукт), ИТ-направление — направления продукта (`direction_match`);
- «Вузы без активности» — `kam_activity.untouched_assigned_universities` за период отчёта
  «Активность КАМов» по умолчанию (последние 30 дней по МСК): закреплённые за КАМом вузы без
  единого взаимодействия КАМа. Измерения: процесс, КАМы, вузы, ИТ-направления — как у отчёта
  КАМов; продукт и программа к закреплению вуза неприменимы.

Область — `report_scope` (как у остальных секций дашборда): с областью «только свои» КАМ
видит свои заявки, лицензии и вузы, закреплённые за ним. Лицензии и вузы — сущности B2B:
блоки есть, только если процесс b2b в области и не выбран B2C, иначе `null`. Кэш KeyDB —
общий `build_report` (`crm:v1:dash:signals:{hash(params + scope)}`, TTL 60 с).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.access import DealScope
from app.models.crm import Contract, ContractProduct, Product, Program, University
from app.models.deal import Deal
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.crm.directions import direction_match
from app.modules.reporting import kam_activity as ka
from app.modules.reporting.service import (
    ReportParams,
    _apply_dims,
    _now,
    _panel_workflows,
    _stuck_conditions,
    _today,
)
from app.modules.requests.service import unassigned_condition
from app.modules.workflow.service import _global_stuck_default  # каскад порога (Р-5)

LICENSE_EXPIRING_DAYS = 30  # «истекают»: последний день лицензии в ближайшие N дней
SIGNAL_ITEMS_LIMIT = 50  # предохранитель списков во всплывающем окне карточки


def kam_params(params: ReportParams) -> ka.KamActivityParams:
    """Фильтры панели → параметры отчёта «Активность КАМов» без периода (по умолчанию —
    последние 30 дней): процесс, КАМы, вузы, ИТ-направления."""
    return ka.KamActivityParams(
        workflow_type=params.workflow_type,
        kam_ids=params.kam_ids,
        university_ids=params.university_ids,
        direction_ids=params.direction_ids,
    )


def _by_workflow(rows: Any, workflows: list[str]) -> dict[str, Any]:
    counts = {pipeline: int(count) for pipeline, count in rows}
    by_workflow = {workflow: counts.get(workflow, 0) for workflow in workflows}
    return {"value": sum(by_workflow.values()), "by_workflow": by_workflow}


def stuck_stmt(params: ReportParams, scope: DealScope, default_days: int) -> sa.Select:
    """Зависшие по процессам — те же условия и измерения, что у KPI «Зависших»."""
    stmt = (
        select(Deal.pipeline, func.count())
        .select_from(Deal)
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(*_stuck_conditions(default_days))
        .group_by(Deal.pipeline)
    )
    return scope.apply(_apply_dims(stmt, params))


def unassigned_stmt(params: ReportParams, scope: DealScope) -> sa.Select:
    """Без ответственного КАМа по процессам — условие фильтра `?unassigned=true`."""
    stmt = (
        select(Deal.pipeline, func.count())
        .select_from(Deal)
        .where(unassigned_condition())
        .group_by(Deal.pipeline)
    )
    return scope.apply(_apply_dims(stmt, params))


def _license_conditions(
    params: ReportParams, own: uuid.UUID | None, today: date
) -> list[sa.ColumnElement[bool]]:
    """Лицензии действующих договоров с вузами, последний день — не позже горизонта."""
    horizon = today + timedelta(days=LICENSE_EXPIRING_DAYS)
    conditions: list[sa.ColumnElement[bool]] = [
        Contract.status == "active",
        ContractProduct.license_valid_to.is_not(None),
        ContractProduct.license_valid_to <= horizon,
        ContractProduct.transfer_status.is_distinct_from("cancelled"),
    ]
    if params.kam_ids:
        conditions.append(University.kam_user_id.in_(params.kam_ids))
    if params.university_ids:
        conditions.append(University.id.in_(params.university_ids))
    if params.product_ids:
        conditions.append(ContractProduct.product_id.in_(params.product_ids))
    if params.program_ids:
        # программа — через её ИТ-продукт (у строки договора своей программы нет)
        conditions.append(
            ContractProduct.product_id.in_(
                select(Program.product_id).where(
                    Program.id.in_(params.program_ids), Program.product_id.is_not(None)
                )
            )
        )
    if params.direction_ids:
        # у строки договора нет программы — направления её ИТ-продукта
        conditions.append(
            direction_match(sa.null(), ContractProduct.product_id, params.direction_ids)
        )
    if own is not None:
        conditions.append(University.kam_user_id == own)
    return conditions


def _license_from(stmt: sa.Select) -> sa.Select:
    return (
        stmt.select_from(ContractProduct)
        .join(Contract, Contract.id == ContractProduct.contract_id)
        .join(University, University.id == Contract.university_id)
    )


def license_counts_stmt(params: ReportParams, own: uuid.UUID | None, today: date) -> sa.Select:
    expired = ContractProduct.license_valid_to < today
    stmt = _license_from(
        select(func.count().filter(sa.not_(expired)), func.count().filter(expired))
    )
    return stmt.where(*_license_conditions(params, own, today))


def license_items_stmt(
    params: ReportParams, own: uuid.UUID | None, today: date, *, expired: bool
) -> sa.Select:
    """Строки окна карточки: истекающие — ближайшие первыми, истёкшие — недавние первыми."""
    kam = aliased(AppUser, name="license_kam")
    stmt = _license_from(
        select(
            Contract.id,
            Contract.number,
            University.id,
            University.name,
            University.short_name,
            kam.id,
            kam.full_name,
            kam.username,
            Product.id,
            Product.name,
            ContractProduct.license_valid_to,
            ContractProduct.transfer_status,
        )
    )
    stmt = (
        stmt.join(Product, Product.id == ContractProduct.product_id)
        .outerjoin(kam, kam.id == University.kam_user_id)
        .where(*_license_conditions(params, own, today))
    )
    if expired:
        stmt = stmt.where(ContractProduct.license_valid_to < today).order_by(
            ContractProduct.license_valid_to.desc()
        )
    else:
        stmt = stmt.where(ContractProduct.license_valid_to >= today).order_by(
            ContractProduct.license_valid_to
        )
    return stmt.order_by(University.name, Product.name).limit(SIGNAL_ITEMS_LIMIT)


async def _licenses(
    session: AsyncSession, params: ReportParams, own: uuid.UUID | None, today: date
) -> dict[str, Any]:
    expiring, expired = (await session.execute(license_counts_stmt(params, own, today))).one()
    items: list[dict[str, Any]] = []
    for is_expired in (False, True):
        for (
            contract_id,
            number,
            university_id,
            university_name,
            university_short,
            kam_id,
            kam_full_name,
            kam_username,
            product_id,
            product_name,
            valid_to,
            transfer_status,
        ) in (
            await session.execute(license_items_stmt(params, own, today, expired=is_expired))
        ).all():
            items.append(
                {
                    "contract_id": str(contract_id),
                    "contract_number": number,
                    "university_id": str(university_id),
                    "university": university_short or university_name,
                    "kam_id": str(kam_id) if kam_id else None,
                    "kam": (kam_full_name or kam_username) if kam_id else None,
                    "product_id": str(product_id),
                    "product": product_name,
                    "valid_to": valid_to.isoformat(),
                    "days_left": (valid_to - today).days,
                    "state": "expired" if is_expired else "expiring",
                    "transfer_status": transfer_status,
                }
            )
    return {
        "expiring": int(expiring),
        "expired": int(expired),
        "horizon_days": LICENSE_EXPIRING_DAYS,
        "items": items,
        "truncated": len(items) < int(expiring) + int(expired),
    }


async def _untouched(
    session: AsyncSession, params: ka.KamActivityParams, scope: DealScope, today: date
) -> dict[str, Any]:
    report = await ka.untouched_assigned_universities(
        session, ka.resolve_params(params, today=today), scope
    )
    items = report["items"]
    return {
        "value": len(items),
        "period": report["period"],
        "items": items[:SIGNAL_ITEMS_LIMIT],
        "truncated": len(items) > SIGNAL_ITEMS_LIMIT,
    }


async def build_signals(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Тело ответа `GET /reports/signals` (конверт и кэш — `service.build_report`)."""
    now: datetime = _now()
    today = _today()
    workflows = _panel_workflows(params, scope)
    default_days = await _global_stuck_default(session)
    stuck = _by_workflow(
        (await session.execute(stuck_stmt(params, scope, default_days))).all(), workflows
    )
    unassigned = _by_workflow(
        (await session.execute(unassigned_stmt(params, scope))).all(), workflows
    )
    activity_params = kam_params(params)
    # лицензии и вузы — сущности B2B: процесс b2b в области и не выбран B2C
    b2b_visible, own = ka._b2b_rule(activity_params, scope)
    return {
        "as_of": now.isoformat(),
        "workflow_types": workflows,
        "stuck": stuck,
        "unassigned": unassigned,
        "licenses": await _licenses(session, params, own, today) if b2b_visible else None,
        "untouched_universities": (
            await _untouched(session, activity_params, scope, today) if b2b_visible else None
        ),
    }
