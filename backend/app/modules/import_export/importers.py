"""Построчная валидация и применение импорта по `entity_type` (api-contract.md §6.4).

Покрыты: `universities`, `students`, `requests_b2b`, `requests_b2c`, `kam_catalog`
(таблица КАМов: вуз — ИТ-продукт — договор — лицензия — КАМ) и `dictionary:*`.
Дедупликация студентов/физлиц — по blind index `email_hmac` (data-model.md §7.1);
заявки создаются в initial-этапе своей воронки с историей `kind='import'`. Валидация
(dry-run) ничего не пишет; создание недостающих сущностей (`create_missing`)
происходит только на apply.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.pii import (
    PiiCrypto,
    display_name_from_full_name,
    mask_email,
    mask_full_name,
    normalize_email,
)
from app.models.crm import (
    CONTRACT_PRODUCT_TRANSFER_STATUSES,
    Contract,
    ContractProduct,
    Counterparty,
    InteractionType,
    ItDirection,
    Product,
    Program,
    University,
    UniversityContact,
)
from app.models.deal import Deal, DealStageHistory
from app.models.service import AppSetting
from app.models.talent import FederalProject, Student, StudentStatusHistory
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.crm.directions import link_product_directions, load_direction_lookup
from app.modules.import_export.mapping import (
    EntityFields,
    MappingEntry,
    entity_fields,
    normalize_phone,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

FUNNEL_STATUSES = ("candidate", "studying", "graduate", "talent_pool")

# встроенный словарь русских значений статуса воронки (поверх value_map пользователя)
FUNNEL_STATUS_RU: dict[str, str] = {
    "кандидат": "candidate",
    "учится": "studying",
    "обучается": "studying",
    "студент": "studying",
    "выпускник": "graduate",
    "закончил": "graduate",
    "кадровый резерв": "talent_pool",
    "резерв": "talent_pool",
    "talent pool": "talent_pool",
}

_CLIENT_KIND_RU: dict[str, str] = {
    "физлицо": "person",
    "физ лицо": "person",
    "физическое лицо": "person",
    "person": "person",
    "юрлицо": "legal_entity",
    "юр лицо": "legal_entity",
    "юридическое лицо": "legal_entity",
    "company": "legal_entity",
    "legal_entity": "legal_entity",
}


@dataclass
class RowIssue:
    row: int
    column: str | None
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "column": self.column,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class PreparedRow:
    row_number: int  # номер строки в исходном файле (1-based, с учётом заголовка)
    values: dict[str, str | None]
    issues: list[RowIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass
class ImportOptions:
    """Опции сессии (§6.3): стратегия, ключи дедупликации, дефолты."""

    update_strategy: str = "upsert"  # create_only | upsert | update_only
    match_by: tuple[str, ...] = ()
    skip_empty_rows: bool = True
    default_values: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None, spec: EntityFields) -> ImportOptions:
        raw = raw or {}
        strategy = raw.get("update_strategy") or "upsert"
        match_by = tuple(raw.get("match_by") or spec.default_match_by)
        return cls(
            update_strategy=strategy,
            match_by=match_by,
            skip_empty_rows=bool(raw.get("skip_empty_rows", True)),
            default_values=dict(raw.get("default_values") or {}),
        )


@dataclass
class ImportContext:
    session: AsyncSession
    spec: EntityFields
    options: ImportOptions
    entries: list[MappingEntry]
    actor: AppUser
    crypto: PiiCrypto
    import_session_id: uuid.UUID
    lookups: dict[str, dict[str, Any]] = field(default_factory=dict)

    def entry_for(self, target_field: str) -> MappingEntry | None:
        for entry in self.entries:
            if entry.target_field == target_field:
                return entry
        return None


@dataclass
class ApplyStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    # разбивка составного импорта по сущностям (kam_catalog): договоры, лицензии, КАМы…
    details: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _parse_amount(raw: str) -> Decimal:
    cleaned = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Сумма «{raw}» не является числом") from exc
    if value < 0:
        raise ValueError(f"Сумма «{raw}» не может быть отрицательной")
    return value


def _parse_date(raw: str) -> date:
    from app.modules.import_export.mapping import date_ru_to_iso

    return date.fromisoformat(date_ru_to_iso(raw))


def validate_email_format(raw: str) -> bool:
    return bool(_EMAIL_RE.match(raw.strip()))


def resolve_funnel_status(raw: str) -> str | None:
    value = raw.strip().lower().replace("ё", "е")
    if value in FUNNEL_STATUSES:
        return value
    return FUNNEL_STATUS_RU.get(value)


def find_file_duplicates(rows: list[tuple[int, str]], *, column: str) -> dict[int, RowIssue]:
    """Дубликаты внутри файла по нормализованному ключу: первая строка выигрывает.

    `rows` — (row_number, normalized_key); возвращает issues для строк-дублей.
    """
    seen: dict[str, int] = {}
    issues: dict[int, RowIssue] = {}
    for row_number, key in rows:
        if not key:
            continue
        if key in seen:
            issues[row_number] = RowIssue(
                row=row_number,
                column=column,
                code="duplicate_in_file",
                message=f"Дубликат по {column} со строкой {seen[key]}",
            )
        else:
            seen[key] = row_number
    return issues


# --------------------------------------------------------------------------------------
# Загрузка lookup-справочников (один раз на validate/apply)
# --------------------------------------------------------------------------------------


async def _load_lookups(ctx: ImportContext) -> None:
    if ctx.lookups:
        return
    session = ctx.session
    universities = (await session.execute(select(University))).scalars().all()
    ctx.lookups["university_by_name"] = {_norm(u.name): u for u in universities}
    ctx.lookups["university_by_inn"] = {u.inn: u for u in universities if u.inn}
    programs = (await session.execute(select(Program))).scalars().all()
    ctx.lookups["program_by_name"] = {_norm(p.name): p for p in programs}
    products = (await session.execute(select(Product))).scalars().all()
    ctx.lookups["product_by_code"] = {_norm(p.code): p for p in products}
    ctx.lookups["product_by_name"] = {_norm(p.name): p for p in products}
    projects = (await session.execute(select(FederalProject))).scalars().all()
    ctx.lookups["fedproject_by_name"] = {_norm(p.name): p for p in projects}
    ctx.lookups["direction_by_key"] = await load_direction_lookup(session)
    itypes = (await session.execute(select(InteractionType))).scalars().all()
    ctx.lookups["itype_by_code"] = {_norm(t.code): t for t in itypes}
    ctx.lookups["itype_by_name"] = {_norm(t.name): t for t in itypes}
    users = (
        (await session.execute(select(AppUser).where(AppUser.is_active.is_(True)))).scalars().all()
    )
    ctx.lookups["user_by_username"] = {_norm(u.username): u for u in users}
    ctx.lookups["user_by_email"] = {_norm(u.email): u for u in users if u.email}


def _lookup_university(ctx: ImportContext, raw: str) -> University | None:
    return ctx.lookups["university_by_inn"].get(raw.strip()) or ctx.lookups[
        "university_by_name"
    ].get(_norm(raw))


def _lookup_program(ctx: ImportContext, raw: str) -> Program | None:
    return ctx.lookups["program_by_name"].get(_norm(raw))


def _lookup_product(ctx: ImportContext, raw: str) -> Product | None:
    return ctx.lookups["product_by_code"].get(_norm(raw)) or ctx.lookups["product_by_name"].get(
        _norm(raw)
    )


def _lookup_direction(ctx: ImportContext, raw: str) -> ItDirection | None:
    """ИТ-направление по коду или названию без регистра (каталог it_direction)."""
    return ctx.lookups["direction_by_key"].get(_norm(raw))


def _check_direction(ctx: ImportContext, row: PreparedRow) -> None:
    raw = row.values.get("it_direction")
    if raw and _lookup_direction(ctx, raw) is None:
        _issue(
            row,
            "it_direction",
            "lookup_not_found",
            f"ИТ-направление «{raw}» не найдено в справочнике (поиск по коду и названию)",
        )


def _lookup_user(ctx: ImportContext, raw: str) -> AppUser | None:
    return ctx.lookups["user_by_username"].get(_norm(raw)) or ctx.lookups["user_by_email"].get(
        _norm(raw)
    )


def _issue(row: PreparedRow, column: str | None, code: str, message: str) -> None:
    row.issues.append(RowIssue(row=row.row_number, column=column, code=code, message=message))


def _check_reference(
    ctx: ImportContext,
    row: PreparedRow,
    *,
    target_field: str,
    found: bool,
    label: str,
) -> None:
    """lookup-мисс: ошибка, если у поля не включён `create_missing`."""
    entry = ctx.entry_for(target_field)
    if found or (entry is not None and entry.create_missing):
        return
    value = row.values.get(target_field)
    _issue(
        row,
        target_field,
        "lookup_not_found",
        f"{label} «{value}» не найден. Создайте запись или включите create_missing",
    )


# --------------------------------------------------------------------------------------
# Студенты (talent pool): дедуп по email_hmac
# --------------------------------------------------------------------------------------


async def _validate_students(ctx: ImportContext, rows: list[PreparedRow]) -> None:
    dup_issues = find_file_duplicates(
        [(r.row_number, normalize_email(r.values.get("email") or "")) for r in rows],
        column="email",
    )
    for row in rows:
        values = row.values
        if not (values.get("full_name") or "").strip():
            _issue(row, "full_name", "required", "Не заполнено обязательное поле «ФИО»")
        email = (values.get("email") or "").strip()
        if not email:
            _issue(row, "email", "required", "Не заполнено обязательное поле «Email»")
        elif not validate_email_format(email):
            # ПДн в тексте ошибки маскируются (data-model.md §8.5): сообщение
            # попадает в import_row_error.error и XLSX-отчёт открытым текстом
            _issue(row, "email", "invalid_format", f"Некорректный email: «{mask_email(email)}»")
        elif row.row_number in dup_issues:
            row.issues.append(dup_issues[row.row_number])
        if values.get("university"):
            _check_reference(
                ctx,
                row,
                target_field="university",
                found=_lookup_university(ctx, values["university"]) is not None,
                label="Вуз",
            )
        if values.get("program"):
            _check_reference(
                ctx,
                row,
                target_field="program",
                found=_lookup_program(ctx, values["program"]) is not None,
                label="Программа",
            )
        if values.get("federal_project"):
            _check_reference(
                ctx,
                row,
                target_field="federal_project",
                found=ctx.lookups["fedproject_by_name"].get(_norm(values["federal_project"]))
                is not None,
                label="Федеральный проект",
            )
        status_raw = values.get("funnel_status")
        if status_raw and resolve_funnel_status(status_raw) is None:
            _issue(
                row,
                "funnel_status",
                "invalid_value",
                f"Неизвестный статус воронки «{status_raw}». "
                f"Допустимо: {', '.join(FUNNEL_STATUSES)}",
            )


async def _ensure_federal_project(ctx: ImportContext, name: str) -> FederalProject:
    key = _norm(name)
    project = ctx.lookups["fedproject_by_name"].get(key)
    if project is None:
        project = FederalProject(name=name.strip())
        ctx.session.add(project)
        await ctx.session.flush()
        ctx.lookups["fedproject_by_name"][key] = project
    return project


async def _ensure_university(ctx: ImportContext, raw: str) -> University:
    university = _lookup_university(ctx, raw)
    if university is None:
        university = University(name=raw.strip())
        ctx.session.add(university)
        await ctx.session.flush()
        ctx.lookups["university_by_name"][_norm(raw)] = university
    return university


async def _ensure_program(ctx: ImportContext, raw: str) -> Program:
    program = _lookup_program(ctx, raw)
    if program is None:
        program = Program(name=raw.strip())
        ctx.session.add(program)
        await ctx.session.flush()
        ctx.lookups["program_by_name"][_norm(raw)] = program
    return program


async def _student_refs(
    ctx: ImportContext, values: dict[str, str | None]
) -> dict[str, uuid.UUID | None]:
    refs: dict[str, uuid.UUID | None] = {
        "university_id": None,
        "program_id": None,
        "federal_project_id": None,
    }
    if values.get("university"):
        refs["university_id"] = (await _ensure_university(ctx, values["university"])).id
    if values.get("program"):
        program = await _ensure_program(ctx, values["program"])
        refs["program_id"] = program.id
    if values.get("federal_project"):
        refs["federal_project_id"] = (
            await _ensure_federal_project(ctx, values["federal_project"])
        ).id
    return refs


# лимит asyncpg — 32767 bind-параметров на запрос; lookup по hmac идёт чанками
_HMAC_LOOKUP_CHUNK = 10_000


async def _apply_students(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    stats = ApplyStats()
    hmacs = [ctx.crypto.email_hmac(r.values["email"] or "") for r in rows]
    existing: dict[str, Student] = {}
    unique_hmacs = list(dict.fromkeys(hmacs))
    for start in range(0, len(unique_hmacs), _HMAC_LOOKUP_CHUNK):
        chunk = unique_hmacs[start : start + _HMAC_LOOKUP_CHUNK]
        found = (
            (await ctx.session.execute(select(Student).where(Student.email_hmac.in_(chunk))))
            .scalars()
            .all()
        )
        existing.update({s.email_hmac: s for s in found})
    for row, email_hmac in zip(rows, hmacs, strict=True):
        values = row.values
        student = existing.get(email_hmac)
        status = resolve_funnel_status(values.get("funnel_status") or "") or "candidate"
        if student is not None:
            if ctx.options.update_strategy == "create_only":
                stats.skipped += 1
                continue
            refs = await _student_refs(ctx, values)
            full_name = (values["full_name"] or "").strip()
            student.full_name = full_name
            student.full_name_hmac = ctx.crypto.full_name_hmac(full_name)
            student.display_name = display_name_from_full_name(full_name)
            if values.get("phone"):
                student.phone = values["phone"]
            if values.get("notes"):
                student.notes = values["notes"]
            for attr, value in refs.items():
                if value is not None:
                    setattr(student, attr, value)
            if values.get("funnel_status") and student.funnel_status != status:
                ctx.session.add(
                    StudentStatusHistory(
                        student_id=student.id,
                        from_status=student.funnel_status,
                        to_status=status,
                        actor_user_id=ctx.actor.id,
                        reason="import",
                    )
                )
                student.funnel_status = status
            stats.updated += 1
            continue
        if ctx.options.update_strategy == "update_only":
            stats.skipped += 1
            continue
        refs = await _student_refs(ctx, values)
        full_name = (values["full_name"] or "").strip()
        student = Student(
            full_name=full_name,
            email=(values["email"] or "").strip(),
            phone=values.get("phone"),
            email_hmac=email_hmac,
            full_name_hmac=ctx.crypto.full_name_hmac(full_name),
            display_name=display_name_from_full_name(full_name),
            funnel_status=status,
            source="import",
            import_session_id=ctx.import_session_id,
            notes=values.get("notes"),
            created_by=ctx.actor.id,
            **refs,
        )
        ctx.session.add(student)
        await ctx.session.flush()
        ctx.session.add(
            StudentStatusHistory(
                student_id=student.id,
                from_status=None,
                to_status=status,
                actor_user_id=ctx.actor.id,
                reason="import",
            )
        )
        existing[email_hmac] = student
        stats.created += 1
    return stats


# --------------------------------------------------------------------------------------
# Вузы (и dictionary:universities_registry)
# --------------------------------------------------------------------------------------


async def _validate_universities(ctx: ImportContext, rows: list[PreparedRow]) -> None:
    dup_issues = find_file_duplicates(
        [(r.row_number, _norm(r.values.get("name"))) for r in rows], column="name"
    )
    for row in rows:
        if not (row.values.get("name") or "").strip():
            _issue(row, "name", "required", "Не заполнено обязательное поле «Название»")
        elif row.row_number in dup_issues:
            row.issues.append(dup_issues[row.row_number])
        inn = (row.values.get("inn") or "").strip()
        if inn and not re.fullmatch(r"\d{10}|\d{12}", inn):
            _issue(row, "inn", "invalid_format", f"ИНН «{inn}» должен содержать 10 или 12 цифр")


async def _apply_universities(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    stats = ApplyStats()
    attrs = ("short_name", "inn", "kpp", "region", "city", "website", "notes")
    for row in rows:
        values = row.values
        name = (values["name"] or "").strip()
        inn = (values.get("inn") or "").strip() or None
        university = (inn and ctx.lookups["university_by_inn"].get(inn)) or ctx.lookups[
            "university_by_name"
        ].get(_norm(name))
        if university is not None:
            if ctx.options.update_strategy == "create_only":
                stats.skipped += 1
                continue
            university.name = name
            for attr in attrs:
                value = (values.get(attr) or "").strip() or None
                if value is not None:
                    setattr(university, attr, value)
            stats.updated += 1
        else:
            if ctx.options.update_strategy == "update_only":
                stats.skipped += 1
                continue
            university = University(
                name=name,
                **{attr: (values.get(attr) or "").strip() or None for attr in attrs},
            )
            ctx.session.add(university)
            await ctx.session.flush()
            stats.created += 1
        ctx.lookups["university_by_name"][_norm(name)] = university
        if university.inn:
            ctx.lookups["university_by_inn"][university.inn] = university
    return stats


# --------------------------------------------------------------------------------------
# Заявки B2B / B2C: создаются в initial-этапе своей воронки
# --------------------------------------------------------------------------------------


async def _initial_stage(ctx: ImportContext, pipeline: str) -> tuple[Workflow, WorkflowStage]:
    cache_lookup_key = f"workflow:{pipeline}"
    cached = ctx.lookups.get(cache_lookup_key)
    if cached:
        return cached["workflow"], cached["stage"]
    workflow = (
        await ctx.session.execute(select(Workflow).where(Workflow.code == pipeline))
    ).scalar_one_or_none()
    stage = None
    if workflow is not None:
        stage = (
            await ctx.session.execute(
                select(WorkflowStage).where(
                    WorkflowStage.workflow_id == workflow.id,
                    WorkflowStage.is_initial.is_(True),
                    WorkflowStage.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    if workflow is None or stage is None:
        from app.core.errors import unprocessable

        raise unprocessable(f"Воронка {pipeline} не сконфигурирована — импорт заявок невозможен")
    ctx.lookups[cache_lookup_key] = {"workflow": workflow, "stage": stage}
    return workflow, stage


def _resolve_amount(row: PreparedRow) -> Decimal | None:
    raw = row.values.get("amount")
    if not raw:
        return None
    try:
        return _parse_amount(raw)
    except ValueError:
        return None  # ошибка уже зафиксирована на этапе валидации


async def _validate_requests_b2b(ctx: ImportContext, rows: list[PreparedRow]) -> None:
    for row in rows:
        values = row.values
        university_raw = (values.get("university") or "").strip()
        if not university_raw:
            _issue(row, "university", "required", "Не заполнено обязательное поле «Вуз»")
        else:
            _check_reference(
                ctx,
                row,
                target_field="university",
                found=_lookup_university(ctx, university_raw) is not None,
                label="Вуз",
            )
        if values.get("product") and _lookup_product(ctx, values["product"]) is None:
            _issue(
                row,
                "product",
                "lookup_not_found",
                f"Продукт «{values['product']}» не найден (поиск по коду и названию)",
            )
        if values.get("program"):
            _check_reference(
                ctx,
                row,
                target_field="program",
                found=_lookup_program(ctx, values["program"]) is not None,
                label="Программа",
            )
        if values.get("assignee") and _lookup_user(ctx, values["assignee"]) is None:
            _issue(
                row,
                "assignee",
                "lookup_not_found",
                f"Пользователь «{values['assignee']}» не найден",
            )
        _check_direction(ctx, row)
        if values.get("amount"):
            try:
                _parse_amount(values["amount"])
            except ValueError as exc:
                _issue(row, "amount", "invalid_format", str(exc))


async def _create_deal_row(
    ctx: ImportContext,
    row: PreparedRow,
    *,
    pipeline: str,
    title: str,
    university_id: uuid.UUID | None,
    counterparty_id: uuid.UUID | None,
    interaction_type_id: uuid.UUID | None = None,
) -> None:
    workflow, stage = await _initial_stage(ctx, pipeline)
    values = row.values
    product = _lookup_product(ctx, values["product"]) if values.get("product") else None
    program = None
    if values.get("program"):
        entry = ctx.entry_for("program")
        program = (
            await _ensure_program(ctx, values["program"])
            if entry is not None and entry.create_missing
            else _lookup_program(ctx, values["program"])
        )
    assignee = _lookup_user(ctx, values["assignee"]) if values.get("assignee") else None
    deal = Deal(
        pipeline=pipeline,
        workflow_id=workflow.id,
        stage_id=stage.id,
        title=title,
        university_id=university_id,
        counterparty_id=counterparty_id,
        interaction_type_id=interaction_type_id,
        product_id=product.id if product else (program.product_id if program else None),
        program_id=program.id if program else None,
        responsible_user_id=(assignee or ctx.actor).id,
        amount=_resolve_amount(row),
        source="import",
        description=values.get("description"),
        created_by=ctx.actor.id,
    )
    ctx.session.add(deal)
    await ctx.session.flush()
    # «ИТ-направление» строки привязывается к продукту заявки (M:N, только добавление)
    raw_direction = values.get("it_direction")
    direction = _lookup_direction(ctx, raw_direction) if raw_direction else None
    if direction is not None and deal.product_id is not None:
        await link_product_directions(ctx.session, deal.product_id, [direction.id])
    ctx.session.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=None,
            to_stage_id=stage.id,
            from_stage_name=None,
            to_stage_name=stage.name,
            actor_user_id=ctx.actor.id,
            kind="import",
            reason="import",
        )
    )


async def _apply_requests_b2b(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    stats = ApplyStats()
    for row in rows:
        values = row.values
        university = await _ensure_university(ctx, values["university"] or "")
        title = (values.get("title") or "").strip() or (
            f"{university.short_name or university.name}"
            + (f" — {values['product']}" if values.get("product") else "")
        )
        await _create_deal_row(
            ctx,
            row,
            pipeline="b2b",
            title=title,
            university_id=university.id,
            counterparty_id=None,
        )
        stats.created += 1
    return stats


def _resolve_client_kind(raw: str | None) -> str | None:
    if not raw:
        return "person"
    return _CLIENT_KIND_RU.get(raw.strip().lower().replace("ё", "е"))


async def _validate_requests_b2c(ctx: ImportContext, rows: list[PreparedRow]) -> None:
    for row in rows:
        values = row.values
        kind = _resolve_client_kind(values.get("client_kind"))
        if kind is None:
            _issue(
                row,
                "client_kind",
                "invalid_value",
                f"Неизвестный тип клиента «{values.get('client_kind')}» "
                "(допустимо: физлицо, юрлицо)",
            )
            continue
        email = (values.get("email") or "").strip()
        if not email:
            _issue(row, "email", "required", "Не заполнено обязательное поле «Email»")
        elif not validate_email_format(email):
            _issue(row, "email", "invalid_format", f"Некорректный email: «{mask_email(email)}»")
        if kind == "person" and not (values.get("full_name") or "").strip():
            _issue(row, "full_name", "required", "Для физлица обязательно поле «ФИО»")
        if kind == "legal_entity" and not (values.get("company_name") or "").strip():
            _issue(
                row,
                "company_name",
                "required",
                "Для юрлица обязательно поле «Название организации»",
            )
        itype_raw = (values.get("interaction_type") or "").strip()
        if itype_raw and not (
            ctx.lookups["itype_by_code"].get(_norm(itype_raw))
            or ctx.lookups["itype_by_name"].get(_norm(itype_raw))
        ):
            _issue(
                row,
                "interaction_type",
                "lookup_not_found",
                f"Тип взаимодействия «{itype_raw}» не найден",
            )
        if values.get("product") and _lookup_product(ctx, values["product"]) is None:
            _issue(
                row,
                "product",
                "lookup_not_found",
                f"Продукт «{values['product']}» не найден (поиск по коду и названию)",
            )
        if values.get("program"):
            _check_reference(
                ctx,
                row,
                target_field="program",
                found=_lookup_program(ctx, values["program"]) is not None,
                label="Программа",
            )
        _check_direction(ctx, row)
        if values.get("amount"):
            try:
                _parse_amount(values["amount"])
            except ValueError as exc:
                _issue(row, "amount", "invalid_format", str(exc))


async def _find_or_create_counterparty_row(
    ctx: ImportContext, values: dict[str, str | None]
) -> Counterparty:
    kind = _resolve_client_kind(values.get("client_kind")) or "person"
    if kind == "person":
        email = (values["email"] or "").strip()
        email_hmac = ctx.crypto.email_hmac(email)
        existing = (
            await ctx.session.execute(
                select(Counterparty).where(Counterparty.email_hmac == email_hmac)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        full_name = (values["full_name"] or "").strip()
        counterparty = Counterparty(
            kind="person",
            display_name=display_name_from_full_name(full_name),
            full_name=full_name,
            email=email,
            phone=values.get("phone"),
            email_hmac=email_hmac,
            full_name_hmac=ctx.crypto.full_name_hmac(full_name),
        )
        ctx.session.add(counterparty)
        await ctx.session.flush()
        return counterparty
    company = (values["company_name"] or "").strip()
    inn = (values.get("inn") or "").strip() or None
    stmt = select(Counterparty).where(Counterparty.kind == "legal_entity")
    stmt = (
        stmt.where(Counterparty.inn == inn)
        if inn
        else stmt.where(func.lower(Counterparty.org_name) == company.lower())
    )
    existing = (await ctx.session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing
    counterparty = Counterparty(
        kind="legal_entity",
        display_name=company,
        org_name=company,
        inn=inn,
        org_email=(values.get("email") or "").strip() or None,
        org_phone=values.get("phone"),
    )
    ctx.session.add(counterparty)
    await ctx.session.flush()
    return counterparty


async def _apply_requests_b2c(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    stats = ApplyStats()
    for row in rows:
        values = row.values
        counterparty = await _find_or_create_counterparty_row(ctx, values)
        itype_raw = (values.get("interaction_type") or "").strip()
        itype = (
            ctx.lookups["itype_by_code"].get(_norm(itype_raw))
            or ctx.lookups["itype_by_name"].get(_norm(itype_raw))
            if itype_raw
            else None
        )
        subject = values.get("program") or values.get("product") or "импорт"
        title = (values.get("title") or "").strip() or (f"{counterparty.display_name} — {subject}")
        await _create_deal_row(
            ctx,
            row,
            pipeline="b2c",
            title=title,
            university_id=None,
            counterparty_id=counterparty.id,
            interaction_type_id=itype.id if itype else None,
        )
        stats.created += 1
    return stats


# --------------------------------------------------------------------------------------
# Справочники админки (dictionary:*)
# --------------------------------------------------------------------------------------


async def _validate_dictionary(ctx: ImportContext, rows: list[PreparedRow]) -> None:
    name = ctx.spec.entity_type.split(":", 1)[1]
    key_field = "code" if name == "interaction_types" else "name"
    dup_issues = find_file_duplicates(
        [(r.row_number, _norm(r.values.get(key_field))) for r in rows], column=key_field
    )
    for row in rows:
        for required in ctx.spec.required_fields():
            if not (row.values.get(required) or "").strip():
                label = ctx.spec.by_name()[required].label
                _issue(row, required, "required", f"Не заполнено обязательное поле «{label}»")
        if row.row_number in dup_issues:
            row.issues.append(dup_issues[row.row_number])
        for date_field in ("starts_on", "ends_on"):
            raw = row.values.get(date_field)
            if raw:
                try:
                    _parse_date(raw)
                except ValueError as exc:
                    _issue(row, date_field, "invalid_format", str(exc))


async def _apply_dictionary(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    name = ctx.spec.entity_type.split(":", 1)[1]
    if name == "federal_projects":
        return await _apply_federal_projects(ctx, rows)
    if name == "interaction_types":
        return await _apply_interaction_types(ctx, rows)
    if name == "universities_registry":
        return await _apply_universities(ctx, rows)
    if name == "products":
        return await _apply_products(ctx, rows)
    return await _apply_value_list(ctx, rows, name)


# кавычки, в которых организаторы оформляют названия продуктов: «Базис Dynamix»
_PRODUCT_QUOTE_CHARS = "«»\"'“”"


def split_product_names(raw: str) -> list[str]:
    """Ячейка «Продукт» может содержать несколько продуктов через запятую.

    `«RT.DataLake», «RT.Warehouse»` → ["RT.DataLake", "RT.Warehouse"]; кавычки
    снимаются, пустые и повторяющиеся значения отбрасываются.
    """
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().strip(_PRODUCT_QUOTE_CHARS).strip()
        if name and _norm(name) not in {_norm(n) for n in names}:
            names.append(name)
    return names


def product_code_from_name(name: str) -> str:
    """Код продукта из названия: `RT.DataLake` → `rt_datalake` (code NOT NULL в DDL)."""
    slug = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", "_", name).strip("_").lower()
    return slug or _norm(name)


async def _apply_products(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    """Справочник продуктов из «Вендоры.xlsx»: дедуп по названию (upsert).

    У модели Product нет полей vendor/attrs — вендор сохраняется в текстовое
    поле description с префиксом «Вендор: ». Контакты (ПДн) не сохраняются.
    """
    stats = ApplyStats()
    for row in rows:
        vendor = (row.values.get("vendor") or "").strip()
        description = f"Вендор: {vendor}" if vendor else None
        for name in split_product_names(row.values.get("name") or ""):
            product = _lookup_product(ctx, name)
            if product is not None:
                if ctx.options.update_strategy == "create_only":
                    stats.skipped += 1
                    continue
                product.name = name
                if description:
                    product.description = description
                stats.updated += 1
            else:
                if ctx.options.update_strategy == "update_only":
                    stats.skipped += 1
                    continue
                product = Product(
                    code=product_code_from_name(name),
                    name=name,
                    product_type="other",
                    description=description,
                )
                ctx.session.add(product)
                await ctx.session.flush()
                stats.created += 1
            ctx.lookups["product_by_code"][_norm(product.code)] = product
            ctx.lookups["product_by_name"][_norm(name)] = product
    return stats


async def _apply_federal_projects(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    stats = ApplyStats()
    for row in rows:
        values = row.values
        name = (values["name"] or "").strip()
        project = ctx.lookups["fedproject_by_name"].get(_norm(name))
        starts_on = _parse_date(values["starts_on"]) if values.get("starts_on") else None
        ends_on = _parse_date(values["ends_on"]) if values.get("ends_on") else None
        if project is not None:
            if ctx.options.update_strategy == "create_only":
                stats.skipped += 1
                continue
            project.code = (values.get("code") or "").strip() or project.code
            project.description = values.get("description") or project.description
            project.starts_on = starts_on or project.starts_on
            project.ends_on = ends_on or project.ends_on
            stats.updated += 1
            continue
        if ctx.options.update_strategy == "update_only":
            stats.skipped += 1
            continue
        project = FederalProject(
            name=name,
            code=(values.get("code") or "").strip() or None,
            description=values.get("description"),
            starts_on=starts_on,
            ends_on=ends_on,
        )
        ctx.session.add(project)
        await ctx.session.flush()
        ctx.lookups["fedproject_by_name"][_norm(name)] = project
        stats.created += 1
    return stats


async def _apply_interaction_types(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    stats = ApplyStats()
    for row in rows:
        values = row.values
        code = (values["code"] or "").strip()
        itype = ctx.lookups["itype_by_code"].get(_norm(code))
        if itype is not None:
            if ctx.options.update_strategy == "create_only":
                stats.skipped += 1
                continue
            itype.name = (values["name"] or "").strip()
            itype.description = values.get("description") or itype.description
            stats.updated += 1
            continue
        if ctx.options.update_strategy == "update_only":
            stats.skipped += 1
            continue
        itype = InteractionType(
            pipeline="b2c",
            code=code,
            name=(values["name"] or "").strip(),
            description=values.get("description"),
            created_by=ctx.actor.id,
        )
        ctx.session.add(itype)
        await ctx.session.flush()
        ctx.lookups["itype_by_code"][_norm(code)] = itype
        stats.created += 1
    return stats


async def _apply_value_list(ctx: ImportContext, rows: list[PreparedRow], name: str) -> ApplyStats:
    """Справочники-списки без таблиц (регионы, источники, теги…) — app_setting['dict:*'].

    Резолюция: DDL data-model.md не содержит универсальной таблицы справочников,
    а менять схему запрещено; jsonb-значение `app_setting` покрывает потребность
    (admin-only запись, чтение — селекты UI).
    """
    stats = ApplyStats()
    key = f"dict:{name}"
    setting = await ctx.session.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value={"items": []}, updated_by=ctx.actor.id)
        ctx.session.add(setting)
    items: list[dict[str, Any]] = list((setting.value or {}).get("items") or [])
    index = {_norm(item.get("name")): item for item in items}
    for row in rows:
        item_name = (row.values["name"] or "").strip()
        existing = index.get(_norm(item_name))
        code = (row.values.get("code") or "").strip() or None
        if existing is not None:
            if ctx.options.update_strategy == "create_only":
                stats.skipped += 1
                continue
            if code:
                existing["code"] = code
            stats.updated += 1
            continue
        if ctx.options.update_strategy == "update_only":
            stats.skipped += 1
            continue
        item = {"id": str(uuid.uuid4()), "name": item_name, "code": code}
        items.append(item)
        index[_norm(item_name)] = item
        stats.created += 1
    setting.value = {"items": items}
    setting.updated_by = ctx.actor.id
    return stats


# --------------------------------------------------------------------------------------
# Таблица КАМов (kam_catalog): вуз — ИТ-продукт — договор — лицензия — КАМ
# --------------------------------------------------------------------------------------
#
# Строка файла — лицензия на ИТ-продукт по договору с вузом. Ключ upsert — номер договора
# (уникален без регистра, uq_contract_number); в одном договоре несколько продуктов —
# несколько строк файла (или несколько продуктов через запятую в одной ячейке). Права —
# как у карточек: КАМ без «Назначения ответственного КАМа за вуз» не меняет КАМа вуза
# (новый вуз — только на себя), без «Правки чужих вузов и договоров» не трогает чужие
# договоры и контакты чужих вузов, без «Создания и правки продуктов» не создаёт продукты
# и не меняет их вендора. Нарушение — построчная ошибка, а не отказ всего файла.

TRANSFER_STATUS_LABELS: dict[str, str] = {
    "not_started": "Не передана",
    "in_progress": "В процессе передачи",
    "transferred": "Передана",
    "cancelled": "Отменена",
}

# русские формулировки статуса передачи лицензии (поверх value_map пользователя)
_TRANSFER_STATUS_RU: dict[str, str] = {
    "не передана": "not_started",
    "не передано": "not_started",
    "не передан": "not_started",
    "не начата": "not_started",
    "ожидает": "not_started",
    "ожидает передачи": "not_started",
    "нет": "not_started",
    "в процессе": "in_progress",
    "в процессе передачи": "in_progress",
    "в работе": "in_progress",
    "передается": "in_progress",
    "на передаче": "in_progress",
    "частично": "in_progress",
    "частично передана": "in_progress",
    "передана": "transferred",
    "передано": "transferred",
    "передан": "transferred",
    "передана вузу": "transferred",
    "выдана": "transferred",
    "активирована": "transferred",
    "да": "transferred",
    "отменена": "cancelled",
    "отменено": "cancelled",
    "отмена": "cancelled",
    "отказ": "cancelled",
    "отозвана": "cancelled",
    "аннулирована": "cancelled",
}

_STATUS_TRIM_RE = re.compile(r"[\s.!,;:]+")
_NAME_SPLIT_RE = re.compile(r"[\s.,]+")
_INN_RE = re.compile(r"\d{10}|\d{12}")
_DATE_DMY_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?:\s*г(?:ода|\.)?)?$")
_TERM_YEARS_RE = re.compile(
    r"^(?:на\s+)?(\d+(?:[.,]\d+)?)\s*(?:г|год|года|лет|years?|y)?\.?$", re.IGNORECASE
)
_TERM_UNTIL_YEAR_RE = re.compile(r"^(?:до\s+)?(\d{4})(?:\s*(?:г|год|года))?\.?$")
_CONTACT_SPLIT_RE = re.compile(r"[;\n]+")
_CONTACT_EMAIL_RE = re.compile(r"[^\s@,;()<>]+@[^\s@,;()<>]+\.[^\s@,;()<>]+")
_CONTACT_PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")
_CONTACT_POSITION_RE = re.compile(r"\(([^)]*)\)")
_LIST_SPLIT_RE = re.compile(r"[,;\n/]+")
_VENDOR_PREFIX = "Вендор:"

MAX_LICENSE_TERM_YEARS = 99  # = CHECK ck_contract_product_license_term
_MIN_LICENSE_YEAR = 1990
_MAX_LICENSE_YEAR = 2100


def resolve_transfer_status(raw: str) -> str | None:
    """«Передана», «в процессе передачи», «отказ» или код → код статуса передачи."""
    value = _STATUS_TRIM_RE.sub(" ", raw.strip().lower().replace("ё", "е")).strip()
    if value in CONTRACT_PRODUCT_TRANSFER_STATUSES:  # коды CHECK ck_contract_product_…
        return value
    for code, label in TRANSFER_STATUS_LABELS.items():
        if value == label.lower():
            return code
    return _TRANSFER_STATUS_RU.get(value)


def _name_tokens(raw: str) -> list[str]:
    return [t for t in _NAME_SPLIT_RE.split(raw.strip().lower().replace("ё", "е")) if t]


def person_name_keys(full_name: str) -> set[str]:
    """Ключи поиска сотрудника по ФИО из таблицы КАМа (`app_user.full_name` — «Ф И О»).

    «Петров Константин Андреевич» → полное ФИО, «петров константин», «константин петров»,
    «петров к», «петров к а». Одна фамилия — не ключ: слишком неоднозначно.
    """
    tokens = _name_tokens(full_name)
    if len(tokens) < 2:
        return set()
    last, first = tokens[0], tokens[1]
    keys = {" ".join(tokens), f"{last} {first}", f"{first} {last}", f"{last} {first[0]}"}
    if len(tokens) > 2:
        keys.add(f"{last} {first[0]} {tokens[2][0]}")
    return keys


def add_years(start: date, years: int) -> date:
    """Та же дата через N лет; 29 февраля в невисокосный год → 28 февраля."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, day=28)


