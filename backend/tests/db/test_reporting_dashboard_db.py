"""«Аналитика» на PostgreSQL: единый слой фильтров, нагрузка КАМов, «Завершено», процессы,
востребованность программ (три показателя ТЗ), студенты.

Проверяется то, что видит проверяющий глазами: выбрал два вуза — цифры каждой секции
равны сумме по каждому вузу (а не «последнему»); «Все» — оба процесса, а не молча B2B;
KPI «Завершено» равно сумме недельных точек «Завершено» динамики за тот же период;
программы упорядочены по востребованности, а не ручным топ-20.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppUser, Deal, DealStageHistory, Workflow, WorkflowStage
from app.models.crm import Product, Program, University
from app.models.talent import Student, StudentStatusHistory
from app.modules.admin.service import set_flag
from app.modules.reporting.service import (
    REPORT_TZ,
    ReportParams,
    build_report,
    report_programs_demand,
)

from . import factories

Workflows = dict[str, tuple[Workflow, dict[str, WorkflowStage]]]

NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(REPORT_TZ).date()
PERIOD = {"from": (TODAY - timedelta(days=60)).isoformat(), "to": TODAY.isoformat()}


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


async def make_product(session: AsyncSession, name: str) -> Product:
    product = Product(code=f"p-{uuid.uuid4().hex[:8]}", name=name)
    session.add(product)
    await session.flush()
    return product


async def make_program(
    session: AsyncSession, name: str, *, product: Product | None = None, rank: int = 1000
) -> Program:
    program = Program(name=name, product_id=product.id if product else None, priority_rank=rank)
    session.add(program)
    await session.flush()
    return program


async def make_student(
    session: AsyncSession,
    *,
    status: str,
    university: University | None = None,
    program: Program | None = None,
    product: Product | None = None,
    history: Iterable[tuple[str | None, str, datetime]] = (),
    changed_at: datetime | None = None,
) -> Student:
    n = uuid.uuid4().hex
    student = Student(
        full_name=f"Студентов {n[:6]} Тест",
        email=f"{n[:10]}@student.test",
        email_hmac=(n * 2)[:64],
        display_name=f"Студентов {n[:4]}.",
        university_id=university.id if university else None,
        program_id=program.id if program else None,
        product_id=product.id if product else None,
        funnel_status=status,
        status_changed_at=changed_at or NOW,
    )
    session.add(student)
    await session.flush()
    for from_status, to_status, at in history:
        session.add(
            StudentStatusHistory(
                student_id=student.id, from_status=from_status, to_status=to_status, created_at=at
            )
        )
    await session.flush()
    return student


async def get_json(client: Any, path: str, params: Any = None) -> dict[str, Any]:
    response = await client.get(f"/api/v1/reports/{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids_params(name: str, ids: Iterable[Any], extra: dict[str, str] | None = None) -> list:
    pairs = [(name, str(getattr(item, "id", item))) for item in ids]
    return pairs + list((extra or {}).items())


def funnel_total(report: dict[str, Any]) -> int:
    return sum(item["value"] for item in report["funnel"]["series"])


def workload_total(report: dict[str, Any]) -> int:
    return sum(row["active_requests"] for row in report["kam_workload"]["rows"])


def series_total(report: dict[str, Any], key: str) -> int:
    (series,) = [s for s in report["dynamics"]["series"] if s["key"] == key]
    return sum(point["value"] for point in series["points"])


# --------------------------------------------------------------------------------------
# Мир: три КАМа, три вуза, два продукта и программы, заявки B2B и B2C
# --------------------------------------------------------------------------------------


@pytest.fixture
async def world(db_session: AsyncSession, workflows: Workflows, admin_user: AppUser) -> dict:
    wf, st = workflows["b2b"]
    wf_c, st_c = workflows["b2c"]
    kams = [
        await factories.make_user(db_session, roles=("kam",), full_name=f"Камов{n} Кам")
        for n in (1, 2, 3)
    ]
    unis = [
        await factories.make_university(db_session, name=f"Вуз отчёта №{n}", kam=kams[n - 1])
        for n in (1, 2, 3)
    ]
    p1 = await make_product(db_session, "Продукт один")
    p2 = await make_product(db_session, "Продукт два")
    g1 = await make_program(db_session, "Программа один", product=p1, rank=10)
    g2 = await make_program(db_session, "Программа два", product=p2, rank=20)

    async def deal(kam: int, uni: int, path: list, *, program: Program, created: float) -> Deal:
        return await factories.make_deal(
            db_session,
            workflow=wf,
            stages=st,
            responsible=kams[kam],
            university=unis[uni],
            product_id=program.product_id,
            program_id=program.id,
            created_at=ago(created),
            path=[(code, ago(days)) for code, days in path],
        )

    deals = {
        "neg1": await deal(0, 0, [("new", 10), ("negotiation", 8)], program=g1, created=10),
        "fc2": await deal(1, 1, [("first_contact", 5)], program=g2, created=5),
        "new3": await deal(2, 2, [("new", 3)], program=g1, created=3),
        "won1": await deal(0, 0, [("new", 20), ("completed", 5)], program=g1, created=20),
        "lost2": await deal(1, 1, [("new", 15), ("rejected", 6)], program=g2, created=15),
        # зависшая: на «Подготовке договора» 40 дней при пороге 14
        "stuck1": await deal(0, 0, [("new", 50), ("contract_prep", 40)], program=g1, created=50),
    }
    deals["b2c1"] = await factories.make_deal(
        db_session, workflow=wf_c, stages=st_c, responsible=kams[0], created_at=ago(4)
    )
    return {
        "head": admin_user,
        "kams": kams,
        "unis": unis,
        "products": (p1, p2),
        "programs": (g1, g2),
        "deals": deals,
        "workflows": workflows,
    }


# --------------------------------------------------------------------------------------
# 1. Мультифильтр: сумма по двум вузам = сумме по каждому (а не «последний»)
# --------------------------------------------------------------------------------------


async def test_two_universities_sum_in_every_section(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    u1, u2, _u3 = world["unis"]
    one = await get_json(client, "dashboard", ids_params("university_id", [u1], PERIOD))
    two = await get_json(client, "dashboard", ids_params("university_id", [u2], PERIOD))
    both = await get_json(client, "dashboard", ids_params("university_id", [u1, u2], PERIOD))

    assert one["kpi"]["active"]["value"] == 2  # neg1, stuck1
    assert two["kpi"]["active"]["value"] == 1  # fc2
    for metric in ("active", "completed", "lost", "stuck"):
        assert (
            both["kpi"][metric]["value"]
            == one["kpi"][metric]["value"] + two["kpi"][metric]["value"]
        ), metric
    assert both["kpi"]["active"]["value"] != two["kpi"]["active"]["value"]
    assert funnel_total(both) == funnel_total(one) + funnel_total(two)
    assert workload_total(both) == workload_total(one) + workload_total(two)
    for key in ("created", "closed", "lost"):
        assert series_total(both, key) == series_total(one, key) + series_total(two, key)
    assert both["params"]["university_id"] == sorted([str(u1.id), str(u2.id)])


async def test_two_kams_and_products(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    k1, k2, k3 = world["kams"]
    report = await get_json(client, "dashboard", ids_params("kam_id", [k1, k2], PERIOD))
    assert {row["kam_id"] for row in report["kam_workload"]["rows"]} == {str(k1.id), str(k2.id)}
    # открытые: neg1, stuck1, b2c1 (КАМ 1) + fc2 (КАМ 2); new3 у КАМа 3 — вне фильтра
    assert report["kpi"]["active"]["value"] == 4
    assert report["kpi"]["completed"]["value"] == 1 and report["kpi"]["lost"]["value"] == 1

    p1, _p2 = world["products"]
    by_product = await get_json(client, "dashboard", ids_params("product_id", [p1], PERIOD))
    # продукт 1: neg1, new3, stuck1 открыты; won1 закрыта
    assert by_product["kpi"]["active"]["value"] == 3
    assert {row["name"] for row in by_product["programs_demand"]["rows"]} == {"Программа один"}
    only_k3 = await get_json(client, "dashboard", ids_params("kam_id", [k3], PERIOD))
    assert only_k3["kpi"]["active"]["value"] == 1


async def test_single_param_backward_compatible(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    k1 = world["kams"][0]
    single = await get_json(client, "kam-workload", {"kam_id": str(k1.id)})
    listed = await get_json(client, "kam-workload", [("kam_id", str(k1.id))])
    assert single["rows"] == listed["rows"]
    assert [row["kam_id"] for row in single["rows"]] == [str(k1.id)]


async def test_invalid_filters_are_400(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    too_many = [("kam_id", str(uuid.uuid4())) for _ in range(51)]
    response = await client.get("/api/v1/reports/dashboard", params=too_many)
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["code"] == "too_many_values"
    bad = await client.get("/api/v1/reports/dashboard", params={"university_id": "x"})
    assert bad.status_code == 400


async def test_export_filters_parse_to_same_report(db_session, world) -> None:  # noqa: ANN001
    """`filters` выгрузки (списки строк, как шлёт дашборд) → тот же отчёт, что на экране."""
    u1, u2, _u3 = world["unis"]
    params = ReportParams.from_filters(
        {"university_id": [str(u1.id), str(u2.id)], "from": PERIOD["from"], "to": PERIOD["to"]}
    )
    report = await build_report(db_session, "dashboard", world["head"], params)
    assert report["kpi"]["active"]["value"] == 3


# --------------------------------------------------------------------------------------
# 2. «Все» — оба процесса; этапы в порядке процесса; «Отказ» помечен
# --------------------------------------------------------------------------------------


async def test_funnel_all_means_all_workflows(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    report = await get_json(client, "funnel")
    assert report["workflow_types"] == ["b2b", "b2c"]
    b2c = [item for item in report["series"] if item["workflow_type"] == "b2c"]
    assert sum(item["value"] for item in b2c) == 1
    assert all(item["label"].startswith("B2C · ") for item in b2c)
    b2b = [item for item in report["series"] if item["workflow_type"] == "b2b"]
    assert [item["sort_order"] for item in b2b] == sorted(item["sort_order"] for item in b2b)
    (rejected,) = [item for item in b2b if item["key"] == "rejected"]
    assert rejected["outcome"] == "lost" and rejected["is_terminal"] is True
    assert rejected["value"] == 1

    only_b2b = await get_json(client, "funnel", {"workflow_type": "b2b"})
    assert only_b2b["workflow_types"] == ["b2b"]
    assert {item["workflow_type"] for item in only_b2b["series"]} == {"b2b"}
    assert all(" · " not in item["label"] for item in only_b2b["series"])
    # KPI «Все» и этапы «Все» — одно множество заявок
    dashboard = await get_json(client, "dashboard")
    open_on_stages = sum(
        item["value"] for item in dashboard["funnel"]["series"] if not item["is_terminal"]
    )
    assert open_on_stages == dashboard["kpi"]["active"]["value"]


async def test_empty_period_returns_zero_stages(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    report = await get_json(client, "funnel", {"from": "2020-01-01", "to": "2020-01-31"})
    assert report["series"] and all(item["value"] == 0 for item in report["series"])


# --------------------------------------------------------------------------------------
# 3. Нагрузка по КАМам: {code,count} и {key,value}, порядок этапов, вузы, зависшие
# --------------------------------------------------------------------------------------


async def test_kam_workload_shape_and_order(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    k1 = world["kams"][0]
    report = await get_json(client, "kam-workload", {"workflow_type": "b2b"})
    (row,) = [r for r in report["rows"] if r["kam_id"] == str(k1.id)]
    assert row["active_requests"] == 2
    assert row["stuck"] == 1
    assert row["universities"] == 1
    assert row["top_universities"] == [
        {"id": str(world["unis"][0].id), "name": "Вуз отчёта №1", "count": 2}
    ]
    codes = [item["code"] for item in row["by_status"]]
    assert codes == ["negotiation", "contract_prep"]  # sort_order 2, 3
    for item in row["by_status"]:
        assert item["key"] == item["code"] and item["value"] == item["count"] == 1
        assert item["workflow_type"] == "b2b"
    # фильтр по вузу доходит до нагрузки (раньше игнорировался)
    by_uni = await get_json(client, "kam-workload", ids_params("university_id", [world["unis"][1]]))
    assert sum(r["active_requests"] for r in by_uni["rows"]) == 1


async def test_kam_workload_all_processes_in_stage_order(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    k1 = world["kams"][0]
    report = await get_json(client, "kam-workload")
    (row,) = [r for r in report["rows"] if r["kam_id"] == str(k1.id)]
    assert row["active_requests"] == 3  # +B2C
    assert [(i["workflow_type"], i["code"]) for i in row["by_status"]] == [
        ("b2b", "negotiation"),
        ("b2b", "contract_prep"),
        ("b2c", "new"),
    ]


# --------------------------------------------------------------------------------------
# 4. «Завершено»: KPI = сумма динамики; правка и перевод схемы не «завершают» заново
# --------------------------------------------------------------------------------------


async def test_completed_kpi_equals_dynamics(db_session, api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    period = {"from": (TODAY - timedelta(days=30)).isoformat(), "to": TODAY.isoformat()}
    report = await get_json(client, "dashboard", period)
    assert report["kpi"]["completed"]["value"] == 1  # won1 — 5 дней назад
    assert report["kpi"]["lost"]["value"] == 1  # lost2 — 6 дней назад
    assert report["kpi"]["conversion"]["value"] == 50.0
    assert series_total(report, "closed") == report["kpi"]["completed"]["value"]
    assert series_total(report, "lost") == report["kpi"]["lost"]["value"]
    assert report["dynamics"]["period"] == period
    assert report["kpi"]["period"]["from"] == period["from"]
    assert report["kpi"]["period"]["is_default"] is False

    # правка карточки давно закрытой заявки и строка перевода схемы — не закрытие
    old = await factories.make_deal(
        db_session,
        workflow=world["workflows"]["b2b"][0],
        stages=world["workflows"]["b2b"][1],
        responsible=world["kams"][0],
        university=world["unis"][0],
        created_at=ago(200),
        path=[("new", ago(200)), ("completed", ago(100))],
    )
    completed_stage = world["workflows"]["b2b"][1]["completed"]
    await db_session.execute(sa.update(Deal).where(Deal.id == old.id).values(updated_at=NOW))
    db_session.add(
        DealStageHistory(
            deal_id=old.id,
            from_stage_id=completed_stage.id,
            to_stage_id=completed_stage.id,
            from_stage_name=completed_stage.name,
            to_stage_name=completed_stage.name,
            kind="system_migration",
            created_at=ago(1),
        )
    )
    await db_session.flush()
    again = await get_json(client, "dashboard", period)
    assert again["kpi"]["completed"]["value"] == 1
    assert series_total(again, "closed") == 1


async def test_default_kpi_period_is_labelled(api, world) -> None:  # noqa: ANN001
    report = await get_json(api.as_user(world["head"]), "dashboard")
    period = report["kpi"]["period"]
    assert period["is_default"] is True and period["days"] == 30
    assert period["to"] == TODAY.isoformat()
    assert date.fromisoformat(period["previous"]["to"]) == date.fromisoformat(
        period["from"]
    ) - timedelta(days=1)


# --------------------------------------------------------------------------------------
# 5. Востребованность программ: три показателя, период, без ручного топ-20, флаг
# --------------------------------------------------------------------------------------


async def test_programs_demand_not_limited_by_manual_top(db_session, api, world) -> None:  # noqa: ANN001
    wf, st = world["workflows"]["b2b"]
    for n in range(22):
        await make_program(db_session, f"Программа без заявок {n:02d}", rank=30 + n)
    hit = await make_program(db_session, "Хит без приоритета", rank=5000)
    for _ in range(5):  # больше, чем у «Программы один» (4)
        await factories.make_deal(
            db_session,
            workflow=wf,
            stages=st,
            responsible=world["kams"][1],
            university=world["unis"][1],
            program_id=hit.id,
            created_at=ago(2),
        )
    report = await get_json(api.as_user(world["head"]), "programs-demand")
    names = [row["name"] for row in report["rows"]]
    assert len(names) == 25  # 2 + 22 + хит: никакого limit(20) до сортировки
    assert names[0] == "Хит без приоритета"
    assert report["rows"][0]["requests_count"] == 5
    assert report["auto_ranking"] is False
    assert all(row["computed_demand"] is None for row in report["rows"])
    assert {c["key"] for c in report["columns"]} >= {
        "requests_count",
        "students_studying",
        "parallel_streams",
        "priority_rank",
    }


async def test_programs_demand_three_indicators_with_period(db_session, world) -> None:  # noqa: ANN001
    wf, st = world["workflows"]["b2b"]
    g1, g2 = world["programs"]
    u1, u2, _u3 = world["unis"]
    kam = world["kams"][0]
    # потоки: две заявки одного вуза на этапе обучения — два потока; третья отучилась
    for _ in range(2):
        await factories.make_deal(
            db_session,
            workflow=wf,
            stages=st,
            responsible=kam,
            university=u1,
            program_id=g1.id,
            created_at=ago(90),
            path=[("new", ago(90)), ("in_learning", ago(70))],
        )
    await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        university=u1,
        program_id=g1.id,
        created_at=ago(90),
        path=[("new", ago(90)), ("in_learning", ago(30)), ("completed", ago(10))],
    )
    # обучающиеся: двое учатся со 100 дней назад, выпускник ушёл 50 дней назад
    for _ in range(2):
        await make_student(
            db_session,
            status="studying",
            university=u1,
            program=g1,
            history=[(None, "candidate", ago(120)), ("candidate", "studying", ago(100))],
            changed_at=ago(100),
        )
    await make_student(
        db_session,
        status="graduate",
        university=u2,
        program=g1,
        history=[("candidate", "studying", ago(100)), ("studying", "graduate", ago(50))],
        changed_at=ago(50),
    )
    # без истории (импорт): статус «учится» с 5 дней назад
    await make_student(db_session, status="studying", university=u2, program=g2, changed_at=ago(5))
    await make_student(db_session, status="candidate", university=u1, program=g1)

    async def demand(**kwargs: Any) -> dict[str, dict[str, Any]]:
        body = await report_programs_demand(db_session, ReportParams(**kwargs), None)  # type: ignore[arg-type]
        return {row["name"]: row for row in body["rows"]}

    now = await demand()
    assert now["Программа один"]["students_studying"] == 2
    assert now["Программа один"]["parallel_streams"] == 2  # не distinct по вузу
    assert now["Программа два"]["students_studying"] == 1
    # заявки на обучение: neg1, new3, won1, stuck1 + три новые
    assert now["Программа один"]["requests_count"] == 7

    # период 60–55 дней назад: учились трое (выпускник ушёл позже начала), потоков 2
    early = await demand(date_from=TODAY - timedelta(days=60), date_to=TODAY - timedelta(days=55))
    assert early["Программа один"]["students_studying"] == 3
    assert early["Программа один"]["parallel_streams"] == 2
    assert early["Программа два"]["students_studying"] == 0  # начала учиться позже
    assert early["Программа один"]["requests_count"] == 0  # заявки созданы раньше/позже

    # период 20–15 дней назад: третья заявка ещё в обучении — три потока
    mid = await demand(date_from=TODAY - timedelta(days=20), date_to=TODAY - timedelta(days=15))
    assert mid["Программа один"]["parallel_streams"] == 3
    assert mid["Программа один"]["students_studying"] == 2

    # фильтр по вузу: студенты и потоки вуза 2
    by_uni = await demand(university_id=[u2.id])
    assert by_uni["Программа один"]["parallel_streams"] == 0
    assert by_uni["Программа один"]["students_studying"] == 0  # выпускник не «учится»
    assert by_uni["Программа два"]["students_studying"] == 1
    # фильтр по КАМу: студенты вузов КАМа 1 (вуз 1)
    by_kam = await demand(kam_id=[kam.id])
    assert by_kam["Программа один"]["students_studying"] == 2
    assert by_kam["Программа два"]["students_studying"] == 0


async def test_auto_ranking_flag_adds_index(db_session, world) -> None:  # noqa: ANN001
    g1, g2 = world["programs"]
    await make_student(db_session, status="studying", program=g2)
    off = await report_programs_demand(db_session, ReportParams(), None)  # type: ignore[arg-type]
    assert off["auto_ranking"] is False and off["formula"] is None
    await set_flag(db_session, world["head"], "auto_ranking", True)
    await db_session.flush()
    on = await report_programs_demand(db_session, ReportParams(), None)  # type: ignore[arg-type]
    assert on["auto_ranking"] is True and on["formula"]
    rows = {row["name"]: row for row in on["rows"]}
    assert 0 < rows["Программа два"]["computed_demand"] <= 100
    indexes = [row["computed_demand"] for row in on["rows"]]
    assert indexes == sorted(indexes, reverse=True)
    assert any(c["key"] == "computed_demand" for c in on["columns"])


# --------------------------------------------------------------------------------------
# 6. Студенты: фильтры вуза, КАМа (вузы КАМа), программы
# --------------------------------------------------------------------------------------


async def test_talent_funnel_filters(db_session, api, world) -> None:  # noqa: ANN001
    u1, u2, u3 = world["unis"]
    g1, g2 = world["programs"]
    await make_student(db_session, status="candidate", university=u1, program=g1)
    await make_student(db_session, status="studying", university=u2, program=g2)
    await make_student(db_session, status="graduate", university=u3, program=g1)
    client = api.as_user(world["head"])

    def total(report: dict[str, Any]) -> int:
        return sum(item["value"] for item in report["series"])

    both = await get_json(client, "talent-pool-funnel", ids_params("university_id", [u1, u2]))
    assert total(both) == 2
    by_kam = await get_json(client, "talent-pool-funnel", ids_params("kam_id", [world["kams"][2]]))
    assert total(by_kam) == 1
    assert {i["key"]: i["value"] for i in by_kam["series"]}["graduate"] == 1
    by_program = await get_json(client, "talent-pool-funnel", ids_params("program_id", [g1]))
    assert total(by_program) == 2
    dashboard = await get_json(client, "dashboard", ids_params("university_id", [u1, u2]))
    assert total(dashboard["talent_pool_funnel"]) == 2


async def test_stuck_report_and_coverage_use_lists(api, world) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    u1, u2, _u3 = world["unis"]
    stuck = await get_json(client, "stuck", ids_params("university_id", [u1, u2]))
    assert [row["request_id"] for row in stuck["rows"]] == [str(world["deals"]["stuck1"].id)]
    none = await get_json(client, "stuck", ids_params("university_id", [u2]))
    assert none["rows"] == []
    coverage = await get_json(
        client, "universities-coverage", ids_params("kam_id", world["kams"][:2])
    )
    assert sum(row["total"] for row in coverage["rows"]) == 2


# --------------------------------------------------------------------------------------
# 7. Выгрузка с фильтрами-списками: файл совпадает с экраном
# --------------------------------------------------------------------------------------


async def test_export_matches_screen_with_list_filters(api, world, memory_storage) -> None:  # noqa: ANN001
    client = api.as_user(world["head"])
    u1, u2, _u3 = world["unis"]
    filters = {"university_id": [str(u1.id), str(u2.id)], **PERIOD}
    screen = await get_json(client, "dashboard", ids_params("university_id", [u1, u2], PERIOD))

    async def export(entity_type: str) -> dict[str, Any]:
        created = await client.post(
            "/api/v1/export/jobs",
            json={"entity_type": entity_type, "format": "json", "filters": filters},
        )
        assert created.status_code == 201, created.text
        download = await client.get(f"/api/v1/export/jobs/{created.json()['id']}/download")
        assert download.status_code == 200, download.text
        return download.json()

    kpi_file = await export("report:dashboard")
    values = {item["metric"]: item["value"] for item in kpi_file["items"]}
    assert values["Заявок активных"] == screen["kpi"]["active"]["value"] == 3
    assert values["Завершено за период"] == screen["kpi"]["completed"]["value"]
    assert values["Отказ за период"] == screen["kpi"]["lost"]["value"]

    programs_file = await export("report:programs-demand")
    keys = {column["key"] for column in programs_file["columns"]}
    assert {"requests_count", "students_studying", "parallel_streams"} <= keys
    by_name = {item["name"]: item for item in programs_file["items"]}
    screen_programs = {row["name"]: row for row in screen["programs_demand"]["rows"]}
    assert (
        by_name["Программа один"]["requests_count"]
        == (screen_programs["Программа один"]["requests_count"])
    )
