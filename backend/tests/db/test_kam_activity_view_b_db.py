"""Вид Б «Сводка для заказчика» (P1-3) на настоящем PostgreSQL: 4 KPI (охват, вузы с
действующим договором, обучающиеся, выиграно), главная диаграмма «Вузы B2B по этапам на
конец периода» с правилом отнесения, риски и студенты по программам.

`NOW` = 20.10.2026 12:00 МСК; период по умолчанию — 21.09–20.10, прошлый — 22.08–20.09.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from app.models import Contract, Program, Student, StudentStatusHistory
from app.modules.reporting import kam_activity as ka
from app.modules.reporting.kam_activity import KamActivityParams
from app.modules.reporting.kam_activity_export import (
    build_kam_activity_file,
    render_kam_activity_pdf,
)
from app.modules.reporting.schemas import KamActivitySummaryOut

from . import factories

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=ka.REPORT_TZ)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ka, "_now", lambda: NOW)


async def _contract(session, university, *, status: str, begins: date, ends: date | None):
    session.add(
        Contract(
            number=f"Д-{university.short_name}-{status}",
            university_id=university.id,
            status=status,
            signed_at=begins,
            valid_to=ends,
        )
    )
    await session.flush()


async def _student(session, n: int, university, program, path: list[tuple[str, datetime]]):
    row = Student(
        full_name=f"Студентов{n} Иван",
        email=f"s{n}@example.com",
        email_hmac=f"{n:064d}",
        display_name=f"Студентов{n} И.",
        university_id=university.id,
        program_id=program.id,
        funnel_status=path[-1][0],
        status_changed_at=path[-1][1],
    )
    session.add(row)
    await session.flush()
    previous = None
    for status, at in path:
        session.add(
            StudentStatusHistory(
                student_id=row.id, from_status=previous, to_status=status, created_at=at
            )
        )
        previous = status
    await session.flush()


@pytest.fixture
async def portfolio(db_session, workflows) -> dict[str, Any]:
    wf, stages = workflows["b2b"]
    head = await factories.make_user(db_session, roles=("head_kam",), full_name="Морозов Д.")
    kam1 = await factories.make_user(db_session, roles=("kam",), full_name="Петров К.")
    kam2 = await factories.make_user(db_session, roles=("kam",), full_name="Иванова М.")
    mipt = await factories.make_university(db_session, name="МФТИ", short_name="МФТИ", kam=kam1)
    itmo = await factories.make_university(db_session, name="ИТМО", short_name="ИТМО", kam=kam2)
    spbu = await factories.make_university(db_session, name="СПбГУ", short_name="СПбГУ", kam=kam2)
    urfu = await factories.make_university(db_session, name="УрФУ", short_name="УрФУ", kam=kam1)

    async def deal(responsible, university, path, **kwargs):
        return await factories.make_deal(
            db_session,
            workflow=wf,
            stages=stages,
            responsible=responsible,
            university=university,
            created_at=path[0][1],
            path=path,
            **kwargs,
        )

    # МФТИ: отказ в периоде и переговоры → вуз на «Переговорах»
    await deal(kam1, mipt, [("new", ago(70)), ("rejected", ago(10))])
    await deal(kam1, mipt, [("new", ago(70)), ("negotiation", ago(40))])
    # ИТМО: договор подписан (−25 д), а на конец прошлого периода — переговоры
    await deal(
        kam2, itmo, [("new", ago(90)), ("negotiation", ago(60)), ("contract_signed", ago(25))]
    )
    # СПбГУ: только отказ в периоде → «Отказ»
    await deal(kam2, spbu, [("new", ago(50)), ("rejected", ago(8))])
    # УрФУ — закреплён за kam1, заявок нет → «Без заявок в работе»

    await _contract(db_session, itmo, status="active", begins=ago(24).date(), ends=None)
    await _contract(  # истёк до конца периода — не действует
        db_session, mipt, status="completed", begins=date(2025, 1, 1), ends=ago(30).date()
    )
    program = Program(name="Python для вузов", university_id=itmo.id)
    db_session.add(program)
    await db_session.flush()
    await _student(db_session, 1, itmo, program, [("candidate", ago(90)), ("studying", ago(40))])
    await _student(db_session, 2, itmo, program, [("candidate", ago(90)), ("studying", ago(5))])
    return {"head": head, "kam1": kam1, "mipt": mipt, "itmo": itmo, "urfu": urfu}


async def test_view_b_kpis_contracts_and_students(db_session, portfolio) -> None:
    summary = await ka.build_kam_activity_summary(
        db_session, portfolio["head"], KamActivityParams()
    )
    KamActivitySummaryOut.model_validate(summary)
    kpi = summary["kpi"]
    # вузы с действующим договором на конец периода: ИТМО (договор с −24 д), договор МФТИ
    # завершён и истёк 20.09; на конец прошлого периода (20.09) — наоборот: МФТИ ещё
    # действовал, ИТМО ещё не начался
    assert kpi["universities_with_contract"] == {"value": 1, "prev": 1, "delta": 0}
    assert kpi["students_studying"] == {"value": 2, "prev": 1, "delta": 1}
    assert summary["talent"]["students_studying"]["value"] == 2
    assert summary["talent"]["programs"][0]["name"] == "Python для вузов"


async def test_universities_by_stage_main_chart(db_session, portfolio) -> None:
    summary = await ka.build_kam_activity_summary(
        db_session, portfolio["head"], KamActivityParams()
    )
    block = summary["universities_by_stage"]
    rows = {row["code"]: row for row in block["rows"]}
    assert rows["negotiation"]["value"] == 1 and rows["negotiation"]["universities"] == ["МФТИ"]
    assert rows["contract_signed"]["value"] == 1 and rows["contract_signed"]["prev"] == 0
    assert rows["rejected"]["value"] == 1 and rows["rejected"]["universities"] == ["СПбГУ"]
    assert block["total"] == 3
    # на конец прошлого периода: МФТИ и ИТМО на переговорах (СПбГУ — на «Новой»)
    assert rows["negotiation"]["prev"] == 2 and rows["new"]["prev"] == 1
    assert block["without_requests"]["universities"] == ["УрФУ"]
    assert "самому продвинутому этапу" in block["rule"]

    risks = {risk["key"]: risk for risk in summary["risks"]}
    assert risks["lost"]["value"] == 2
    assert "УрФУ" in risks["untouched"]["items"]


async def test_view_b_in_pdf_and_xlsx(db_session, portfolio) -> None:
    import io

    import openpyxl

    head = portfolio["head"]
    pdf, _rows, _name = await build_kam_activity_file(
        db_session, user=head, filters={}, export_format="pdf"
    )
    assert pdf.startswith(b"%PDF")
    data, _rows, _name = await build_kam_activity_file(
        db_session, user=head, filters={}, export_format="xlsx"
    )
    sheet = openpyxl.load_workbook(io.BytesIO(data))["Сводка"]
    texts = [row[0] for row in sheet.iter_rows(values_only=True) if row and row[0]]
    assert "Вузы B2B по этапам на конец периода" in texts
    assert "Вузы с действующим договором" in texts
    assert "Риски" in texts
    # рендер не зависит от наличия блока B2B (отчёт только по B2C)
    summary = await ka.build_kam_activity_summary(
        db_session, head, KamActivityParams(workflow_type="b2c")
    )
    assert summary["universities_by_stage"] is None
    detail = await ka.build_kam_activity(db_session, head, KamActivityParams(workflow_type="b2c"))
    from app.modules.reporting.kam_activity_export import ExportMeta

    meta = ExportMeta(generated_by="Тест", generated_at=NOW, filters_text="Процесс: B2C")
    assert render_kam_activity_pdf(detail, summary, meta).startswith(b"%PDF")