def license_end(signed_at: date, years: int) -> date:
    """Последний день действия лицензии на N лет: 01.02.2026 + 1 год → 31.01.2027."""
    return add_years(signed_at, years) - timedelta(days=1)


def parse_license_date(raw: str) -> date:
    """Дата из ячейки: дд.мм.гггг, д.м.гг, дд/мм/гггг, ISO (Excel-даты парсер уже
    привёл к ISO); «г.» в конце допускается."""
    value = raw.strip().lower()
    match = _DATE_DMY_RE.match(value)
    try:
        if match:
            day, month, year = (int(g) for g in match.groups())
            parsed = date(year + 2000 if year < 100 else year, month, day)
        else:
            parsed = date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"Дата «{raw}» не распознана: ожидается дд.мм.гггг") from exc
    if not _MIN_LICENSE_YEAR <= parsed.year <= _MAX_LICENSE_YEAR:
        raise ValueError(f"Дата «{raw}» вне допустимого диапазона")
    return parsed


@dataclass(frozen=True)
class LicenseTerm:
    """Срок лицензии из ячейки: число лет, явная дата окончания или «бессрочно»."""

    years: int | None = None
    valid_to: date | None = None
    perpetual: bool = False


def parse_license_term(raw: str) -> LicenseTerm:
    """«1», «3 года», «5 лет» → лет; «2027», «до 2027 г.» → до 31.12.2027;
    «31.12.2027», «до 31.12.2027» → до даты; «бессрочно» → без срока."""
    value = raw.strip().lower().replace("ё", "е")
    if value.startswith("бессроч"):
        return LicenseTerm(perpetual=True)
    until_year = _TERM_UNTIL_YEAR_RE.match(value)
    if until_year:
        year = int(until_year.group(1))
        if _MIN_LICENSE_YEAR <= year <= _MAX_LICENSE_YEAR:
            return LicenseTerm(valid_to=date(year, 12, 31))
    years_match = _TERM_YEARS_RE.match(value)
    if years_match:
        number = Decimal(years_match.group(1).replace(",", "."))
        if number != number.to_integral_value() or not 1 <= number <= MAX_LICENSE_TERM_YEARS:
            raise ValueError(
                f"Срок лицензии «{raw}»: укажите целое число лет от 1 до {MAX_LICENSE_TERM_YEARS}"
            )
        return LicenseTerm(years=int(number))
    try:
        return LicenseTerm(valid_to=parse_license_date(value.removeprefix("до").strip()))
    except ValueError:
        pass
    raise ValueError(
        f"Срок лицензии «{raw}» не распознан: укажите число лет (1, 3), "
        "год окончания (2027) или дату окончания (31.12.2027)"
    )


