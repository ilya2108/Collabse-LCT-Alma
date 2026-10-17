"""Импорт «Таблица КАМов» (P1-12) через мастер импорта на настоящем PostgreSQL.

Сквозной сценарий API: загрузка → автопредложение маппинга → dry-run → применение.
Строка файла — лицензия на ИТ-продукт по договору с вузом: вуз и продукт находятся или
создаются, договор upsert'ится по номеру, строка «договор × продукт» получает поля
лицензии (миграция 0008), вуз — ответственного КАМа по «Фамилия Имя», контакты вуза
дописываются. Права — как у карточек (каталог F2), нарушения — построчные ошибки.

Мир: руководитель «Морозов Дмитрий», КАМы «Петров Константин» и «Иванова Мария»,
наблюдатель; направления «Анализ данных», DevOps, «Тестирование (QA)», «Инженерия
данных»; в каталоге уже есть продукт «Пример.Аналитика» (без вендора).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app.models import (
    AuditLog,
    Contract,
    ContractProduct,
    ImportRowError,
    ImportSession,
    ItDirection,
    Product,
    ProductDirection,
    University,
    UniversityContact,
)

from . import factories

API = "/api/v1"
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "kam_catalog_example.xlsx"
CREATE = {"university": "match_or_create", "product": "match_or_create"}


@pytest.fixture
async def world(db_session, memory_storage) -> dict[str, Any]:  # noqa: ARG001 — MinIO в памяти
    make = factories.make_user
    head = await make(db_session, roles=("head_kam",), full_name="Морозов Дмитрий Александрович")
    kam1 = await make(
        db_session, roles=("kam",), username="kam1@test", full_name="Петров Константин Андреевич"
    )
    kam2 = await make(db_session, roles=("kam",), full_name="Иванова Мария Сергеевна")
    observer = await make(db_session, roles=("observer",), full_name="Кузнецов Олег Игоревич")
    directions = {
        code: ItDirection(code=code, name=name)
        for code, name in (
            ("data-analysis", "Анализ данных"),
            ("devops", "DevOps"),
            ("qa", "Тестирование (QA)"),
            ("data-engineering", "Инженерия данных"),
        )
    }
    analytics = Product(code="primer-analytics", name="Пример.Аналитика", product_type="other")
    db_session.add_all([*directions.values(), analytics])
    await db_session.flush()
    return {
        "head": head,
        "kam1": kam1,
        "kam2": kam2,
        "observer": observer,
        "directions": directions,
        "analytics": analytics,
    }


# --- помощники мастера ----------------------------------------------------------------------


async def upload(client, data: bytes, name: str = "kam.xlsx") -> dict[str, Any]:  # noqa: ANN001
    response = await client.post(
        f"{API}/import/sessions",
        files={"file": (name, data)},
        data={"entity_type": "kam_catalog"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auto_mapping(session: dict[str, Any], rules: dict[str, str] | None = None) -> list[dict]:
    mapping = [
        {"source_column": s["source_column"], "target_field": s["target_field"]}
        for s in session["suggested_mapping"]
    ]
    for entry in mapping:
        if rules and entry["target_field"] in rules:
            entry["lookup"] = rules[entry["target_field"]]
    return mapping


async def validate(
    client,  # noqa: ANN001
    session: dict[str, Any],
    rules: dict[str, str] | None = None,
    strategy: str = "upsert",
) -> dict[str, Any]:
    saved = await client.put(
        f"{API}/import/sessions/{session['id']}/mapping",
        json={"mapping": auto_mapping(session, rules), "options": {"update_strategy": strategy}},
    )
    assert saved.status_code == 200, saved.text
    report = await client.post(f"{API}/import/sessions/{session['id']}/validate")
    assert report.status_code == 200, report.text
    return report.json()


async def apply(client, session: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    response = await client.post(
        f"{API}/import/sessions/{session['id']}/apply", json={"mode": "skip_errors"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "completed"
    return body["result"]


def csv_bytes(rows: list[list[str]], encoding: str = "utf-8-sig") -> bytes:
    """CSV «как из Excel»: разделитель «;», ячейки с «;» и кавычками — в кавычках."""
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode(encoding)


def issues_by_row(report: dict[str, Any]) -> dict[int, set[tuple[str | None, str]]]:
    result: dict[int, set[tuple[str | None, str]]] = {}
    for issue in report["errors"]:
        result.setdefault(issue["row"], set()).add((issue["column"], issue["code"]))
    return result


async def contract_lines(db_session, number: str) -> tuple[Contract, dict[str, ContractProduct]]:  # noqa: ANN001
    contract = (
        await db_session.execute(
            sa.select(Contract)
            .where(sa.func.lower(Contract.number) == number.lower())
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    rows = (
        await db_session.execute(
            sa.select(Product.name, ContractProduct)
            .join(Product, Product.id == ContractProduct.product_id)
            .where(ContractProduct.contract_id == contract.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    return contract, dict(rows)


async def university_named(db_session, name: str) -> University:  # noqa: ANN001
    return (
        await db_session.execute(
            sa.select(University)
            .where(University.name == name)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


# --- сквозной сценарий на фикстуре -----------------------------------------------------------


async def test_kam_table_fixture_end_to_end(api, db_session, world) -> None:
    client = api.as_user(world["head"])
    session = await upload(client, FIXTURE.read_bytes())

    assert session["entity_type"] == "kam_catalog"
    suggested = {s["target_field"] for s in session["suggested_mapping"]}
    assert len(suggested) == 11  # «Служебная колонка» не маппится
    required = {f["field"] for f in session["target_fields"] if f["required"]}
    assert required == {"university", "product", "contract_number"}
    # ПДн «Ответственных от вуза» в сэмплах маскированы уже при распознавании
    contacts_column = next(
        c for c in session["detected"]["columns"] if c["name"] == "Контакты вуза"
    )
    assert contacts_column["samples"] == ["С***а А.О.", "О***в И.П.; Б***а О.В."]

    report = await validate(client, session, CREATE)
    assert (report["total_rows"], report["valid_rows"], report["error_rows"]) == (5, 3, 2)
    assert report["error_report_file_id"]
    issues = issues_by_row(report)
    assert issues[6] == {("product", "duplicate_in_file")}
    assert issues[7] == {
        ("license_signed_at", "invalid_format"),
        ("license_term", "invalid_format"),
        ("transfer_status", "invalid_value"),
        ("kam", "lookup_not_found"),
    }
    # dry-run ничего не пишет
    assert (
        await db_session.execute(sa.select(sa.func.count()).select_from(Contract))
    ).scalar() == 0

    result = await apply(client, session)
    assert (result["created"], result["updated"], result["skipped"], result["failed"]) == (
        3,
        0,
        0,
        2,
    )
    assert result["details"] == {
        "universities_created": 2,
        "kam_assigned": 2,
        "contracts_created": 2,
        "products_created": 2,
        "products_updated": 1,  # вендор у существующего «Пример.Аналитика»
        "directions_linked": 4,
        "licenses_created": 3,
        "contacts_created": 3,
    }

    north = await university_named(db_session, "Северный технологический университет")
    south = await university_named(db_session, "Южный гуманитарный институт")
    assert north.kam_user_id == world["kam1"].id
    assert south.kam_user_id == world["kam2"].id

    contract, lines = await contract_lines(db_session, "ЛЦ-2026/001")
    assert contract.university_id == north.id
    assert contract.responsible_user_id == world["kam1"].id
    assert contract.status == "active"
    assert set(lines) == {"Пример.Аналитика", "Пример.Конвейер"}
    first = lines["Пример.Аналитика"]
    assert (
        first.license_signed_at,
        first.license_term_years,
        first.license_valid_to,
        first.transfer_status,
        first.note,
    ) == (date(2026, 2, 1), 1, date(2027, 1, 31), "transferred", "Пилот на кафедре ИТ")
    assert first.transfer_status_updated_at is not None
    assert first.added_by == world["head"].id
    second = lines["Пример.Конвейер"]
    assert (second.license_term_years, second.license_valid_to, second.transfer_status) == (
        None,
        date(2028, 12, 31),
        "in_progress",
    )

    contract2, lines2 = await contract_lines(db_session, "ЛЦ-2026/002")
    assert contract2.university_id == south.id and contract2.responsible_user_id == world["kam2"].id
    storage = lines2["Демо.Хранилище"]
    assert (
        storage.license_signed_at,
        storage.license_term_years,
        storage.license_valid_to,
        storage.transfer_status,
    ) == (date(2026, 3, 15), 3, date(2029, 3, 14), "not_started")

    # карточка договора отдаёт лицензии по продуктам (ContractOut.licenses)
    card = await client.get(f"{API}/contracts/{contract.id}")
    assert card.status_code == 200, card.text
    licenses = {item["product_id"]: item for item in card.json()["licenses"]}
    assert set(licenses) == {str(link.product_id) for link in lines.values()}
    assert licenses[str(first.product_id)] | {"transfer_status_updated_at": None} == {
        "product_id": str(first.product_id),
        "license_signed_at": "2026-02-01",
        "license_term_years": 1,
        "license_valid_to": "2027-01-31",
        "transfer_status": "transferred",
        "transfer_status_updated_at": None,
        "note": "Пилот на кафедре ИТ",
    }
    assert licenses[str(first.product_id)]["transfer_status_updated_at"]

    products = {
        p.name: p
        for p in (
            await db_session.execute(sa.select(Product).execution_options(populate_existing=True))
        ).scalars()
    }
    assert products["Пример.Конвейер"].description == "Вендор: ООО «Пример Софт»"
    assert products["Пример.Конвейер"].product_type == "other"
    assert products["Пример.Аналитика"].description == "Вендор: ООО «Пример Софт»"
    assert products["Демо.Хранилище"].description == "Вендор: АО «Демо Данные»"
    links = {
        (name, code)
        for name, code in (
            await db_session.execute(
                sa.select(Product.name, ItDirection.code)
                .join(ProductDirection, ProductDirection.product_id == Product.id)
                .join(ItDirection, ItDirection.id == ProductDirection.direction_id)
            )
        ).all()
    }
    assert links == {
        ("Пример.Аналитика", "data-analysis"),
        ("Пример.Конвейер", "devops"),
        ("Пример.Конвейер", "qa"),
        ("Демо.Хранилище", "data-engineering"),
    }

    contacts = (
        (
            await db_session.execute(
                sa.select(UniversityContact).order_by(UniversityContact.full_name)
            )
        )
        .scalars()
        .all()
    )
    by_name = {c.full_name: c for c in contacts}
    assert set(by_name) == {
        "Белова Ольга Викторовна",
        "Орлов Игорь Петрович",
        "Смирнова Анна Олеговна",
    }
    smirnova = by_name["Смирнова Анна Олеговна"]
    assert (smirnova.university_id, smirnova.position, smirnova.phone, smirnova.email) == (
        north.id,
        "проректор",
        "+79000000001",
        "smirnova@example.edu",
    )
    assert smirnova.is_primary
    assert by_name["Орлов Игорь Петрович"].is_primary
    assert not by_name["Белова Ольга Викторовна"].is_primary
    assert by_name["Белова Ольга Викторовна"].position == "декан"

    audit = (
        await db_session.execute(
            sa.select(AuditLog.action, AuditLog.entity_id, AuditLog.after).where(
                AuditLog.action.in_(("university.kam_changed", "contract.created"))
            )
        )
    ).all()
    kam_changes = {
        row.entity_id: row.after for row in audit if row.action == "university.kam_changed"
    }
    assert kam_changes[north.id]["kam_user_id"] == str(world["kam1"].id)
    assert kam_changes[north.id]["reason"] == "import"
    assert sum(1 for row in audit if row.action == "contract.created") == 2

    stored = await db_session.get(ImportSession, uuid.UUID(session["id"]))
    await db_session.refresh(stored)
    assert stored.status == "completed"
    errors = (
        (
            await db_session.execute(
                sa.select(ImportRowError.row_number).where(
                    ImportRowError.import_session_id == stored.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(errors) == [6, 7]


# --- upsert по номеру договора и стратегии дублей ----------------------------------------


@pytest.fixture
async def existing_line(db_session, world) -> dict[str, Any]:
    university = await factories.make_university(
        db_session, name="Северный технологический университет", kam=world["kam1"]
    )
    contract = Contract(
        number="ЛЦ-7",
        university_id=university.id,
        status="active",
        responsible_user_id=world["kam1"].id,
    )
    db_session.add(contract)
    await db_session.flush()
    db_session.add(
        ContractProduct(
            contract_id=contract.id,
            product_id=world["analytics"].id,
            license_signed_at=date(2025, 2, 1),
            license_term_years=1,
            license_valid_to=date(2026, 1, 31),
            transfer_status="in_progress",
        )
    )
    await db_session.flush()
    return {"university": university, "contract": contract}


async def test_upsert_by_contract_number_cp1251(api, db_session, world, existing_line) -> None:
    client = api.as_user(world["head"])
    header = ["Вуз", "ПО", "Номер договора", "Статус передачи", "Подписание лицензии", "Лишняя"]
    row = [
        "Северный технологический университет",
        "primer-analytics",
        "лц-7",
        "Передана",
        "01.02.2026",
        "x",
    ]
    data = csv_bytes([header, row, ["", "", "", "", "", ""]], encoding="cp1251")

    session = await upload(client, data, "kam.csv")
    assert session["detected"]["encoding"] == "cp1251"
    report = await validate(client, session)
    assert (report["total_rows"], report["valid_rows"]) == (1, 1)
    result = await apply(client, session)
    assert (result["created"], result["updated"]) == (0, 1)
    assert result["details"] == {"licenses_updated": 1}

    contract, lines = await contract_lines(db_session, "ЛЦ-7")
    assert contract.id == existing_line["contract"].id
    line = lines["Пример.Аналитика"]
    # переподписание пересчитало окончание по сохранённому сроку
    assert (line.license_signed_at, line.license_term_years, line.license_valid_to) == (
        date(2026, 2, 1),
        1,
        date(2027, 1, 31),
    )
    assert line.transfer_status == "transferred"
    assert line.transfer_status_updated_at is not None
    assert (
        await db_session.execute(sa.select(sa.func.count()).select_from(Contract))
    ).scalar() == 1

    # «только новые»: существующий договор пропускается целиком
    again = await upload(client, data, "kam.csv")
    await validate(client, again, strategy="create_only")
    assert (await apply(client, again))["skipped"] == 1

    # «только новые»: новый договор из двух строк файла создаётся целиком
    two_rows = [
        ["Северный технологический университет", "primer-analytics", "ЛЦ-9", "", "", ""],
        ["Северный технологический университет", "Пример.Конвейер", "ЛЦ-9", "", "", ""],
    ]
    pair = await upload(client, csv_bytes([header, *two_rows]), "kam.csv")
    await validate(client, pair, CREATE, strategy="create_only")
    assert (await apply(client, pair))["created"] == 2
    assert set((await contract_lines(db_session, "ЛЦ-9"))[1]) == {
        "Пример.Аналитика",
        "Пример.Конвейер",
    }

    # «только обновлять»: нового договора не появится
    new_row = ["Северный технологический университет", "primer-analytics", "ЛЦ-8", "", "", ""]
    fresh = await upload(client, csv_bytes([header, new_row]), "kam.csv")
    await validate(client, fresh, strategy="update_only")
    assert (await apply(client, fresh))["skipped"] == 1
    assert (
        await db_session.execute(sa.select(sa.func.count()).select_from(Contract))
    ).scalar() == 2  # ЛЦ-7 и ЛЦ-9


# --- права: как у карточек вуза, договора и продукта ---------------------------------------


async def test_kam_rights_are_row_errors(api, db_session, world, existing_line) -> None:
    kam2 = world["kam2"]
    own = await factories.make_university(db_session, name="Южный гуманитарный институт", kam=kam2)
    header = ["Вуз", "ПО", "Номер договора", "Ответственный КАМ", "Контакты вуза"]
    rows = [
        header,
        # 2: чужой договор и смена КАМа чужого вуза
        ["Северный технологический университет", "Пример.Аналитика", "ЛЦ-7", "Иванова Мария", ""],
        # 3: продукта нет, а создавать продукты КАМ не вправе
        [own.name, "Новый продукт", "К-1", "", ""],
        # 4: свой вуз — можно: новый договор и контакт
        [own.name, "Пример.Аналитика", "К-2", "Иванова Мария", "Орлов Игорь"],
        # 5: новый вуз КАМ закрепляет только за собой
        ["Новый вуз", "Пример.Аналитика", "К-3", "Петров Константин", ""],
        # 6: контакты чужого вуза
        ["Северный технологический университет", "Пример.Аналитика", "К-4", "", "Сидоров Сидор"],
    ]
    client = api.as_user(kam2)
    session = await upload(client, csv_bytes(rows), "kam.csv")
    report = await validate(client, session, CREATE)
    issues = issues_by_row(report)
    assert issues == {
        2: {("contract_number", "forbidden"), ("kam", "forbidden")},
        3: {("product", "forbidden")},
        5: {("kam", "forbidden")},
        6: {("university_contacts", "forbidden")},
    }
    result = await apply(client, session)
    assert (result["created"], result["failed"]) == (1, 4)
    contract, lines = await contract_lines(db_session, "К-2")
    assert contract.university_id == own.id and contract.responsible_user_id == kam2.id
    assert contract.status == "draft"  # без даты подписания лицензии — черновик
    assert set(lines) == {"Пример.Аналитика"} and lines["Пример.Аналитика"].transfer_status is None

    # наблюдатель без import:run и тем более без прав на вузы и договоры
    denied = await api.as_user(world["observer"]).post(
        f"{API}/import/sessions",
        files={"file": ("kam.csv", csv_bytes(rows))},
        data={"entity_type": "kam_catalog"},
    )
    assert denied.status_code == 403


# --- конфликты договоров, поиск КАМа, справочники, даты ----------------------------------------


async def test_conflicts_and_lookups(api, db_session, world, admin_user, existing_line) -> None:
    north = existing_line["university"]
    other = await factories.make_university(db_session, name="Восточный университет")
    client_b2c = await factories.make_counterparty(db_session, kind="legal_entity")
    db_session.add(Contract(number="Д-B2C", counterparty_id=client_b2c.id))
    await factories.make_user(db_session, roles=("kam",), full_name="Сидоров Сергей Петрович")
    await factories.make_user(db_session, roles=("kam",), full_name="Сидоров Сергей Ильич")
    await factories.make_user(
        db_session, roles=("kam",), full_name="Ушедший Андрей Андреевич", is_active=False
    )
    await db_session.flush()
    n, p = north.name, "Пример.Аналитика"
    header = [
        "Вуз",
        "ПО",
        "Номер договора",
        "КАМ",
        "ИТ-направление",
        "Подписание лицензии",
        "Срок лицензии",
    ]
    rows = [
        header,
        [other.name, "Пример.Конвейер", "ЛЦ-7", "", "", "", ""],  # 2: договор другого вуза
        [n, p, "Д-B2C", "", "", "", ""],  # 3: договор B2C
        [n, p, "Н-1", "Сидоров Сергей", "", "", ""],  # 4: двое с таким именем
        [n, p, "Н-2", "Ушедший Андрей", "", "", ""],  # 5: деактивирован
        [n, p, "Н-3", "Петров", "", "", ""],  # 6: только фамилия
        [n, p, "Н-4", "kam1@test", "", "", ""],  # 7: логин — можно
        [other.name, p, "Н-4", "", "", "", ""],  # 8: тот же договор — другой вуз в файле
        ["1234567890", p, "Н-5", "", "", "", ""],  # 9: ИНН без вуза
        [n, p, "Н-6", "", "Несуществующее", "", ""],  # 10: нет направления
        [n, p, "Н-7", "", "", "01.02.2026", "2025"],  # 11: окончание раньше подписания
        [n, f"{p}, Пример.Конвейер", "Н-8", "", "devops; qa", "01.02.2026", "бессрочно"],  # 12
        [n, p, "Н-9", "Иванова Мария", "", "", ""],  # 13: у вуза в строке 7 другой КАМ
        [n, p, "ЛЦ-7", "", "", "01.03.2026", "до 2025"],  # 14: конфликт с сохранённой датой
    ]
    client = api.as_user(admin_user)
    session = await upload(client, csv_bytes(rows), "kam.csv")
    report = await validate(client, session, CREATE)
    issues = issues_by_row(report)
    assert issues == {
        2: {("contract_number", "conflict")},
        3: {("contract_number", "conflict")},
        4: {("kam", "ambiguous")},
        5: {("kam", "inactive_user")},
        6: {("kam", "invalid_format")},
        # тот же договор с тем же продуктом — ещё и дубль строки 7
        8: {("contract_number", "conflict_in_file"), ("product", "duplicate_in_file")},
        9: {("university", "lookup_not_found")},
        10: {("it_direction", "lookup_not_found")},
        11: {("license_term", "invalid_value")},
        13: {("kam", "conflict_in_file")},
        14: {("license_term", "invalid_value")},
    }
    assert report["valid_rows"] == 2  # строки 7 и 12

    result = await apply(client, session)
    assert result["created"] == 2
    await db_session.refresh(north)
    assert north.kam_user_id == world["kam1"].id  # строка 7: КАМ по логину
    contract, lines = await contract_lines(db_session, "Н-8")
    assert set(lines) == {"Пример.Аналитика", "Пример.Конвейер"}  # два продукта в ячейке
    assert all(
        (line.license_signed_at, line.license_term_years, line.license_valid_to)
        == (date(2026, 2, 1), None, None)
        for line in lines.values()
    )
