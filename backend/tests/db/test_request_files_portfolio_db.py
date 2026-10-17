"""Поток B, волна 4, на настоящем PostgreSQL: файлы в статусах, комментарий на любом
переходе, ответственный КАМ вуза по праву, передача портфеля, порог этапа до 365 дней.

Сквозные кейсы идут через приложение (`api.as_user`), файлы — в памяти (`memory_storage`).
Миграция 0006 проверяется на схеме сессии (колонка, CHECK, право) и шагом бэкфилла.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.access import reset_role_catalog
from app.models import (
    AppUser,
    AuditLog,
    Deal,
    DealComment,
    DealStageHistory,
    FileObject,
    University,
)
from app.modules.files.service import ATTACHMENT_FORMATS

from . import factories
from .rbac_factories import KAM_PERMISSIONS, make_role

API = "/api/v1"
PDF = b"%PDF-1.4 scan"
ZIP = b"PK\x03\x04" + b"\x00" * 26

MIGRATION_0006 = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261018_0006_files_in_statuses_and_kam_assign.py"
)


@pytest.fixture
async def people(db_session) -> dict[str, AppUser]:
    make = factories.make_user
    head = await make(db_session, roles=("head_kam",), full_name="Морозов Дмитрий")
    return {
        "admin": await make(db_session, roles=("admin",), full_name="Админов Админ"),
        "head": head,
        "kam1": await make(db_session, roles=("kam",), full_name="Петров Константин", manager=head),
        "kam2": await make(db_session, roles=("kam",), full_name="Иванова Мария", manager=head),
        "observer": await make(db_session, roles=("observer",), full_name="Кузнецов Олег"),
    }


def ago(days: float):  # noqa: ANN201
    return factories.days_ago(days)


async def _history(db_session, deal: Deal) -> list[DealStageHistory]:  # noqa: ANN001
    return list(
        (
            await db_session.execute(
                sa.select(DealStageHistory)
                .where(DealStageHistory.deal_id == deal.id)
                .order_by(DealStageHistory.created_at)
            )
        ).scalars()
    )


async def _audit(db_session, action: str, entity_id: uuid.UUID) -> list[AuditLog]:  # noqa: ANN001
    return list(
        (
            await db_session.execute(
                sa.select(AuditLog)
                .where(AuditLog.action == action, AuditLog.entity_id == entity_id)
                .order_by(AuditLog.id)
            )
        ).scalars()
    )


async def _version(client, deal: Deal) -> int:  # noqa: ANN001
    card = await client.get(f"{API}/requests/{deal.id}")
    assert card.status_code == 200, card.text
    return card.json()["version"]


def _error_code(response) -> str:  # noqa: ANN001
    body = response.json()["error"]
    return body["details"][0]["code"] if body.get("details") else body["code"]


# --------------------------------------------------------------------------------------
# P0-5: файлы в статусах
# --------------------------------------------------------------------------------------


async def test_upload_whitelist_and_link_to_current_stage(
    api, db_session, workflows, people, memory_storage
) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    kam1 = people["kam1"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=kam1,
        path=[("new", ago(10)), ("first_contact", ago(5))],
    )
    created_row, entry_row = await _history(db_session, deal)
    client = api.as_user(kam1)
    url = f"{API}/requests/{deal.id}/files"

    # тип содержимого задаёт сервер по расширению, а не заголовок клиента
    uploaded = await client.post(url, files={"file": ("Скан.pdf", PDF, "text/html")})
    assert uploaded.status_code == 201, uploaded.text
    item = uploaded.json()
    assert item["content_type"] == "application/pdf"
    assert item["stage_history_id"] == str(entry_row.id)
    assert item["stage_name"] == "Первичный контакт"
    assert item["uploaded_by_name"] == "Петров Константин"

    # 10 форматов ТЗ проходят, остальное — 400 с понятным текстом
    for name, data in (("смета.xlsx", ZIP), ("архив.tar.gz", b"\x1f\x8b\x08\x00data")):
        ok = await client.post(url, files={"file": (name, data)})
        assert ok.status_code == 201, ok.text
    exe = await client.post(url, files={"file": ("отчёт.exe", b"MZ\x90\x00")})
    assert exe.status_code == 400
    assert _error_code(exe) == "file_type_not_allowed"
    assert exe.json()["error"]["message"] == "Недопустимый формат файла"
    assert "Можно приложить: PNG, JPEG, PDF" in exe.json()["error"]["details"][0]["message"]
    renamed = await client.post(url, files={"file": ("договор.pdf", b"<html>x</html>")})
    assert renamed.status_code == 400 and _error_code(renamed) == "file_type_not_allowed"
    assert "не похоже на PDF" in renamed.json()["error"]["details"][0]["message"]

    # явная привязка к прошлому переходу своей заявки — можно; к чужой — 400
    to_created = await client.post(
        url,
        files={"file": ("письмо.pdf", PDF)},
        data={"stage_history_id": str(created_row.id)},
    )
    assert to_created.status_code == 201, to_created.text
    assert to_created.json()["stage_name"] == "Новая"
    other = await factories.make_deal(db_session, workflow=wf, stages=stages, responsible=kam1)
    (other_row,) = await _history(db_session, other)
    foreign = await client.post(
        url, files={"file": ("письмо.pdf", PDF)}, data={"stage_history_id": str(other_row.id)}
    )
    assert foreign.status_code == 400
    assert foreign.json()["error"]["details"][0]["field"] == "stage_history_id"

    listed = (await api.as_user(people["observer"]).get(url)).json()
    assert listed["total"] == 4
    assert {i["stage_name"] for i in listed["items"]} == {"Первичный контакт", "Новая"}
    assert all(i["uploaded_by_name"] == "Петров Константин" for i in listed["items"])


async def test_transition_with_files_and_optional_comment(
    api, db_session, workflows, people, memory_storage
) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    kam1, kam2 = people["kam1"], people["kam2"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=kam1,
        path=[("new", ago(10)), ("first_contact", ago(5))],
    )
    client = api.as_user(kam1)
    files_url = f"{API}/requests/{deal.id}/files"
    transitions_url = f"{API}/requests/{deal.id}/transitions"

    # файл, загруженный в модалке перехода (без привязки до перехода), и неподходящие:
    # чужой, удалённый, уже приложенный к этапу из вкладки «Файлы», несуществующий
    own_response = await client.post(
        files_url, files={"file": ("КП.pdf", PDF)}, data={"pending_transition": "true"}
    )
    assert own_response.status_code == 201, own_response.text
    own = own_response.json()
    assert own["stage_history_id"] is None and own["stage_name"] is None
    foreign = await factories.make_file(db_session, entity_id=deal.id, uploaded_by=kam2)
    deleted = await factories.make_file(
        db_session, entity_id=deal.id, uploaded_by=kam1, deleted=True
    )
    in_stage = (await client.post(files_url, files={"file": ("Устав.pdf", PDF)})).json()
    assert in_stage["stage_name"] == "Первичный контакт"
    both = await client.post(
        files_url,
        files={"file": ("x.pdf", PDF)},
        data={"pending_transition": "true", "stage_history_id": in_stage["stage_history_id"]},
    )
    assert both.status_code == 400
    rows_before = len(await _history(db_session, deal))

    for bad_ids in (
        [str(foreign.id)],
        [str(deleted.id)],
        [in_stage["id"]],
        [str(uuid.uuid4())],
    ):
        refused = await client.post(
            transitions_url,
            json={
                "to_status_id": str(stages["negotiation"].id),
                "version": await _version(client, deal),
                "file_ids": [own["id"], *bad_ids],
            },
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["details"][0]["field"] == "file_ids"
    # невалидный файл не оставил заявку переведённой
    assert len(await _history(db_session, deal)) == rows_before
    await db_session.refresh(deal)
    assert deal.stage_id == stages["first_contact"].id

    moved = await client.post(
        transitions_url,
        json={
            "to_status_id": str(stages["negotiation"].id),
            "version": await _version(client, deal),
            "comment": "  Созвонились с проректором  ",
            "file_ids": [own["id"]],
        },
    )
    assert moved.status_code == 200, moved.text
    rows = await _history(db_session, deal)
    transition_row = rows[-1]
    assert transition_row.to_stage_id == stages["negotiation"].id
    assert transition_row.comment == "Созвонились с проректором"
    stored = await db_session.get(FileObject, uuid.UUID(own["id"]))
    await db_session.refresh(stored)
    assert stored.stage_history_id == transition_row.id
    (attached_audit,) = await _audit(db_session, "request.files_attached", deal.id)
    assert attached_audit.after["file_ids"] == [own["id"]]

    # уже приложенный к переходу файл ко второму переходу не приложить
    again = await client.post(
        transitions_url,
        json={
            "to_status_id": str(stages["contract_prep"].id),
            "version": await _version(client, deal),
            "file_ids": [own["id"]],
        },
    )
    assert again.status_code == 400

    # комментарий необязателен на обычном переходе …
    plain = await client.post(
        transitions_url,
        json={
            "to_status_id": str(stages["contract_prep"].id),
            "version": await _version(client, deal),
        },
    )
    assert plain.status_code == 200, plain.text
    # … и обязателен на возврате (requires_comment) — сервер не даёт обойти модалку
    no_reason = await client.post(
        transitions_url,
        json={
            "to_status_id": str(stages["negotiation"].id),
            "version": await _version(client, deal),
        },
    )
    assert no_reason.status_code == 400
    # отказ движка с валидными файлами: переход и привязка — одна транзакция, файл остаётся
    # без строки истории, аудита привязки нет (фронт удалит такой файл сам)
    pending = (
        await client.post(
            files_url, files={"file": ("Скан.pdf", PDF)}, data={"pending_transition": "true"}
        )
    ).json()
    refused_by_engine = await client.post(
        transitions_url,
        json={
            "to_status_id": str(stages["negotiation"].id),
            "version": await _version(client, deal),
            "file_ids": [pending["id"]],
        },
    )
    assert refused_by_engine.status_code == 400
    assert refused_by_engine.json()["error"]["details"][0]["field"] == "comment"
    stored_pending = await db_session.get(FileObject, uuid.UUID(pending["id"]))
    await db_session.refresh(stored_pending)
    assert stored_pending.stage_history_id is None
    assert len(await _audit(db_session, "request.files_attached", deal.id)) == 1
    too_long = await client.post(
        transitions_url,
        json={
            "to_status_id": str(stages["negotiation"].id),
            "version": await _version(client, deal),
            "comment": "к" * 4001,
        },
    )
    assert too_long.status_code == 400

    # таймлайн: файл — под своим переходом, комментарий — там же
    history = (await client.get(f"{API}/requests/{deal.id}/history")).json()["items"]
    by_id = {item["id"]: item for item in history}
    item = by_id[str(transition_row.id)]
    assert item["comment"] == "Созвонились с проректором"
    assert [f["file_name"] for f in item["files"]] == ["КП.pdf"]
    assert item["files"][0]["stage_name"] == "Переговоры"
    # файл из вкладки «Файлы» — под входом в этап, где его загрузили; у прочих — пусто
    entry = by_id[in_stage["stage_history_id"]]
    assert [f["file_name"] for f in entry["files"]] == ["Устав.pdf"]
    others = {str(transition_row.id), in_stage["stage_history_id"]}
    assert all(i["files"] == [] for i in history if i["id"] not in others)
    # в списке файлов заявки — этап, в котором файл приложен
    listed = (await client.get(files_url)).json()["items"]
    assert sorted(
        (f["file_name"], f["stage_name"])
        for f in listed
        if f["id"] not in (str(foreign.id), pending["id"])
    ) == [
        ("КП.pdf", "Переговоры"),
        ("Устав.pdf", "Первичный контакт"),
    ]


async def test_transition_files_need_files_permission(
    api, db_session, workflows, people, memory_storage
) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    # своя роль без права на файлы: приложить файл к переходу нельзя (403 до движка)
    await make_role(
        db_session, code="kam_nofiles", permissions=KAM_PERMISSIONS - {"requests:files"}
    )
    reset_role_catalog()
    no_files = await factories.make_user(db_session, roles=("kam_nofiles",))
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=no_files,
        path=[("new", ago(3))],
    )
    file_object = await factories.make_file(db_session, entity_id=deal.id, uploaded_by=no_files)
    client = api.as_user(no_files)
    denied = await client.post(
        f"{API}/requests/{deal.id}/transitions",
        json={
            "to_status_id": str(stages["first_contact"].id),
            "version": await _version(client, deal),
            "file_ids": [str(file_object.id)],
        },
    )
    assert denied.status_code == 403
    assert "Файлы заявок процесса B2B" in denied.json()["error"]["message"]
    await db_session.refresh(deal)
    assert deal.stage_id == stages["new"].id


async def test_backfill_links_existing_files_to_stage(db_session, workflows, people) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    kam1 = people["kam1"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=kam1,
        path=[("new", ago(30)), ("first_contact", ago(20)), ("negotiation", ago(10))],
    )
    created, first_contact, negotiation = await _history(db_session, deal)
    early = await factories.make_file(db_session, entity_id=deal.id, uploaded_by=kam1, at=ago(25))
    middle = await factories.make_file(db_session, entity_id=deal.id, uploaded_by=kam1, at=ago(15))
    late = await factories.make_file(db_session, entity_id=deal.id, uploaded_by=kam1, at=ago(1))
    contract_file = await factories.make_file(
        db_session, entity_id=uuid.uuid4(), entity_type="contract", uploaded_by=kam1
    )

    spec = importlib.util.spec_from_file_location("migration_0006", MIGRATION_0006)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    (backfill,) = [s for s in module._UPGRADE if s.lstrip().startswith("UPDATE file_object")]
    await db_session.execute(sa.text(backfill))
    for file_object in (early, middle, late, contract_file):
        await db_session.refresh(file_object)
    assert early.stage_history_id == created.id
    assert middle.stage_history_id == first_contact.id
    assert late.stage_history_id == negotiation.id
    assert contract_file.stage_history_id is None


# --------------------------------------------------------------------------------------
# P1-6: порог зависания этапа до 365 дней (CHECK миграции 0006)
# --------------------------------------------------------------------------------------


async def test_stage_threshold_check_allows_365(db_session, workflows) -> None:  # noqa: ANN001
    _wf, stages = workflows["b2b"]
    stage = stages["in_learning"]
    stage.stuck_threshold_days = 365
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            stage.stuck_threshold_days = 366
            await db_session.flush()


# --------------------------------------------------------------------------------------
# P0-10: ответственный КАМ вуза — по праву universities:assign
# --------------------------------------------------------------------------------------


def _university_form(university: University, **changes: Any) -> dict[str, Any]:
    """Тело PATCH, как его шлёт форма карточки: весь набор полей."""
    body = {
        "version": university.xmin,
        "name": university.name,
        "short_name": university.short_name,
        "region": university.region,
        "city": university.city,
        "kam_user_id": str(university.kam_user_id) if university.kam_user_id else None,
        "notes": university.notes,
    }
    body.update(changes)
    return body


async def test_university_kam_change_requires_assign(api, db_session, people) -> None:  # noqa: ANN001
    kam1, kam2, head = people["kam1"], people["kam2"], people["head"]
    own = await factories.make_university(db_session, name="МГТУ им. Баумана", kam=kam1)
    free = await factories.make_university(db_session, name="ТПУ")
    kam = api.as_user(kam1)

    # форма с неизменённым КАМом — правка своих полей проходит (сравниваем значение)
    same = await kam.patch(
        f"{API}/universities/{own.id}", json=_university_form(own, city="Москва")
    )
    assert same.status_code == 200, same.text
    await db_session.refresh(own)
    assert own.city == "Москва"
    assert await _audit(db_session, "university.kam_changed", own.id) == []

    # сменить, снять или взять ничейный — только с правом
    for target, kam_value in ((own, str(kam2.id)), (own, None), (free, str(kam1.id))):
        await db_session.refresh(target)
        denied = await kam.patch(
            f"{API}/universities/{target.id}", json=_university_form(target, kam_user_id=kam_value)
        )
        assert denied.status_code == 403, denied.text
        assert "руководитель" in denied.json()["error"]["message"]
    await db_session.refresh(own)
    assert own.kam_user_id == kam1.id

    # руководитель меняет — аудит university.kam_changed
    await db_session.refresh(own)
    changed = await api.as_user(head).patch(
        f"{API}/universities/{own.id}", json=_university_form(own, kam_user_id=str(kam2.id))
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["kam_user_id"] == str(kam2.id)
    (audit,) = await _audit(db_session, "university.kam_changed", own.id)
    assert audit.actor_user_id == head.id
    assert audit.before == {"kam_user_id": str(kam1.id)}
    assert audit.after == {"kam_user_id": str(kam2.id)}

    # несуществующий КАМ — 400, а не 500 от внешнего ключа
    await db_session.refresh(own)
    missing = await api.as_user(head).patch(
        f"{API}/universities/{own.id}", json=_university_form(own, kam_user_id=str(uuid.uuid4()))
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["details"][0]["field"] == "kam_user_id"


async def test_university_create_kam_rules(api, db_session, people) -> None:  # noqa: ANN001
    kam1, kam2, head = people["kam1"], people["kam2"], people["head"]
    kam = api.as_user(kam1)
    on_other = await kam.post(
        f"{API}/universities", json={"name": "СПбПУ", "kam_user_id": str(kam2.id)}
    )
    assert on_other.status_code == 403
    on_self = await kam.post(
        f"{API}/universities", json={"name": "СПбПУ", "kam_user_id": str(kam1.id)}
    )
    assert on_self.status_code == 201, on_self.text
    created_id = uuid.UUID(on_self.json()["id"])
    (audit,) = await _audit(db_session, "university.kam_changed", created_id)
    assert audit.before == {"kam_user_id": None} and audit.after == {"kam_user_id": str(kam1.id)}
    without = await kam.post(f"{API}/universities", json={"name": "УрФУ"})
    assert without.status_code == 201
    assert await _audit(db_session, "university.kam_changed", uuid.UUID(without.json()["id"])) == []
    by_head = await api.as_user(head).post(
        f"{API}/universities", json={"name": "НГУ", "kam_user_id": str(kam2.id)}
    )
    assert by_head.status_code == 201 and by_head.json()["kam_user_id"] == str(kam2.id)


async def test_university_is_active_via_patch(api, db_session, workflows, people) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    kam1, admin = people["kam1"], people["admin"]
    busy = await factories.make_university(db_session, name="ИТМО", kam=kam1)
    await factories.make_deal(
        db_session, workflow=wf, stages=stages, responsible=kam1, university=busy
    )
    idle = await factories.make_university(db_session, name="МИФИ", kam=kam1)

    # КАМ скрыть свой вуз через PATCH не может (прежде обходил DELETE и проверку заявок)
    denied = await api.as_user(kam1).patch(
        f"{API}/universities/{idle.id}", json={"version": idle.xmin, "is_active": False}
    )
    assert denied.status_code == 403
    # администратор — тем же правилом, что DELETE: не при открытых заявках
    blocked = await api.as_user(admin).patch(
        f"{API}/universities/{busy.id}", json={"version": busy.xmin, "is_active": False}
    )
    assert blocked.status_code == 409
    done = await api.as_user(admin).patch(
        f"{API}/universities/{idle.id}", json={"version": idle.xmin, "is_active": False}
    )
    assert done.status_code == 200 and done.json()["is_active"] is False
    (audit,) = await _audit(db_session, "university.deactivated", idle.id)
    assert audit.after == {"is_active": False}
    # неизменённое значение и null — не изменение
    await db_session.refresh(busy)
    same = await api.as_user(kam1).patch(
        f"{API}/universities/{busy.id}", json={"version": busy.xmin, "is_active": True}
    )
    assert same.status_code == 200
    await db_session.refresh(busy)
    null = await api.as_user(kam1).patch(
        f"{API}/universities/{busy.id}", json={"version": busy.xmin, "is_active": None}
    )
    assert null.status_code == 200 and null.json()["is_active"] is True


# --------------------------------------------------------------------------------------
# P1-11: передача портфеля ушедшего КАМа
# --------------------------------------------------------------------------------------


async def test_transfer_portfolio(api, db_session, workflows, people) -> None:  # noqa: ANN001
    b2b, b2b_stages = workflows["b2b"]
    b2c, b2c_stages = workflows["b2c"]
    kam1, kam2, head = people["kam1"], people["kam2"], people["head"]
    open_1 = await factories.make_deal(
        db_session,
        workflow=b2b,
        stages=b2b_stages,
        responsible=kam1,
        path=[("new", ago(40)), ("first_contact", ago(30))],
    )
    open_2 = await factories.make_deal(
        db_session, workflow=b2b, stages=b2b_stages, responsible=kam1, path=[("new", ago(20))]
    )
    won = await factories.make_deal(
        db_session,
        workflow=b2b,
        stages=b2b_stages,
        responsible=kam1,
        path=[("new", ago(90)), ("in_learning", ago(60)), ("completed", ago(30))],
    )
    b2c_open = await factories.make_deal(
        db_session, workflow=b2c, stages=b2c_stages, responsible=kam1, path=[("new", ago(5))]
    )
    foreign = await factories.make_deal(
        db_session, workflow=b2b, stages=b2b_stages, responsible=kam2, path=[("new", ago(5))]
    )
    comment = await factories.make_comment(db_session, deal=open_1, author=kam1, at=ago(25))
    # получатель — КАМ только B2B: B2C-заявку он не увидит, она остаётся в отчёте skipped
    await make_role(
        db_session, code="kam_b2b", permissions=KAM_PERMISSIONS, workflow_types=("b2b",)
    )
    reset_role_catalog()
    successor = await factories.make_user(db_session, roles=("kam_b2b",), full_name="Сидоров Павел")
    # уходящий КАМ уже деактивирован — передача всё равно возможна
    kam1.is_active = False
    await db_session.flush()
    universities = sorted(
        {d.university_id for d in (open_1, open_2, won)}, key=str
    )  # вузы make_deal закреплены за ответственным
    history_before = {
        d.id: [(r.id, r.actor_user_id) for r in await _history(db_session, d)]
        for d in (open_1, open_2, won)
    }
    url = f"{API}/requests/transfer-portfolio"
    body = {"from_user_id": str(kam1.id), "to_user_id": str(successor.id)}

    # КАМ передавать портфели не может
    assert (await api.as_user(kam2).post(url, json=body)).status_code == 403
    # предпросмотр ничего не меняет
    preview = await api.as_user(head).post(url, json={**body, "dry_run": True})
    assert preview.status_code == 200, preview.text
    report = preview.json()
    assert report["dry_run"] is True
    assert report["requests"]["transferred"] == 2 and report["requests"]["skipped"] == 1
    assert report["requests"]["by_workflow"] == {"b2b": 2}
    assert report["requests"]["skipped_items"][0]["id"] == str(b2c_open.id)
    assert "B2C" in report["requests"]["skipped_items"][0]["reason"]
    assert report["universities"]["transferred"] == len(universities)
    await db_session.refresh(open_1)
    assert open_1.responsible_user_id == kam1.id

    done = await api.as_user(head).post(url, json=body)
    assert done.status_code == 200, done.text
    assert done.json()["dry_run"] is False
    for deal in (open_1, open_2, won, b2c_open, foreign):
        await db_session.refresh(deal)
    assert open_1.responsible_user_id == successor.id
    assert open_2.responsible_user_id == successor.id
    assert won.responsible_user_id == kam1.id  # закрытые заявки не передаются
    assert b2c_open.responsible_user_id == kam1.id  # процесс недоступен получателю
    assert foreign.responsible_user_id == kam2.id
    for university_id in universities:
        university = await db_session.get(University, university_id)
        await db_session.refresh(university)
        assert university.kam_user_id == successor.id
        (kam_audit,) = await _audit(db_session, "university.kam_changed", university_id)
        assert kam_audit.before == {"kam_user_id": str(kam1.id)}
        assert kam_audit.after["kam_user_id"] == str(successor.id)

    # аудит request.assigned — по каждой переданной заявке
    for deal in (open_1, open_2):
        (assigned,) = await _audit(db_session, "request.assigned", deal.id)
        assert assigned.actor_user_id == head.id
        assert assigned.before == {"assignee_id": str(kam1.id)}
        assert assigned.after == {"assignee_id": str(successor.id), "reason": "portfolio_transfer"}
    (summary,) = await _audit(db_session, "user.portfolio_transferred", kam1.id)
    assert summary.after == {
        "to_user_id": str(successor.id),
        "requests": 2,
        "universities": len(universities),
        "skipped": 1,
    }

    # история, комментарии и авторство прошлых действий не меняются
    for deal_id, rows in history_before.items():
        deal = await db_session.get(Deal, deal_id)
        assert [(r.id, r.actor_user_id) for r in await _history(db_session, deal)] == rows
    await db_session.refresh(comment)
    stored_comment = await db_session.get(DealComment, comment.id)
    assert stored_comment.author_user_id == kam1.id

    # повторная передача — пустой отчёт (B2C-заявка по-прежнему пропущена)
    again = (await api.as_user(head).post(url, json=body)).json()
    assert again["requests"]["transferred"] == 0 and again["universities"]["transferred"] == 0


async def test_transfer_portfolio_validation_and_university_right(
    api, db_session, workflows, people
) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    kam1, kam2, admin = people["kam1"], people["kam2"], people["admin"]
    deal = await factories.make_deal(db_session, workflow=wf, stages=stages, responsible=kam1)
    url = f"{API}/requests/transfer-portfolio"

    same = await api.as_user(admin).post(
        url, json={"from_user_id": str(kam1.id), "to_user_id": str(kam1.id)}
    )
    assert same.status_code == 400
    inactive = await factories.make_user(db_session, roles=("kam",), is_active=False)
    to_inactive = await api.as_user(admin).post(
        url, json={"from_user_id": str(kam1.id), "to_user_id": str(inactive.id)}
    )
    assert to_inactive.status_code == 400
    assert to_inactive.json()["error"]["details"][0]["field"] == "to_user_id"
    unknown = await api.as_user(admin).post(
        url, json={"from_user_id": str(uuid.uuid4()), "to_user_id": str(kam2.id)}
    )
    assert unknown.status_code == 400

    # своя роль с назначением заявок, но без назначения КАМов вузов
    await make_role(
        db_session,
        code="dispatcher",
        permissions={"requests:read", "requests:assign", "requests:assign_any", "admin:users"},
    )
    reset_role_catalog()
    dispatcher = await factories.make_user(db_session, roles=("dispatcher",))
    body = {"from_user_id": str(kam1.id), "to_user_id": str(kam2.id)}
    with_universities = await api.as_user(dispatcher).post(url, json=body)
    assert with_universities.status_code == 403
    only_requests = await api.as_user(dispatcher).post(
        url, json={**body, "include_universities": False}
    )
    assert only_requests.status_code == 200, only_requests.text
    assert only_requests.json()["requests"]["transferred"] == 1
    assert only_requests.json()["universities"] == {
        "included": False,
        "transferred": 0,
        "items": [],
    }
    await db_session.refresh(deal)
    assert deal.responsible_user_id == kam2.id
    university = await db_session.get(University, deal.university_id)
    assert university.kam_user_id == kam1.id


# --------------------------------------------------------------------------------------
# Миграция 0006: каталог прав совпадает с кодом
# --------------------------------------------------------------------------------------


async def test_migration_0006_permission_and_column(db_session, migrated_db) -> None:  # noqa: ANN001
    permission = (
        await db_session.execute(
            sa.text("SELECT group_code, is_scoped FROM app_permission WHERE code = :c"),
            {"c": "universities:assign"},
        )
    ).one()
    assert tuple(permission) == ("crm", False)
    holders = set(
        (
            await db_session.execute(
                sa.text(
                    "SELECT r.code FROM app_role r JOIN app_role_permission p "
                    "ON p.role_id = r.id WHERE p.permission_code = 'universities:assign'"
                )
            )
        ).scalars()
    )
    assert holders == {"admin", "head_kam"}
    assert migrated_db.head == "0006" or migrated_db.head > "0006"
    assert set(ATTACHMENT_FORMATS) >= {"png", "jpeg", "pdf", "zip", "gzip", "rar"}