def merge_license(
    current: tuple[date | None, int | None, date | None],
    signed_at: date | None,
    term: LicenseTerm | None,
) -> tuple[date | None, int | None, date | None]:
    """Итоговые (подписание, лет, окончание) строки договора после строки файла.

    Пустые ячейки не стирают сохранённое. Новая дата подписания при известном сроке в
    годах пересчитывает окончание; «бессрочно» снимает срок. Окончание раньше
    подписания — ValueError (это же проверяет CHECK ck_contract_product_license_dates).
    """
    current_signed, years, valid_to = current
    signed = signed_at or current_signed
    if term is not None:
        years, valid_to = term.years, term.valid_to
    if years is not None and signed is not None and (term is not None or signed_at is not None):
        valid_to = license_end(signed, years)
    elif term is None and signed_at is not None and valid_to is not None and valid_to < signed:
        # переподписание позже прежней явной даты окончания: старая дата устарела
        valid_to = None
    if valid_to is not None and signed is not None and valid_to < signed:
        raise ValueError(
            f"Окончание лицензии {valid_to:%d.%m.%Y} раньше её подписания {signed:%d.%m.%Y}"
        )
    return signed, years, valid_to


@dataclass(frozen=True)
class ContactInfo:
    full_name: str
    position: str | None = None
    email: str | None = None
    phone: str | None = None


