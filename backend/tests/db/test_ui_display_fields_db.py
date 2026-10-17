"""Поток B, «пустые поля в интерфейсе»: ответы API содержат то, что читает фронт.

Каждый тест закрепляет поле, без которого экран показывал прочерк:
- «Владелец» договора и колонка «Вуз / Контрагент» реестра — `university` / `counterparty`
  в ContractOut (карточка, реестр, создание, правка);
- «Студенты вуза (N)» — счётчик лежит в `aggregates.students_count`, а не в корне;
- колонки «Вуз», «Программа», «Федпроект» реестра и доски talent pool — подписи связей
  в списке GET /students, подгруженные пачкой (число запросов не растёт со студентами);
- удалённый комментарий заявки помечен флагом `deleted`, а не `deleted_at`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import AppUser, Contract
from app.models.crm import Product, Program, University
from app.models.talent import FederalProject, Student

from . import factories

API = "/api/v1"


async def _make_program(session: AsyncSession, name: str) -> Program:
    program = Program(name=name)
    session.add(program)
    await session.flush()
    return program


async def _make_federal_project(session: AsyncSession, name: str) -> FederalProject:
    project = FederalProject(name=name)
    session.add(project)
    await session.flush()
    return project


async def _make_student(
    session: AsyncSession,
    *,
    university: University | None = None,
    program: Program | None = None,
    federal_project: FederalProject | None = None,
) -> Student:
    n = uuid.uuid4().hex
    student = Student(
        full_name=f"Студентов {n[:6]} Тест",
        email=f"{n[:10]}@student.test",
        email_hmac=(n * 2)[:64],
        display_name=f"Студентов {n[:4]}.",
        university_id=university.id if university else None,
        program_id=program.id if program else None,
        federal_project_id=federal_project.id if federal_project else None,
    )
    session.add(student)
    await session.flush()
    return student


@contextmanager
def _count_statements(engine: AsyncEngine) -> Iterator[list[str]]:
    statements: list[str] = []

    def on_execute(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    sa.event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
    try:
        yield statements
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", on_execute)


# --- договоры: «Владелец» -------------------------------------------------------------------


async def test_contract_owner_university_in_card_list_create_and_patch(
    api, db_session: AsyncSession, admin_user: AppUser
) -> None:
    university = await factories.make_university(db_session, name="НИУ «Тестовый»")
    client = api.as_user(admin_user)

    created = await client.post(
        f"{API}/contracts", json={"number": "Д-OWNER-1", "university_id": str(university.id)}
    )
    assert created.status_code == 201, created.text
    expected = {"id": str(university.id), "name": "НИУ «Тестовый»"}
    assert created.json()["university"] == expected
    assert created.json()["counterparty"] is None
    contract_id = created.json()["id"]

    card = await client.get(f"{API}/contracts/{contract_id}")
    assert card.status_code == 200, card.text
    assert card.json()["university"] == expected

    listed = await client.get(f"{API}/contracts", params={"university_id": str(university.id)})
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    assert item["university"] == expected

    patched = await client.patch(
        f"{API}/contracts/{contract_id}",
        json={"version": card.json()["version"], "notes": "правка"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["university"] == expected


async def test_contract_create_with_and_without_products(
    api, db_session: AsyncSession, admin_user: AppUser
) -> None:
    # без продуктов POST /contracts отвечал 500: ответ читал незагруженную коллекцию
    university = await factories.make_university(db_session)
    product = Product(code=f"p-{uuid.uuid4().hex[:8]}", name="Курс")
    db_session.add(product)
    await db_session.flush()
    client = api.as_user(admin_user)

    bare = await client.post(
        f"{API}/contracts", json={"number": "Д-NOPROD", "university_id": str(university.id)}
    )
    assert bare.status_code == 201, bare.text
    assert bare.json()["product_ids"] == [] and bare.json()["licenses"] == []

    with_product = await client.post(
        f"{API}/contracts",
        json={
            "number": "Д-PROD",
            "university_id": str(university.id),
            "product_ids": [str(product.id)],
        },
    )
    assert with_product.status_code == 201, with_product.text
    assert with_product.json()["product_ids"] == [str(product.id)]
    assert with_product.json()["university"]["name"] == university.name


async def test_contract_owner_counterparty_has_display_name_only(
    api, db_session: AsyncSession, admin_user: AppUser
) -> None:
    counterparty = await factories.make_counterparty(
        db_session, kind="person", display_name="Клиентов И. П.", full_name="Клиентов Иван Петрович"
    )
    contract = Contract(number="Д-OWNER-2", counterparty_id=counterparty.id)
    db_session.add(contract)
    await db_session.flush()

    card = await api.as_user(admin_user).get(f"{API}/contracts/{contract.id}")
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["university"] is None
    # ПДн контрагента (ФИО, email, телефон) в договор не попадают — только подпись
    assert body["counterparty"] == {"id": str(counterparty.id), "display_name": "Клиентов И. П."}


async def test_contract_list_owner_names_batched(
    api, db_session: AsyncSession, db_engine: AsyncEngine, admin_user: AppUser
) -> None:
    client = api.as_user(admin_user)
    # прогрев: каталог ролей и пользователь грузятся первым запросом, дальше — из кэша
    assert (await client.get(f"{API}/contracts", params={"limit": 1})).status_code == 200

    async def list_queries(count: int, prefix: str) -> int:
        for i in range(count):
            university = await factories.make_university(db_session)
            db_session.add(Contract(number=f"{prefix}-{i}", university_id=university.id))
        await db_session.flush()
        with _count_statements(db_engine) as statements:
            response = await client.get(f"{API}/contracts", params={"search": prefix})
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == count
        assert all(item["university"] and item["university"]["name"] for item in items)
        return len(statements)

    assert await list_queries(1, "BATCH-A") == await list_queries(4, "BATCH-B")


# --- вуз: «Студенты вуза (N)» ----------------------------------------------------------------


async def test_university_card_students_count_in_aggregates(
    api, db_session: AsyncSession, admin_user: AppUser
) -> None:
    university = await factories.make_university(db_session)
    other = await factories.make_university(db_session)
    await _make_student(db_session, university=university)
    await _make_student(db_session, university=university)
    await _make_student(db_session, university=other)

    response = await api.as_user(admin_user).get(f"{API}/universities/{university.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["aggregates"]["students_count"] == 2
    # фронт читает счётчик из aggregates (types.ts University.aggregates), в корне его нет
    assert "students_count" not in body


# --- talent pool: «Вуз», «Программа», «Федпроект» --------------------------------------------


async def test_students_list_has_reference_names(
    api, db_session: AsyncSession, admin_user: AppUser
) -> None:
    university = await factories.make_university(db_session, name="УрФУ-тест")
    program = await _make_program(db_session, "Python-разработчик")
    project = await _make_federal_project(db_session, "Кадры для цифровой экономики")
    full = await _make_student(
        db_session, university=university, program=program, federal_project=project
    )
    bare = await _make_student(db_session)

    response = await api.as_user(admin_user).get(f"{API}/students", params={"limit": 200})
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}

    assert items[str(full.id)]["university"] == {"id": str(university.id), "name": "УрФУ-тест"}
    assert items[str(full.id)]["program"] == {"id": str(program.id), "name": "Python-разработчик"}
    assert items[str(full.id)]["federal_project"] == {
        "id": str(project.id),
        "name": "Кадры для цифровой экономики",
    }
    for key in ("university", "program", "federal_project"):
        assert items[str(bare.id)][key] is None


async def test_students_list_reference_names_batched(
    api, db_session: AsyncSession, db_engine: AsyncEngine, admin_user: AppUser
) -> None:
    client = api.as_user(admin_user)
    # прогрев: каталог ролей и пользователь грузятся первым запросом, дальше — из кэша
    assert (await client.get(f"{API}/students", params={"limit": 1})).status_code == 200

    async def list_queries(count: int) -> int:
        program = await _make_program(db_session, f"Программа {uuid.uuid4().hex[:6]}")
        project = await _make_federal_project(db_session, f"Федпроект {uuid.uuid4().hex[:6]}")
        for _ in range(count):
            # у каждого студента свой вуз — N разных id на странице
            await _make_student(
                db_session,
                university=await factories.make_university(db_session),
                program=program,
                federal_project=project,
            )
        with _count_statements(db_engine) as statements:
            response = await client.get(
                f"{API}/students", params={"program_id": str(program.id), "limit": 200}
            )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == count
        assert all(item["university"] and item["program"] for item in items)
        return len(statements)

    assert await list_queries(1) == await list_queries(5)


# --- комментарии заявки: флаг удаления --------------------------------------------------------


async def test_request_comments_deleted_flag(
    api, db_session: AsyncSession, workflows: dict[str, Any]
) -> None:
    workflow, stages = workflows["b2b"]
    kam = await factories.make_user(db_session, roles=("kam",))
    deal = await factories.make_deal(db_session, workflow=workflow, stages=stages, responsible=kam)
    alive = await factories.make_comment(db_session, deal=deal, author=kam, body="Живой")
    gone = await factories.make_comment(db_session, deal=deal, author=kam, deleted=True)

    response = await api.as_user(kam).get(f"{API}/requests/{deal.id}/comments")
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[str(alive.id)]["deleted"] is False
    assert items[str(alive.id)]["text"] == "Живой"
    assert items[str(gone.id)]["deleted"] is True
    # фронт (RequestComments.isCommentDeleted) читает именно `deleted`; `deleted_at` не отдаётся
    assert "deleted_at" not in items[str(gone.id)]
