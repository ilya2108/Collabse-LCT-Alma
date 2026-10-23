"""Выгрузка реестра заявок за период совпадает со списком на экране: `created_to` — дата,
последний день периода входит целиком (раньше `<= created_to` отсекал всё после полуночи)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.import_export import export

from . import factories

API = "/api/v1"


async def test_requests_export_includes_last_day(api, db_session, workflows) -> None:  # noqa: ANN001
    head = await factories.make_user(db_session, roles=("head_kam",))
    kam = await factories.make_user(db_session, roles=("kam",), manager=head)
    wf, stages = workflows["b2b"]
    day = (datetime.now(timezone.utc) - timedelta(days=3)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    deal = await factories.make_deal(
        db_session, workflow=wf, stages=stages, responsible=kam, created_at=day
    )
    period = {"created_from": day.date().isoformat(), "created_to": day.date().isoformat()}

    _columns, rows = await export._dataset_requests(db_session, period, head)
    assert [row["id"] for row in rows] == [str(deal.id)]

    listed = await api.as_user(head).get(f"{API}/requests", params=period)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [str(deal.id)]

    # день до периода — пусто и там, и там
    before = (day - timedelta(days=1)).date().isoformat()
    _columns, rows = await export._dataset_requests(
        db_session, {"created_from": before, "created_to": before}, head
    )
    assert rows == []