def parse_contacts(raw: str) -> list[ContactInfo]:
    """«Ответственные от вуза» → контакты; люди разделяются «;» или переносом строки.

    «Иванова Анна Сергеевна (проректор), +7 916 000-11-22, ivanova@vuz.ru» → ФИО,
    должность (в скобках или после запятой), телефон (нормализован), email. Фрагмент
    без ФИО (только телефон или почта) пропускается — его не к кому привязать.
    """
    contacts: list[ContactInfo] = []
    seen: set[str] = set()
    for chunk in _CONTACT_SPLIT_RE.split(raw):
        text = chunk.strip()
        if not text:
            continue
        email_match = _CONTACT_EMAIL_RE.search(text)
        email = email_match.group(0) if email_match else None
        if email_match:
            text = text.replace(email_match.group(0), " ")
        phone: str | None = None
        for phone_match in _CONTACT_PHONE_RE.finditer(text):
            digits = re.sub(r"\D", "", phone_match.group(0))
            if 10 <= len(digits) <= 15:
                phone = normalize_phone(phone_match.group(0))
                text = text.replace(phone_match.group(0), " ")
                break
        position: str | None = None
        position_match = _CONTACT_POSITION_RE.search(text)
        if position_match:
            position = position_match.group(1).strip() or None
            text = text.replace(position_match.group(0), " ")
        name, _, rest = text.partition(",")
        if position is None:
            position = rest.strip(" ,-–—") or None
        for dash in (" — ", " – ", " - "):
            if dash in name:
                name, _, tail = name.partition(dash)
                position = position or tail.strip(" ,-–—") or None
        name = re.sub(r"\s+", " ", name).strip(" ,-–—:")
        key = " ".join(_name_tokens(name))
        if not name or not any(ch.isalpha() for ch in name) or key in seen:
            continue
        seen.add(key)
        contacts.append(ContactInfo(full_name=name, position=position, email=email, phone=phone))
    return contacts


