"""Создание B2C-заявки с доски (CreateRequestDrawer) на настоящем PostgreSQL.

Форма шлёт нового клиента (`client_kind` + `client`) без `counterparty_id`; раньше
`RequestCreate` молча отбрасывал эти поля и отвечал 400 «Для B2C-заявки обязателен клиент».
Здесь — ровно тот payload, который собирает форма (физлицо и юрлицо), старый путь с
`counterparty_id`, поиск существующего клиента (email физлица, ИНН юрлица), шифрование ПДн
(Fernet + blind index), аудит без открытых ПДн и права (процесс вне роли, наблюдатель,
роль без `clients:pii`).

Общий хелпер контрагента (`requests.service.find_or_create_counterparty`) делят форма и лид
CMS: отдельные тесты фиксируют, что поведение `cms.lead.created` не изменилось.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import reset_role_catalog
from app.core.errors import ApiError
from app.core.pii import get_pii_crypto
from app.models import AuditLog, Counterparty, Deal
from app.models.crm import InteractionType
from app.modules.integrations import service as integrations_service
from app.modules.integrations.schemas import Correlation, InboundEnvelope

from . import factories
from .rbac_factories import KAM_PERMISSIONS, make_role

PERSON = {
    "full_name": "Смирнова Ольга Викторовна",
    "email": "Olga.Smirnova@example.com",
    "phone": "+7 916 000-11-22",
}
COMPANY = {
    "company_name": "ООО «Пример-Обучение»",
    "inn": "7701234567",
    "email": "hr@primer.example",
    "phone": "+7 495 000-00-00",
}


# --------------------------------------------------------------------------------------
# Помощники
# --------------------------------------------------------------------------------------


@pytest.fixture
async def interaction_type(db_session: AsyncSession) -> InteractionType:
    row = InteractionType(code=f"course_{uuid.uuid4().hex[:6]}", name="Покупка курса")
    db_session.add(row)
    await db_session.flush()
    return row


def form_payload(
    interaction_type_id: uuid.UUID,
    *,
    kind: str = "person",
    client: dict[str, Any] | None = None,
    title: str = "Смирнова — курс Python",
) -> dict[str, Any]:
    """Тело POST /requests ровно как его собирает CreateRequestDrawer.submit для B2C:
    product/program/amount/description — null, пустые поля клиента не отправляются
    (`undefined` в JSON.stringify), assignee_id — только если выбран."""
    if client is None:
        client = dict(PERSON) if kind == "person" else dict(COMPANY)
    return {
        "workflow_type": "b2c",
        "title": title,
        "product_id": None,
        "program_id": None,
        "amount": None,
        "description": None,
        "client_kind": kind,
        "interaction_type_id": str(interaction_type_id),
        "client": client,
    }


async def _counterparties(session: AsyncSession) -> list[Counterparty]:
    return list(
        (await session.execute(sa.select(Counterparty).order_by(Counterparty.created_at)))
        .scalars()
        .all()
    )


async def _audit(session: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        (await session.execute(sa.select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )


async def _raw_pii(session: AsyncSession, counterparty_id: uuid.UUID) -> bytes:
    row = (
        await session.execute(
            sa.text("SELECT full_name_enc, email_enc, phone_enc FROM counterparty WHERE id = :id"),
            {"id": counterparty_id},
        )
    ).one()
    return b"".join(bytes(value) for value in row if value is not None)


def _lead_envelope(lead: dict[str, Any]) -> InboundEnvelope:
    return InboundEnvelope(
        event_id=uuid.uuid4(),
        event_type="cms.lead.created",
        occurred_at=datetime.now(timezone.utc),
        source="cms",
        correlation=Correlation(cms_lead_id=f"lead-{uuid.uuid4().hex[:8]}"),
        payload={"lead": lead},
    )


# --------------------------------------------------------------------------------------
# Форма доски: новый клиент
# --------------------------------------------------------------------------------------


async def test_form_payload_person_creates_request_and_client(
    api, db_session: AsyncSession, workflows, interaction_type
) -> None:  # noqa: ANN001
    kam = await factories.make_user(db_session, roles=("kam",))
    _wf, stages = workflows["b2c"]
    initial = next(stage for stage in stages.values() if stage.is_initial)
    client = api.as_user(kam)

    response = await client.post("/api/v1/requests", json=form_payload(interaction_type.id))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["workflow_type"] == "b2c" and body["source"] == "manual"
    assert body["status"]["id"] == str(initial.id)
    assert body["client_kind"] == "person"
    assert body["interaction_type_id"] == str(interaction_type.id)
    assert body["client"]["full_name"] == PERSON["full_name"]
    assert body["client"]["email"] == PERSON["email"]
    assert body["client"]["phone"] == PERSON["phone"]
    assert body["assignee"]["id"] == str(kam.id)
    assert body.get("client_matched") is False

    (counterparty,) = await _counterparties(db_session)
    assert body["counterparty_id"] == str(counterparty.id)
    assert counterparty.kind == "person"
    assert counterparty.display_name == "Смирнова О.В."
    crypto = get_pii_crypto()
    # blind index: поиск по email без расшифровки, регистр email не важен
    assert counterparty.email_hmac == crypto.email_hmac("olga.smirnova@example.com")
    assert counterparty.full_name_hmac == crypto.full_name_hmac(PERSON["full_name"])
    # Fernet: в колонках шифртекст, открытых ПДн в БД нет
    raw = await _raw_pii(db_session, counterparty.id)
    assert raw and "Смирнова".encode() not in raw and b"Olga" not in raw and b"916" not in raw

    deal = await db_session.get(Deal, uuid.UUID(body["id"]))
    assert deal is not None and deal.counterparty_id == counterparty.id
    assert deal.interaction_type_id == interaction_type.id and deal.university_id is None

    # аудит: создание клиента — без открытых ПДн; создание заявки — со ссылкой на клиента
    (created_client,) = await _audit(db_session, "counterparty.created")
    assert created_client.actor_user_id == kam.id
    assert created_client.entity_type == "counterparty"
    assert created_client.entity_id == counterparty.id
    after = created_client.after or {}
    assert after["kind"] == "person" and after["source"] == "request_form"
    flat = repr(after)
    assert "Смирнова" not in flat and "Olga.Smirnova" not in flat and "000-11-22" not in flat
    (created_request,) = await _audit(db_session, "request.created")
    assert (created_request.after or {})["counterparty_id"] == str(counterparty.id)

    # заявка видна на доске: и в списке, которым её рисует BoardPage, и в канбан-агрегате
    listed = await client.get(
        "/api/v1/requests", params={"workflow_type": "b2c", "sort": "-updated_at"}
    )
    assert listed.status_code == 200
    assert body["id"] in {item["id"] for item in listed.json()["items"]}
    board = await client.get("/api/v1/requests/board", params={"workflow_type": "b2c"})
    assert board.status_code == 200
    column = next(c for c in board.json()["columns"] if c["status"]["id"] == str(initial.id))
    assert body["id"] in {card["id"] for card in column["cards"]}


async def test_form_payload_company_and_reuse_by_inn(
    api, db_session: AsyncSession, workflows, interaction_type
) -> None:  # noqa: ANN001
    kam = await factories.make_user(db_session, roles=("kam",))
    client = api.as_user(kam)

    first = await client.post(
        "/api/v1/requests", json=form_payload(interaction_type.id, kind="company")
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["client_kind"] == "company"
    assert body["client"]["company_name"] == COMPANY["company_name"]
    assert body["client"]["inn"] == COMPANY["inn"]
    assert body["client"]["email"] == COMPANY["email"]
    (counterparty,) = await _counterparties(db_session)
    assert counterparty.kind == "legal_entity" and counterparty.org_name == COMPANY["company_name"]
    assert counterparty.full_name is None and counterparty.email_hmac is None

    # та же организация по ИНН (название написали иначе) — тот же контрагент, без дубля
    again = await client.post(
        "/api/v1/requests",
        json=form_payload(
            interaction_type.id,
            kind="company",
            client={"company_name": "Пример-Обучение", "inn": COMPANY["inn"]},
            title="Пример — корпоративное обучение",
        ),
    )
    assert again.status_code == 201, again.text
    assert again.json()["counterparty_id"] == str(counterparty.id)
    assert again.json()["client_matched"] is True
    assert len(await _counterparties(db_session)) == 1
    assert len(await _audit(db_session, "counterparty.created")) == 1


async def test_form_reuses_person_by_email(
    api, db_session: AsyncSession, workflows, interaction_type
) -> None:  # noqa: ANN001
    kam = await factories.make_user(db_session, roles=("kam",))
    client = api.as_user(kam)
    first = await client.post("/api/v1/requests", json=form_payload(interaction_type.id))
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/requests",
        json=form_payload(
            interaction_type.id,
            client={"full_name": "Смирнова Ольга", "email": " olga.smirnova@EXAMPLE.com "},
            title="Смирнова — второй курс",
        ),
    )
    assert second.status_code == 201, second.text
    assert second.json()["counterparty_id"] == first.json()["counterparty_id"]
    assert second.json()["client_matched"] is True
    # карточка клиента не переписывается данными формы
    assert second.json()["client"]["full_name"] == PERSON["full_name"]
    assert len(await _counterparties(db_session)) == 1

    # без email искать не по чему — новый контрагент (форма email не требует)
    no_email = await client.post(
        "/api/v1/requests",
        json=form_payload(
            interaction_type.id,
            client={"full_name": "Петров Пётр", "phone": "+7 900 000-00-01"},
            title="Петров — курс",
        ),
    )
    assert no_email.status_code == 201, no_email.text
    assert no_email.json()["client"]["email"] is None
    assert no_email.json()["client_matched"] is False
    assert len(await _counterparties(db_session)) == 2


async def test_existing_counterparty_id_path_unchanged(
    api, db_session: AsyncSession, workflows, interaction_type
) -> None:  # noqa: ANN001
    kam = await factories.make_user(db_session, roles=("kam",))
    existing = await factories.make_counterparty(
        db_session, full_name="Клиентова Анна Петровна", email="anna@example.com"
    )
    response = await api.as_user(kam).post(
        "/api/v1/requests",
        json={
            "workflow_type": "b2c",
            "title": "Клиентова — курс",
            "counterparty_id": str(existing.id),
            "interaction_type_id": str(interaction_type.id),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["counterparty_id"] == str(existing.id)
    assert body["client"]["full_name"] == "Клиентова Анна Петровна"
    assert "client_matched" not in body
    assert [cp.id for cp in await _counterparties(db_session)] == [existing.id]
    assert await _audit(db_session, "counterparty.created") == []

    # B2B без изменений: вуз обязателен, клиент не нужен
    university = await factories.make_university(db_session, kam=kam)
    b2b = await api.as_user(kam).post(
        "/api/v1/requests",
        json={"workflow_type": "b2b", "title": "Стажировка", "university_id": str(university.id)},
    )
    assert b2b.status_code == 201, b2b.text
    assert b2b.json()["client"] is None and b2b.json()["counterparty_id"] is None


@pytest.mark.parametrize(
    ("patch", "field", "message"),
    [
        ({"client": None, "client_kind": None}, "", "обязателен клиент"),
        ({"client": {"email": "a@b.ru"}}, "", "ФИО клиента"),
        ({"client": {"full_name": "  "}}, "", "ФИО клиента"),
        ({"client_kind": "company", "client": {"inn": "7701234567"}}, "", "организации"),
        ({"client": {"full_name": "Иванов Иван", "email": "не-почта"}}, "client.email", "email"),
        (
            {"client_kind": "company", "client": {"company_name": "ООО «А»", "inn": "123"}},
            "client.inn",
            "ИНН",
        ),
    ],
)
async def test_form_validation_errors_are_readable(
    api, db_session: AsyncSession, workflows, interaction_type, patch, field, message
) -> None:  # noqa: ANN001
    kam = await factories.make_user(db_session, roles=("kam",))
    payload = {**form_payload(interaction_type.id), **patch}
    response = await api.as_user(kam).post("/api/v1/requests", json=payload)
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    (detail,) = error["details"]
    assert detail["field"] == field
    assert message in detail["message"]
    assert not detail["message"].startswith("Value error")
    assert await _counterparties(db_session) == []


async def test_client_and_counterparty_id_conflict(
    api, db_session: AsyncSession, workflows, interaction_type
) -> None:  # noqa: ANN001
    kam = await factories.make_user(db_session, roles=("kam",))
    existing = await factories.make_counterparty(db_session)
    both = await api.as_user(kam).post(
        "/api/v1/requests",
        json={**form_payload(interaction_type.id), "counterparty_id": str(existing.id)},
    )
    assert both.status_code == 400
    assert "либо" in both.json()["error"]["details"][0]["message"]

    university = await factories.make_university(db_session, kam=kam)
    b2b = await api.as_user(kam).post(
        "/api/v1/requests",
        json={
            **form_payload(interaction_type.id),
            "workflow_type": "b2b",
            "university_id": str(university.id),
        },
    )
    assert b2b.status_code == 400
    assert "B2C" in b2b.json()["error"]["details"][0]["message"]
    assert [cp.id for cp in await _counterparties(db_session)] == [existing.id]


# --------------------------------------------------------------------------------------
# Права
# --------------------------------------------------------------------------------------


async def test_form_rights(api, db_session: AsyncSession, workflows, interaction_type) -> None:  # noqa: ANN001
    """Процесс B2C вне роли и наблюдатель — 403, контрагент не создаётся; роль без
    `clients:pii` создаёт заявку, но найденного клиента видит маскированным."""
    await make_role(db_session, code="kam_b2b", workflow_types=("b2b",), name="КАМ B2B")
    await make_role(
        db_session,
        code="kam_b2c_nopii",
        permissions=KAM_PERMISSIONS - {"clients:pii"},
        workflow_types=("b2c",),
        name="КАМ B2C без ПДн",
    )
    reset_role_catalog()
    b2b_only = await factories.make_user(db_session, roles=("kam_b2b",))
    observer = await factories.make_user(db_session, roles=("observer",))
    no_pii = await factories.make_user(db_session, roles=("kam_b2c_nopii",))
    kam = await factories.make_user(db_session, roles=("kam",))

    denied = await api.as_user(b2b_only).post(
        "/api/v1/requests", json=form_payload(interaction_type.id)
    )
    assert denied.status_code == 403
    assert "B2C недоступно" in denied.json()["error"]["message"]
    watcher = await api.as_user(observer).post(
        "/api/v1/requests", json=form_payload(interaction_type.id)
    )
    assert watcher.status_code == 403
    assert await _counterparties(db_session) == []
    assert await _audit(db_session, "counterparty.created") == []

    # КАМ заводит клиента; коллега без clients:pii находит его по email — ПДн маскированы
    created = await api.as_user(kam).post(
        "/api/v1/requests", json=form_payload(interaction_type.id)
    )
    assert created.status_code == 201, created.text
    matched = await api.as_user(no_pii).post(
        "/api/v1/requests",
        json=form_payload(
            interaction_type.id,
            client={"full_name": "Кто-то", "email": PERSON["email"]},
            title="Проверка ПДн",
        ),
    )
    assert matched.status_code == 201, matched.text
    assert matched.json()["counterparty_id"] == created.json()["counterparty_id"]
    masked = matched.json()["client"]
    assert masked["full_name"] != PERSON["full_name"] and "***" in masked["full_name"]
    assert masked["email"] != PERSON["email"] and masked["phone"] != PERSON["phone"]

    # назначить другого ответственного без requests:assign_any — 403, клиент не создаётся
    other = await factories.make_user(db_session, roles=("kam",))
    foreign = await api.as_user(kam).post(
        "/api/v1/requests",
        json={
            **form_payload(
                interaction_type.id, client={"full_name": "Новый Клиент", "email": "n@ex.ru"}
            ),
            "assignee_id": str(other.id),
        },
    )
    assert foreign.status_code == 403
    assert len(await _counterparties(db_session)) == 1


# --------------------------------------------------------------------------------------
# Лид CMS: общий хелпер контрагента не меняет поведение интеграции
# --------------------------------------------------------------------------------------


async def test_cms_lead_counterparty_behaviour(db_session: AsyncSession, workflows) -> None:  # noqa: ANN001
    crypto = get_pii_crypto()
    person = {
        "client_kind": "person",
        "full_name": "Лидова Анна Сергеевна",
        "email": "Lead.Anna@example.com",
        "phone": "+7 900 111-22-33",
    }
    first, status = await integrations_service.handle_cms_lead(db_session, _lead_envelope(person))
    assert status == 201
    deal = await db_session.get(Deal, uuid.UUID(first["crm_request_id"]))
    assert deal is not None and deal.source == "cms"
    counterparty = await db_session.get(Counterparty, deal.counterparty_id)
    assert counterparty is not None and counterparty.kind == "person"
    assert counterparty.display_name == "Лидова А.С."
    assert counterparty.email_hmac == crypto.email_hmac(person["email"])
    assert counterparty.full_name_hmac == crypto.full_name_hmac(person["full_name"])
    assert counterparty.phone == person["phone"]
    raw = await _raw_pii(db_session, counterparty.id)
    assert "Лидова".encode() not in raw and b"Lead.Anna" not in raw

    # повтор лида с тем же email (регистр другой) — тот же контрагент
    second, _ = await integrations_service.handle_cms_lead(
        db_session, _lead_envelope({**person, "email": "lead.anna@EXAMPLE.com"})
    )
    deal2 = await db_session.get(Deal, uuid.UUID(second["crm_request_id"]))
    assert deal2 is not None and deal2.counterparty_id == counterparty.id

    # юрлицо: поиск по ИНН, без ИНН — по точному названию
    company = {"client_kind": "company", "company_name": "ООО «Лид»", "inn": "7709876543"}
    c1, _ = await integrations_service.handle_cms_lead(
        db_session, _lead_envelope({**company, "email": "lead@org.example"})
    )
    c2, _ = await integrations_service.handle_cms_lead(
        db_session, _lead_envelope({**company, "company_name": "Лид"})
    )
    org1 = (await db_session.get(Deal, uuid.UUID(c1["crm_request_id"]))).counterparty_id
    org2 = (await db_session.get(Deal, uuid.UUID(c2["crm_request_id"]))).counterparty_id
    assert org1 == org2
    org = await db_session.get(Counterparty, org1)
    assert org.kind == "legal_entity" and org.org_email == "lead@org.example"
    c3, _ = await integrations_service.handle_cms_lead(
        db_session, _lead_envelope({"client_kind": "company", "company_name": "ООО «Лид»"})
    )
    org3 = (await db_session.get(Deal, uuid.UUID(c3["crm_request_id"]))).counterparty_id
    assert org3 == org1
    # интеграция не пишет аудит создания клиента (как и раньше)
    assert await _audit(db_session, "counterparty.created") == []


@pytest.mark.parametrize(
    ("lead", "message"),
    [
        ({"client_kind": "person", "full_name": "Без Почты"}, "full_name и email"),
        ({"client_kind": "person", "email": "x@example.com"}, "full_name и email"),
        ({"client_kind": "company", "inn": "7709876543"}, "company_name"),
    ],
)
async def test_cms_lead_required_fields_unchanged(
    db_session: AsyncSession, workflows, lead, message
) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as caught:
        await integrations_service.handle_cms_lead(db_session, _lead_envelope(lead))
    assert caught.value.status_code == 422 and message in caught.value.message
    assert await _counterparties(db_session) == []
