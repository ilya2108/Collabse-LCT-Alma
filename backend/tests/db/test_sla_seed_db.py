"""Демо-данные «Просрочек» (`app.tools.seed_sla`) на PostgreSQL: полный сид стенда от
фиксированного якоря (как `make demo-reset`), картина отчёта и идемпотентность повтора."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models import AppUser, AuditLog, Deal, DealComment, Notification
from app.modules.reporting import kam_activity as ka
from app.modules.reporting import sla
from app.modules.reporting.kam_activity import KamActivityParams
from app.tools import seed, seed_sla

ANCHOR = datetime(2026, 10, 16, 9, 0, tzinfo=ka.REPORT_TZ)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sla, "_now", lambda: ANCHOR)
    monkeypatch.setattr(ka, "_now", lambda: ANCHOR)


async def _count(session, model, *where) -> int:  # noqa: ANN001
    stmt = sa.select(sa.func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return (await session.execute(stmt)).scalar_one()


def test_plan_is_deterministic() -> None:
    first = seed_sla.build_sla_plan(ANCHOR)
    assert first == seed_sla.build_sla_plan(ANCHOR) and len(first) == 10
    for plan in first:
        assert plan.path[-1] == (plan.stage, plan.days_on_stage)
        assert [days for _code, days in plan.path] == sorted(
            (days for _code, days in plan.path), reverse=True
        )
    transferred = [plan for plan in first if plan.previous]
    assert {(plan.previous, plan.kam) for plan in transferred} == {("kam19@demo", "kam05@demo")}
    runs = seed_sla.reminder_runs(ANCHOR, 14, 3, until=ANCHOR + timedelta(days=30))
    assert runs[0] == ANCHOR + timedelta(days=15) + seed_sla.RUN_OFFSET
    assert all((b - a) == timedelta(days=3) for a, b in zip(runs, runs[1:], strict=False))


async def test_seed_sla_picture_and_idempotency(
    db_session_factory, memory_storage, monkeypatch, capsys
) -> None:  # noqa: ANN001
    monkeypatch.setattr(seed, "get_session_factory", lambda: db_session_factory)
    monkeypatch.setattr(seed, "_NOW", ANCHOR)
    await seed.run_seed()
    printed = capsys.readouterr().out
    assert "seed: просрочки — сценарных заявок 10, передач 2" in printed

    async with db_session_factory() as session:
        users = {
            user.username: user for user in (await session.execute(sa.select(AppUser))).scalars()
        }
        report = await sla.build_sla(
            session, users["head@demo"], KamActivityParams(), include_late=True, use_cache=False
        )
        rows = {row["kam"]: row for row in report["kams"]}
        kam = {
            name: rows[users[name].full_name]
            for name in ("kam05@demo", "kam12@demo", "kam18@demo", "kam2@demo")
        }
        # у разных КАМов разная картина; ни один блок не пуст
        assert report["kpi"]["overdue"]["value"] > 0 and report["kpi"]["decided"]["value"] > 0
        assert report["kpi"]["overdue_30_plus"]["value"] > 0
        assert report["late_closed"]["total"] > 0
        assert all(report["by_workflow"][wf]["overdue"] > 0 for wf in ("b2b", "b2c"))
        assert any(point["overdue"] for point in report["trend"]["points"])
        assert kam["kam18@demo"]["overdue_no_action"] > 0 and kam["kam18@demo"]["attention"]
        assert kam["kam12@demo"]["attention"] and kam["kam12@demo"]["overdue_30_plus"] > 0
        assert kam["kam05@demo"]["inherited"] == 1  # передача kam19 → kam05 уже просроченной
        assert kam["kam2@demo"]["overdue"] > 0
        quiet = [
            row
            for name, row in rows.items()
            if row["active"] and not row["overdue"] and row["on_time_pct"]
        ]
        assert quiet, "есть КАМы без просрочек"
        # журнал напоминаний и реакция
        info = report["reminders"]["info"]
        assert info["coverage"] == "full" and info["rules_active"] is True
        team = report["reminders"]["team"]
        assert team["reacted_72h"] > 0 and team["no_action"] > 0 and team["undelivered"] > 0
        assert team["delivered"] < team["sent"]  # «доставлено n из m»
        assert sla.TRANSFER_NOTE in report["notes"]
        for item in report["attention"]["items"]:
            assert not any(sla.NO_ADVICE_RE.search(fact) for fact in item["facts"])
        assert not sla.NO_ADVICE_RE.search(report["headline"])

        # выгрузки на «живом» наборе: XLSX со всеми листами, PDF в обоих вариантах
        from app.modules.reporting.sla_export import SHEET_NAMES, build_sla_file

        head = users["head@demo"]
        xlsx, rows_count, _name = await build_sla_file(
            session, user=head, filters={}, export_format="xlsx", options={}
        )
        assert rows_count == report["kpi"]["overdue"]["value"]
        import io

        import openpyxl

        assert tuple(openpyxl.load_workbook(io.BytesIO(xlsx)).sheetnames) == SHEET_NAMES
        for by_kam in (False, True):
            pdf, _rows, _name = await build_sla_file(
                session, user=head, filters={}, export_format="pdf", options={"by_kam": by_kam}
            )
            assert pdf.startswith(b"%PDF")
        own_pdf, _rows, _name = await build_sla_file(
            session, user=users["kam2@demo"], filters={}, export_format="pdf", options={}
        )
        assert own_pdf.startswith(b"%PDF")

        counts = (
            await _count(session, Deal),
            await _count(session, Notification),
            await _count(session, DealComment),
            await _count(session, AuditLog, AuditLog.action == "request.assigned"),
        )

    # повторный запуск ничего не дублирует
    async with db_session_factory() as session:
        again = await seed_sla.seed_sla_history(session, now=ANCHOR)
        await session.commit()
        assert again == dict.fromkeys(
            ("deals", "transfers", "reminders", "escalations", "reactions"), 0
        )
        assert counts == (
            await _count(session, Deal),
            await _count(session, Notification),
            await _count(session, DealComment),
            await _count(session, AuditLog, AuditLog.action == "request.assigned"),
        )