def mask_contacts(raw: str) -> str:
    """Маска «Ответственных от вуза» для сэмплов и отчёта ошибок: только «И***в И.И.»."""
    masked = [mask_full_name(contact.full_name) for contact in parse_contacts(raw)]
    return "; ".join(masked) if masked else "***"


def split_direction_names(raw: str | None) -> list[str]:
    """«DevOps, QA» / «DevOps; QA» → ["DevOps", "QA"] без пустых и повторов."""
    names: list[str] = []
    for part in _LIST_SPLIT_RE.split(raw or ""):
        name = part.strip()
        if name and _norm(name) not in {_norm(n) for n in names}:
            names.append(name)
    return names


def product_vendor(description: str | None) -> str | None:
    """Вендор продукта: первая строка описания «Вендор: …» (так пишет и справочник
    продуктов — у модели Product нет отдельного поля вендора)."""
    first_line = (description or "").split("\n", 1)[0].strip()
    if first_line.startswith(_VENDOR_PREFIX):
        return first_line[len(_VENDOR_PREFIX) :].strip() or None
    return None


def with_vendor(description: str | None, vendor: str) -> str:
    """Описание продукта с вендором в первой строке (прочий текст сохраняется)."""
    line = f"{_VENDOR_PREFIX} {vendor}"
    if not description:
        return line
    if product_vendor(description) is not None or description.startswith(_VENDOR_PREFIX):
        rest = description.split("\n", 1)[1] if "\n" in description else ""
        return f"{line}\n{rest}" if rest else line
    return f"{line}\n{description}"


