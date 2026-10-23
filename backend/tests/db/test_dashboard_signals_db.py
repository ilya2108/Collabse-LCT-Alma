"""Карточка «Сигналы» на «Аналитике» (`GET /reports/signals`) на PostgreSQL.

Каждый сигнал — правило, которое уже есть на других экранах, поэтому проверяется сверкой:
- «Зависшие» — ровно фильтр доски `GET /requests?stuck=true` на тех же данных и фильтрах
  (по процессам, с фильтром вуза, у КАМа — только свои);
- «Без ответственного КАМа» — ровно `GET /requests?unassigned=true` (лид у руководителя,
  заявка у администратора, у уволенного КАМа, у роли «КАМ B2C» в процессе B2B);
- «Лицензии» — границы горизонта 29/30/31 день, «сегодня последний день», истёкшая;
  отменённая передача и недействующий договор не сигналят;
- «Вузы без активности» — те же вузы, что «Закреплены, нет контакта» сводки «Активности
  КАМов» за тот же период;
- область: КАМ видит свои заявки, лицензии и вузы; роль только B2C и выбранный B2C —
  без лицензий и вузов (`null`); пустая база — нули.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import reset_role_catalog
from app.models import AppUser, Workflow, WorkflowStage
from app.models.crm import Contract, ContractProduct, Product, University
from app.modules.reporting.service import REPORT_TZ, ReportParams, build_report
from app.modules.reporting.signals import LICENSE_EXPIRING_DAYS
from app.tools import seed

from . import factories
from .rbac_factories import KAM_PERMISSIONS, make_role

API = "/api/v1"
Workflows = dict[str, tuple[Workflow, dict[str, WorkflowStage]]]


def ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def today() -> date:
    return datetime.now(REPORT_TZ).date()


async def _get(client: Any, path: str, params: Any = None) -> dict[str, Any]:
    response = await client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _ids(client: Any, params: dict[str, Any]) -> set[str]:
    body = await _get(client, "/requests", {"limit": 200, **params})
    assert body["total"] == len(body["items"])
    return {item["id"] for item in body["items"]}


@pytest.fixture
async def people(db_session) -> dict[str, AppUser]:  # noqa: ANN001
    head = await factories.make_user(
        db_session, roles=("head_kam",), full_name="Морозов Дмитрий Александрович"
    )
    kam = await factories.make_user(
        db_session, roles=("kam",), full_name="Петров Константин Андреевич", manager=head
    )
    kam2 = await factories.make_user(
        db_session, roles=("kam",), full_name="Иванова Мария Сергеевна", manager=head
    )
    return {"head": head, "kam": kam, "kam2": kam2}


async def _b2b(session, workflows: Workflows, kam: AppUser, path: list[tuple], **kw) -> Any:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    return await factories.make_deal(
        session, workflow=wf, stages=stages, responsible=kam, created_at=path[0][1], path=path, **kw
    )


async def _b2c(session, workflows: Workflows, owner: AppUser, path: list[tuple], **kw) -> Any:  # noqa: ANN001
    wf, stages = workflows["b2c"]
    return await factories.make_deal(
        session,
        workflow=wf,
        stages=stages,
        responsible=owner,
        created_at=path[0][1],
        path=path,
        **kw,
    )


# --- 1. «Зависшие» = фильтр доски --------------------------------------------------------


async def test_stuck_equals_board_filter(api, db_session, workflows: Workflows, people) -> None:  # noqa: ANN001
    kam, kam2, head = people["kam"], people["kam2"], people["head"]
    over = await _b2b(db_session, workflows, kam, [("new", ago(30)), ("first_contact", ago(14.5))])
    await _b2b(db_session, workflows, kam, [("new", ago(30)), ("first_contact", ago(13.5))])
    await _b2b(db_session, workflows, kam, [("new", ago(7.5))])  # норма этапа 7 — зависла
    await _b2b(db_session, workflows, kam, [("new", ago(60)), ("completed", ago(40))])
    await _b2b(db_session, workflows, kam2, [("new", ago(40)), ("negotiation", ago(25))])
    await _b2c(db_session, workflows, kam2, [("new", ago(30)), ("proposal", ago(16))])
    await _b2c(db_session, workflows, kam, [("new", ago(3))])

    client = api.as_user(head)
    signals = await _get(client, "/reports/signals")
    assert signals["report"] == "signals"
    assert signals["workflow_types"] == ["b2b", "b2c"]
    board = await _ids(client, {"stuck": "true"})
    assert signals["stuck"]["value"] == len(board) == 4
    for workflow_type in ("b2b", "b2c"):
        per_board = await _ids(client, {"stuck": "true", "workflow_type": workflow_type})
        assert signals["stuck"]["by_workflow"][workflow_type] == len(per_board)
    # KPI «Зависших» дашборда — то же число
    dashboard = await _get(client, "/reports/dashboard")
    assert dashboard["kpi"]["stuck"]["value"] == signals["stuck"]["value"]

    # фильтр панели переносится на доску: вуз и процесс
    uni = str(over.university_id)
    filtered = await _get(client, "/reports/signals", {"university_id": uni})
    assert filtered["stuck"]["value"] == len(
        await _ids(client, {"stuck": "true", "university_id": uni})
    )
    only_b2c = await _get(client, "/reports/signals", {"workflow_type": "b2c"})
    assert only_b2c["workflow_types"] == ["b2c"]
    assert only_b2c["stuck"] == {"value": 1, "by_workflow": {"b2c": 1}}

    # период панели на сигналы не влияет — срез «сейчас»
    period = {"from": (today() - timedelta(days=3)).isoformat(), "to": today().isoformat()}
    assert (await _get(client, "/reports/signals", period))["stuck"] == signals["stuck"]

    # КАМ видит в отчётах только свои заявки (доска ему показывает все), поэтому переход
    # с карточки ведёт на доску с фильтром «Только мои» — там те же заявки
    own = api.as_user(kam)
    own_signals = await _get(own, "/reports/signals")
    own_board = await _ids(own, {"stuck": "true", "assignee_id": str(kam.id)})
    assert own_signals["stuck"]["value"] == len(own_board) == 2
    assert len(await _ids(own, {"stuck": "true"})) == 4


# --- 2. «Без ответственного КАМа» = фильтр `?unassigned=true` -----------------------------


async def test_unassigned_equals_list_filter(
    api, db_session, workflows: Workflows, people, admin_user
) -> None:  # noqa: ANN001
    kam, head = people["kam"], people["head"]
    await make_role(
        db_session, code="kam_b2c", permissions=KAM_PERMISSIONS, workflow_types=("b2c",)
    )
    reset_role_catalog()
    kam_c = await factories.make_user(db_session, roles=("kam_b2c",))
    left = await factories.make_user(db_session, roles=("kam",), is_active=False)

    assigned = await _b2b(db_session, workflows, kam, [("new", ago(2))])
    own_b2c = await _b2c(db_session, workflows, kam_c, [("new", ago(2))])  # КАМ B2C в B2C
    lead = await _b2c(db_session, workflows, head, [("new", ago(1))], source="cms")
    at_admin = await _b2b(db_session, workflows, admin_user, [("new", ago(3))])
    at_left = await _b2b(db_session, workflows, left, [("new", ago(20)), ("first_contact", ago(9))])
    wrong_process = await _b2b(db_session, workflows, kam_c, [("new", ago(4))])
    closed_lead = await _b2c(db_session, workflows, head, [("new", ago(30)), ("rejected", ago(20))])
    expected = {str(d.id) for d in (lead, at_admin, at_left, wrong_process)}
    every = expected | {str(d.id) for d in (assigned, own_b2c, closed_lead)}

    client = api.as_user(head)
    assert await _ids(client, {"unassigned": "true"}) == expected
    assert await _ids(client, {"unassigned": "false"}) == every - expected
    signals = await _get(client, "/reports/signals")
    assert signals["unassigned"] == {"value": 4, "by_workflow": {"b2b": 3, "b2c": 1}}
    b2c_board = await _ids(client, {"unassigned": "true", "workflow_type": "b2c"})
    assert b2c_board == {str(lead.id)}

    # фильтр КАМа на панели: заявки выбранных сотрудников
    by_left = await _get(client, "/reports/signals", {"kam_id": str(left.id)})
    assert by_left["unassigned"]["value"] == 1

    # КАМ видит только свои заявки — свои у него всегда распределены
    own = await _get(api.as_user(kam), "/reports/signals")
    assert own["unassigned"]["value"] == 0


# --- 3. «Лицензии»: границы горизонта ----------------------------------------------------


async def _product(session: AsyncSession, name: str) -> Product:
    product = Product(code=f"p-{uuid.uuid4().hex[:8]}", name=name)
    session.add(product)
    await session.flush()
    return product


async def _contract(
    session: AsyncSession,
    university: University,
    lines: list[tuple[Product, int | None, str | None]],
    *,
    status: str = "active",
) -> Contract:
    contract = Contract(
        number=f"Т-{uuid.uuid4().hex[:6]}", university_id=university.id, status=status
    )
    session.add(contract)
    await session.flush()
    for product, days, transfer_status in lines:
        valid_to = today() + timedelta(days=days) if days is not None else None
        session.add(
            ContractProduct(
                contract_id=contract.id,
                product_id=product.id,
                license_signed_at=valid_to - timedelta(days=364) if valid_to else None,
                license_term_years=1 if valid_to else None,
                license_valid_to=valid_to,
                transfer_status=transfer_status,
            )
        )
    await session.flush()
    return contract


@pytest.fixture
async def licenses(db_session, people) -> dict[str, Any]:  # noqa: ANN001
    kam, kam2 = people["kam"], people["kam2"]
    mine = await factories.make_university(db_session, name="МФТИ", short_name="МФТИ", kam=kam)
    other = await factories.make_university(db_session, name="ТГУ", kam=kam2)
    products = {
        name: await _product(db_session, name)
        for name in ("d29", "d30", "d31", "d0", "d-1", "cancel", "none", "draft", "other")
    }
    p = products
    await _contract(
        db_session,
        mine,
        [
            (p["d29"], 29, "transferred"),
            (p["d30"], LICENSE_EXPIRING_DAYS, "in_progress"),
            (p["d31"], LICENSE_EXPIRING_DAYS + 1, None),
            (p["d0"], 0, None),  # сегодня последний день — ещё истекает
            (p["d-1"], -1, "transferred"),  # вчера — истекла
            (p["cancel"], 5, "cancelled"),  # передача отменена — не сигналит
            (p["none"], None, None),  # без даты окончания
        ],
    )
    await _contract(db_session, mine, [(p["draft"], 3, None)], status="draft")
    await _contract(db_session, other, [(p["other"], 3, "not_started")])
    return {"mine": mine, "other": other, "products": products}


async def test_license_boundaries_and_scope(api, people, licenses) -> None:  # noqa: ANN001
    p = licenses["products"]
    head = api.as_user(people["head"])
    signal = (await _get(head, "/reports/signals"))["licenses"]
    assert signal["horizon_days"] == LICENSE_EXPIRING_DAYS == 30
    assert (signal["expiring"], signal["expired"]) == (4, 1)
    assert signal["truncated"] is False
    days = {item["product_id"]: item["days_left"] for item in signal["items"]}
    assert days == {
        str(p["d0"].id): 0,
        str(p["other"].id): 3,
        str(p["d29"].id): 29,
        str(p["d30"].id): 30,
        str(p["d-1"].id): -1,
    }
    # истекающие — ближайшие первыми, затем истёкшие
    assert [item["state"] for item in signal["items"]] == ["expiring"] * 4 + ["expired"]
    assert [item["days_left"] for item in signal["items"][:4]] == [0, 3, 29, 30]
    first = signal["items"][0]
    assert first["university"] == "МФТИ" and first["kam"] == people["kam"].full_name
    assert first["valid_to"] == today().isoformat()

    # фильтры панели: ИТ-продукт строки договора, КАМ — КАМ вуза, вуз
    by_product = await _get(head, "/reports/signals", {"product_id": str(p["d29"].id)})
    assert (by_product["licenses"]["expiring"], by_product["licenses"]["expired"]) == (1, 0)
    by_kam = await _get(head, "/reports/signals", {"kam_id": str(people["kam2"].id)})
    assert (by_kam["licenses"]["expiring"], by_kam["licenses"]["expired"]) == (1, 0)
    by_uni = await _get(head, "/reports/signals", {"university_id": str(licenses["mine"].id)})
    assert (by_uni["licenses"]["expiring"], by_uni["licenses"]["expired"]) == (3, 1)

    # КАМ — только вузы, закреплённые за ним
    own = (await _get(api.as_user(people["kam"]), "/reports/signals"))["licenses"]
    assert (own["expiring"], own["expired"]) == (3, 1)
    assert {item["university_id"] for item in own["items"]} == {str(licenses["mine"].id)}

    # выбран B2C — лицензии (сущность B2B) не показываются
    b2c = await _get(head, "/reports/signals", {"workflow_type": "b2c"})
    assert b2c["licenses"] is None and b2c["untouched_universities"] is None


# --- 4. «Вузы без активности» = «Закреплены, нет контакта» -------------------------------


async def test_untouched_universities_match_kam_report(
    api, db_session, workflows: Workflows, people
) -> None:  # noqa: ANN001
    kam, kam2, head = people["kam"], people["kam2"], people["head"]
    talked = await factories.make_university(db_session, name="Вуз с комментарием", kam=kam)
    met = await factories.make_university(db_session, name="Вуз с мероприятием", kam=kam)
    silent = await factories.make_university(db_session, name="Вуз без контакта", kam=kam)
    old = await factories.make_university(db_session, name="Вуз с давним контактом", kam=kam)
    foreign = await factories.make_university(db_session, name="Чужой вуз", kam=kam2)

    deal = await _b2b(db_session, workflows, kam, [("new", ago(60))], university=talked)
    await factories.make_comment(db_session, deal=deal, author=kam, at=ago(5))
    await factories.make_activity(db_session, university=met, responsible=kam, slots=[ago(3)])
    old_deal = await _b2b(db_session, workflows, kam, [("new", ago(90))], university=old)
    await factories.make_comment(db_session, deal=old_deal, author=kam, at=ago(40))

    client = api.as_user(head)
    signal = (await _get(client, "/reports/signals"))["untouched_universities"]
    names = [item["name"] for item in signal["items"]]
    assert names == ["Вуз без контакта", "Вуз с давним контактом", "Чужой вуз"]
    assert signal["value"] == 3 and signal["truncated"] is False
    assert signal["period"]["days"] == 30
    by_name = {item["name"]: item for item in signal["items"]}
    assert by_name["Вуз без контакта"]["last_contact_at"] is None
    assert by_name["Вуз с давним контактом"]["last_contact_at"] is not None
    assert by_name["Чужой вуз"]["kam_id"] == str(kam2.id)
    assert {item["university_id"] for item in signal["items"]} == {
        str(silent.id),
        str(old.id),
        str(foreign.id),
    }

    # то же правило и тот же период, что у сводки «Активности КАМов»
    summary = await _get(client, "/reports/kam-activity/summary")
    assert summary["untouched_assigned"] == names
    assert summary["period"]["from"] == signal["period"]["from"]

    # КАМ — только свои вузы; фильтр панели «КАМ» — вузы выбранного КАМа
    own = (await _get(api.as_user(kam), "/reports/signals"))["untouched_universities"]
    assert [item["name"] for item in own["items"]] == names[:2]
    by_kam = await _get(client, "/reports/signals", {"kam_id": str(kam2.id)})
    assert by_kam["untouched_universities"]["value"] == 1


# --- 5. роль только B2C и пустая база ----------------------------------------------------


async def test_b2c_role_sees_no_b2b_signals(api, db_session, workflows: Workflows, people) -> None:  # noqa: ANN001
    await make_role(
        db_session, code="kam_b2c", permissions=KAM_PERMISSIONS, workflow_types=("b2c",)
    )
    reset_role_catalog()
    kam_c = await factories.make_user(db_session, roles=("kam_b2c",))
    await _b2c(db_session, workflows, kam_c, [("new", ago(30)), ("proposal", ago(16))])
    await _b2b(db_session, workflows, people["kam"], [("new", ago(30)), ("first_contact", ago(20))])
    await factories.make_university(db_session, name="Закреплённый вуз", kam=kam_c)

    signals = await _get(api.as_user(kam_c), "/reports/signals")
    assert signals["workflow_types"] == ["b2c"]
    assert signals["stuck"] == {"value": 1, "by_workflow": {"b2c": 1}}
    assert signals["licenses"] is None
    assert signals["untouched_universities"] is None


async def test_empty_database_all_calm(db_session, workflows: Workflows, people) -> None:  # noqa: ANN001
    report = await build_report(db_session, "signals", people["head"], ReportParams())
    assert report["stuck"] == {"value": 0, "by_workflow": {"b2b": 0, "b2c": 0}}
    assert report["unassigned"] == {"value": 0, "by_workflow": {"b2b": 0, "b2c": 0}}
    assert report["licenses"] == {
        "expiring": 0,
        "expired": 0,
        "horizon_days": 30,
        "items": [],
        "truncated": False,
    }
    untouched = report["untouched_universities"]
    assert (untouched["value"], untouched["items"], untouched["truncated"]) == (0, [], False)


# --- 6. демо-стенд: каждый сигнал ненулевой и правдоподобный ------------------------------


async def test_demo_seed_signals_are_plausible(
    api, db_session, db_session_factory, memory_storage, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(seed, "get_session_factory", lambda: db_session_factory)
    monkeypatch.setattr(seed, "_NOW", factories.utcnow().replace(microsecond=0))
    await seed.run_seed()

    head = (
        await db_session.execute(sa.select(AppUser).where(AppUser.username == "head@demo"))
    ).scalar_one()
    client = api.as_user(head)
    signals = await _get(client, "/reports/signals")
    stuck = signals["stuck"]["value"]
    unassigned = signals["unassigned"]["value"]
    lic = signals["licenses"]
    untouched = signals["untouched_universities"]
    assert stuck == len(await _ids(client, {"stuck": "true"})) > 0
    assert unassigned == len(await _ids(client, {"unassigned": "true"})) == 2
    assert 2 <= lic["expiring"] <= 4 and 1 <= lic["expired"] <= 2
    assert 1 <= untouched["value"] <= 15
    names = {item["name"] for item in untouched["items"]}
    assert {"ИТМО", "ТГУ"} <= names  # закреплены, контакта не было (seed_activity)