async def _load_kam_catalog_lookups(ctx: ImportContext) -> None:
    """Договоры с их строками и все сотрудники (в том числе неактивные — чтобы отличить
    «не найден» от «деактивирован») для таблицы КАМов."""
    if "contract_by_number" in ctx.lookups:
        return
    session = ctx.session
    contracts = (
        (await session.execute(select(Contract).options(selectinload(Contract.products))))
        .scalars()
        .all()
    )
    ctx.lookups["contract_by_number"] = {_norm(c.number): c for c in contracts}
    users = (await session.execute(select(AppUser))).scalars().all()
    by_name: dict[str, list[AppUser]] = {}
    for user in users:
        for key in person_name_keys(user.full_name or ""):
            by_name.setdefault(key, []).append(user)
    ctx.lookups["kam_by_name"] = by_name
    ctx.lookups["any_user_by_login"] = {_norm(u.username): u for u in users}
    ctx.lookups["any_user_by_login"].update({_norm(u.email): u for u in users if u.email})
    ctx.lookups["university_by_id"] = {u.id: u for u in ctx.lookups["university_by_name"].values()}
    ctx.lookups["contacts_by_university"] = {}


def _resolve_kam(ctx: ImportContext, raw: str) -> tuple[AppUser | None, tuple[str, str] | None]:
    """КАМ по «Фамилия Имя» (а также полному ФИО, «Фамилия И.О.», логину или email).

    Возвращает (пользователь, None) или (None, (код, сообщение)).
    """
    value = raw.strip()
    tokens = _name_tokens(value)
    login_match = ctx.lookups["any_user_by_login"].get(_norm(value))
    if login_match is not None:
        candidates = [login_match]
    elif len(tokens) < 2:
        return None, (
            "invalid_format",
            f"КАМ «{value}»: укажите фамилию и имя («Иванов Иван») или логин",
        )
    else:
        candidates = ctx.lookups["kam_by_name"].get(" ".join(tokens), [])
    active = list({u.id: u for u in candidates if u.is_active}.values())
    if len(active) == 1:
        return active[0], None
    if len(active) > 1:
        return None, (
            "ambiguous",
            f"КАМ «{value}»: найдено сотрудников — {len(active)}. Укажите полное ФИО или логин",
        )
    if candidates:
        return None, ("inactive_user", f"Сотрудник «{value}» деактивирован — укажите другого КАМа")
    return None, (
        "lookup_not_found",
        f"Сотрудник «{value}» не найден (поиск по фамилии и имени, ФИО, логину и email)",
    )


def _university_label(university: University) -> str:
    return university.short_name or university.name


def _license_issue(
    row: PreparedRow,
    line: ContractProduct | None,
    signed_at: date | None,
    term: LicenseTerm | None,
) -> None:
    current = (
        (line.license_signed_at, line.license_term_years, line.license_valid_to)
        if line is not None
        else (None, None, None)
    )
    try:
        merge_license(current, signed_at, term)
    except ValueError as exc:
        _issue(row, "license_term", "invalid_value", str(exc))


async def _validate_kam_catalog(ctx: ImportContext, rows: list[PreparedRow]) -> None:
    await _load_kam_catalog_lookups(ctx)
    actor = ctx.actor
    strategy = ctx.options.update_strategy
    can_assign = actor.access.has("universities:assign")
    can_write_any = actor.access.has("crm:write_any")
    can_write_products = actor.access.has("products:write")
    product_entry = ctx.entry_for("product")
    create_products = product_entry is not None and product_entry.create_missing
    contract_owner: dict[str, tuple[str, int]] = {}  # договор → (вуз, строка) в файле
    university_kam: dict[str, tuple[uuid.UUID, int]] = {}  # вуз → (КАМ, строка) в файле
    line_rows: dict[str, int] = {}  # «договор|продукт» → первая строка файла

    for row in rows:
        values = row.values
        # --- вуз --------------------------------------------------------------------------
        university_raw = (values.get("university") or "").strip()
        university = _lookup_university(ctx, university_raw) if university_raw else None
        # ключ вуза в файле: найденный — по id (ИНН и название сходятся), новый — по названию
        university_key = str(university.id) if university is not None else _norm(university_raw)
        if not university_raw:
            _issue(row, "university", "required", "Не заполнено обязательное поле «Вуз»")
        elif university is None and _INN_RE.fullmatch(university_raw):
            _issue(
                row,
                "university",
                "lookup_not_found",
                f"Вуз с ИНН {university_raw} не найден — для нового вуза укажите название",
            )
        else:
            _check_reference(
                ctx, row, target_field="university", found=university is not None, label="Вуз"
            )

        # --- договор: ключ строки; стратегия дублей решает, будет ли строка применена --------
        number = (values.get("contract_number") or "").strip()
        number_key = _norm(number)
        contract = ctx.lookups["contract_by_number"].get(number_key) if number else None
        skipped = (contract is not None and strategy == "create_only") or (
            contract is None and strategy == "update_only"
        )
        if not number:
            _issue(
                row,
                "contract_number",
                "required",
                "Не заполнено обязательное поле «Номер договора»",
            )
        elif contract is not None:
            owner = ctx.lookups["university_by_id"].get(contract.university_id)
            if contract.university_id is None:
                _issue(
                    row,
                    "contract_number",
                    "conflict",
                    f"Договор №{number} заключён с клиентом B2C, а не с вузом",
                )
            elif university_raw and (university is None or university.id != contract.university_id):
                owner_label = f"«{_university_label(owner)}»" if owner is not None else "другой"
                _issue(
                    row,
                    "contract_number",
                    "conflict",
                    f"Договор №{number} уже заведён на вуз {owner_label}",
                )
            elif (
                not skipped
                and not can_write_any
                and contract.responsible_user_id not in (None, actor.id)
            ):
                _issue(
                    row,
                    "contract_number",
                    "forbidden",
                    f"Договор №{number} ведёт другой КАМ: изменять чужие договоры может "
                    "пользователь с правом «Правка чужих вузов и договоров»",
                )
        if number and university_raw and contract is None:
            # новый договор: все его строки в файле — про один вуз (у существующего
            # владельца определяет БД — проверка выше)
            first = contract_owner.get(number_key)
            if first is None:
                contract_owner[number_key] = (university_key, row.row_number)
            elif first[0] != university_key:
                _issue(
                    row,
                    "contract_number",
                    "conflict_in_file",
                    f"Договор №{number} в строке {first[1]} указан для другого вуза",
                )

        # --- ИТ-продукты ------------------------------------------------------------------
        product_names = split_product_names(values.get("product") or "")
        if not product_names:
            _issue(row, "product", "required", "Не заполнено обязательное поле «ИТ-продукт»")
        products: list[Product | None] = []
        for name in product_names:
            product = _lookup_product(ctx, name)
            products.append(product)
            if number:
                line_key = f"{number_key}|{product.id if product is not None else _norm(name)}"
                if line_key in line_rows:
                    _issue(
                        row,
                        "product",
                        "duplicate_in_file",
                        f"Договор №{number} и ИТ-продукт «{name}» уже есть в строке "
                        f"{line_rows[line_key]}",
                    )
                else:
                    line_rows[line_key] = row.row_number
            if product is not None:
                continue
            if not create_products:
                _issue(
                    row,
                    "product",
                    "lookup_not_found",
                    f"ИТ-продукт «{name}» не найден в каталоге (поиск по коду и названию). "
                    "Добавьте его или выберите правило «Создавать отсутствующие»",
                )
            elif not can_write_products:
                _issue(
                    row,
                    "product",
                    "forbidden",
                    f"ИТ-продукта «{name}» нет в каталоге, а создавать продукты может "
                    "пользователь с правом «Создание и правка продуктов»",
                )

        # --- ИТ-направления ---------------------------------------------------------------
        for direction_name in split_direction_names(values.get("it_direction")):
            if _lookup_direction(ctx, direction_name) is None:
                _issue(
                    row,
                    "it_direction",
                    "lookup_not_found",
                    f"ИТ-направление «{direction_name}» не найдено в справочнике "
                    "(поиск по коду и названию)",
                )

        # --- лицензия ---------------------------------------------------------------------
        signed_at: date | None = None
        term: LicenseTerm | None = None
        license_ok = True
        if values.get("license_signed_at"):
            try:
                signed_at = parse_license_date(values["license_signed_at"])
            except ValueError as exc:
                license_ok = False
                _issue(row, "license_signed_at", "invalid_format", str(exc))
        if values.get("license_term"):
            try:
                term = parse_license_term(values["license_term"])
            except ValueError as exc:
                license_ok = False
                _issue(row, "license_term", "invalid_format", str(exc))
        if license_ok and (signed_at is not None or term is not None):
            # сверка с уже сохранённой лицензией строки договора (CHECK дат в БД)
            lines = contract.products if contract is not None else []
            targets = [
                next((item for item in lines if p is not None and item.product_id == p.id), None)
                for p in products
            ] or [None]
            issues_before = len(row.issues)
            for line in targets:
                _license_issue(row, line, signed_at, term)
                if len(row.issues) > issues_before:
                    break
        status_raw = values.get("transfer_status")
        if status_raw and resolve_transfer_status(status_raw) is None:
            _issue(
                row,
                "transfer_status",
                "invalid_value",
                f"Неизвестный статус передачи лицензии «{status_raw}». Допустимо: "
                + ", ".join(TRANSFER_STATUS_LABELS.values()),
            )

        # --- ответственный КАМ ------------------------------------------------------------
        kam: AppUser | None = None
        if values.get("kam"):
            kam, problem = _resolve_kam(ctx, values["kam"])
            if problem is not None:
                _issue(row, "kam", *problem)
        if kam is not None and university_raw:
            current_kam = university.kam_user_id if university is not None else None
            if not skipped and kam.id != current_kam and not can_assign:
                if university is None and kam.id != actor.id:
                    _issue(
                        row,
                        "kam",
                        "forbidden",
                        "Без права «Назначение ответственного КАМа за вуз» новый вуз "
                        "закрепляется только за вами",
                    )
                elif university is not None:
                    _issue(
                        row,
                        "kam",
                        "forbidden",
                        f"Сменить ответственного КАМа вуза «{_university_label(university)}» "
                        "может руководитель (право «Назначение ответственного КАМа за вуз»)",
                    )
            first_kam = university_kam.get(university_key)
            if first_kam is None:
                university_kam[university_key] = (kam.id, row.row_number)
            elif first_kam[0] != kam.id:
                _issue(
                    row,
                    "kam",
                    "conflict_in_file",
                    f"Для этого вуза в строке {first_kam[1]} указан другой КАМ",
                )

        # --- контакты вуза ----------------------------------------------------------------
        contacts_raw = values.get("university_contacts")
        if contacts_raw:
            if not parse_contacts(contacts_raw):
                _issue(
                    row,
                    "university_contacts",
                    "invalid_format",
                    "Ответственные от вуза: не найдено ни одного ФИО (людей разделяйте «;»)",
                )
            elif (
                not skipped
                and university is not None
                and not can_write_any
                and university.kam_user_id not in (None, actor.id)
            ):
                _issue(
                    row,
                    "university_contacts",
                    "forbidden",
                    f"Контакты вуза «{_university_label(university)}» дополняет его КАМ или "
                    "пользователь с правом «Правка чужих вузов и договоров»",
                )


def _unique_product_code(ctx: ImportContext, name: str) -> str:
    base = product_code_from_name(name)
    code, suffix = base, 2
    while _norm(code) in ctx.lookups["product_by_code"]:
        code, suffix = f"{base}_{suffix}", suffix + 1
    return code


async def _ensure_kam_product(
    ctx: ImportContext, name: str, vendor: str | None, details: Counter[str]
) -> Product:
    product = _lookup_product(ctx, name)
    created = product is None
    if product is None:
        product = Product(code=_unique_product_code(ctx, name), name=name, product_type="other")
        ctx.session.add(product)
        details["products_created"] += 1
    vendor = (vendor or "").strip()
    # вендор — атрибут каталога: меняет его создатель продукта или право products:write
    if vendor and _norm(product_vendor(product.description)) != _norm(vendor):
        if created or ctx.actor.access.has("products:write"):
            product.description = with_vendor(product.description, vendor)
            if not created:
                details["products_updated"] += 1
    if created:
        await ctx.session.flush()
        ctx.lookups["product_by_code"][_norm(product.code)] = product
        ctx.lookups["product_by_name"][_norm(name)] = product
    return product


async def _ensure_contacts(
    ctx: ImportContext, university: University, contacts: list[ContactInfo]
) -> int:
    """Дописывает контакты вуза; существующий (то же ФИО) дополняется пустыми полями."""
    cache: dict[uuid.UUID, dict[str, UniversityContact]] = ctx.lookups["contacts_by_university"]
    index = cache.get(university.id)
    if index is None:
        rows = (
            (
                await ctx.session.execute(
                    select(UniversityContact).where(
                        UniversityContact.university_id == university.id
                    )
                )
            )
            .scalars()
            .all()
        )
        index = {" ".join(_name_tokens(c.full_name)): c for c in rows}
        cache[university.id] = index
    created = 0
    for contact in contacts:
        key = " ".join(_name_tokens(contact.full_name))
        existing = index.get(key)
        if existing is not None:
            existing.position = existing.position or contact.position
            existing.email = existing.email or contact.email
            existing.phone = existing.phone or contact.phone
            continue
        row = UniversityContact(
            university_id=university.id,
            full_name=contact.full_name,
            position=contact.position,
            email=contact.email,
            phone=contact.phone,
            is_primary=not index,  # первый контакт вуза — основной
            notes="Импорт таблицы КАМов",
        )
        ctx.session.add(row)
        index[key] = row
        created += 1
    return created


async def _apply_kam_catalog(ctx: ImportContext, rows: list[PreparedRow]) -> ApplyStats:
    await _load_kam_catalog_lookups(ctx)
    stats = ApplyStats()
    details: Counter[str] = Counter()
    session = ctx.session
    now = datetime.now(timezone.utc)
    strategy = ctx.options.update_strategy
    created_here: set[str] = set()  # договоры этого файла: их строки — не «дубли»
    for row in rows:
        values = row.values
        number = (values["contract_number"] or "").strip()
        contract: Contract | None = ctx.lookups["contract_by_number"].get(_norm(number))
        # стратегия дублей — по ключу строки (номер договора), строка целиком; договор,
        # созданный строкой выше, дубликатом не считается — его продукты дописываются
        existed = contract is not None and _norm(number) not in created_here
        if (existed and strategy == "create_only") or (
            contract is None and strategy == "update_only"
        ):
            stats.skipped += 1
            continue

        university_raw = values["university"] or ""
        university = _lookup_university(ctx, university_raw)
        if university is None:
            university = await _ensure_university(ctx, university_raw)
            ctx.lookups["university_by_id"][university.id] = university
            details["universities_created"] += 1

        kam = _resolve_kam(ctx, values["kam"])[0] if values.get("kam") else None
        if kam is not None and university.kam_user_id != kam.id:
            before = university.kam_user_id
            university.kam_user_id = kam.id
            await write_audit(
                session,
                actor_user_id=ctx.actor.id,
                action="university.kam_changed",
                entity_type="university",
                entity_id=university.id,
                before={"kam_user_id": str(before) if before else None},
                after={
                    "kam_user_id": str(kam.id),
                    "reason": "import",
                    "import_session_id": str(ctx.import_session_id),
                },
            )
            details["kam_assigned"] += 1

        signed_at = (
            parse_license_date(values["license_signed_at"])
            if values.get("license_signed_at")
            else None
        )
        term = parse_license_term(values["license_term"]) if values.get("license_term") else None
        status = (
            resolve_transfer_status(values["transfer_status"])
            if values.get("transfer_status")
            else None
        )
        comment = (values.get("comment") or "").strip() or None

        row_created = False
        if contract is None:
            contract = Contract(
                number=number,
                university_id=university.id,
                # подписанная лицензия — договор действует; без даты — черновик
                status="active" if signed_at is not None else "draft",
                responsible_user_id=(kam.id if kam else university.kam_user_id or ctx.actor.id),
                products=[],
            )
            session.add(contract)
            await session.flush()
            ctx.lookups["contract_by_number"][_norm(number)] = contract
            created_here.add(_norm(number))
            await write_audit(
                session,
                actor_user_id=ctx.actor.id,
                action="contract.created",
                entity_type="contract",
                entity_id=contract.id,
                after={"number": contract.number, "status": contract.status, "source": "import"},
            )
            details["contracts_created"] += 1
            row_created = True
        elif kam is not None and contract.responsible_user_id != kam.id:
            contract.responsible_user_id = kam.id
            details["contracts_updated"] += 1

        directions = [
            direction
            for direction in (
                _lookup_direction(ctx, name)
                for name in split_direction_names(values.get("it_direction"))
            )
            if direction is not None
        ]
        for name in split_product_names(values["product"] or ""):
            product = await _ensure_kam_product(ctx, name, values.get("vendor"), details)
            if directions:
                details["directions_linked"] += await link_product_directions(
                    session, product.id, [d.id for d in directions]
                )
            line = next((item for item in contract.products if item.product_id == product.id), None)
            if line is None:
                line = ContractProduct(product_id=product.id, added_by=ctx.actor.id)
                contract.products.append(line)
                details["licenses_created"] += 1
                row_created = True
            else:
                details["licenses_updated"] += 1
            (
                line.license_signed_at,
                line.license_term_years,
                line.license_valid_to,
            ) = merge_license(
                (line.license_signed_at, line.license_term_years, line.license_valid_to),
                signed_at,
                term,
            )
            if status is not None and line.transfer_status != status:
                line.transfer_status = status
                line.transfer_status_updated_at = now
            if comment:
                line.note = comment
        await session.flush()

        if values.get("university_contacts"):
            details["contacts_created"] += await _ensure_contacts(
                ctx, university, parse_contacts(values["university_contacts"])
            )

        if row_created:
            stats.created += 1
        else:
            stats.updated += 1
    stats.details = {key: count for key, count in details.items() if count}
    return stats


# --------------------------------------------------------------------------------------
# Реестр импортёров
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Importer:
    validate: Any  # async (ctx, rows) -> None — заполняет issues
    apply: Any  # async (ctx, valid_rows) -> ApplyStats


_IMPORTERS: dict[str, Importer] = {
    "students": Importer(_validate_students, _apply_students),
    "universities": Importer(_validate_universities, _apply_universities),
    "requests_b2b": Importer(_validate_requests_b2b, _apply_requests_b2b),
    "requests_b2c": Importer(_validate_requests_b2c, _apply_requests_b2c),
    "kam_catalog": Importer(_validate_kam_catalog, _apply_kam_catalog),
}


def get_importer(entity_type: str) -> Importer:
    if entity_type.startswith("dictionary:"):
        entity_fields(entity_type)  # валидация имени справочника
        return Importer(_validate_dictionary, _apply_dictionary)
    importer = _IMPORTERS.get(entity_type)
    if importer is None:
        entity_fields(entity_type)  # бросит validation_error с перечнем доступных
        raise AssertionError("unreachable")
    return importer


async def prepare_context(
    session: AsyncSession,
    *,
    entity_type: str,
    entries: list[MappingEntry],
    raw_options: dict[str, Any] | None,
    actor: AppUser,
    crypto: PiiCrypto,
    import_session_id: uuid.UUID,
) -> ImportContext:
    spec = entity_fields(entity_type)
    ctx = ImportContext(
        session=session,
        spec=spec,
        options=ImportOptions.from_raw(raw_options, spec),
        entries=entries,
        actor=actor,
        crypto=crypto,
        import_session_id=import_session_id,
    )
    await _load_lookups(ctx)
    return ctx
