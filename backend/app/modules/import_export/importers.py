"""Построчная валидация и применение импорта по `entity_type` (api-contract.md §6.4).

Стадия B3 покрывает: `universities`, `students`, `requests_b2b`, `requests_b2c`
и `dictionary:*`. Дедупликация студентов/физлиц — по blind index `email_hmac`
(data-model.md §7.1); заявки создаются в initial-этапе своей воронки с историей
`kind='import'`. Валидация (dry-run) ничего не пишет; создание недостающих
сущностей (`create_missing`) происходит только на apply.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import (
    PiiCrypto,
    display_name_from_full_name,
    mask_email,
    normalize_email,
)
from app.models.crm import Counterparty, InteractionType, Product, Program, University
from app.models.deal import Deal, DealStageHistory
from app.models.service import AppSetting
from app.models.talent import FederalProject, Student, StudentStatusHistory
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.import_export.mapping import EntityFields, MappingEntry, entity_fields

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

    def as_dict(self) -> dict[str, int]:
        return {"created": self.created, "updated": self.updated, "skipped": self.skipped}


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


def find_file_duplicates(
    rows: list[tuple[int, str]], *, column: str
) -> dict[int, RowIssue]:
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
    itypes = (await session.execute(select(InteractionType))).scalars().all()
    ctx.lookups["itype_by_code"] = {_norm(t.code): t for t in itypes}
    ctx.lookups["itype_by_name"] = {_norm(t.name): t for t in itypes}
    users = (
        (await session.execute(select(AppUser).where(AppUser.is_active.is_(True))))
        .scalars()
        .all()
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
    return ctx.lookups["product_by_code"].get(_norm(raw)) or ctx.lookups[
        "product_by_name"
    ].get(_norm(raw))


def _lookup_user(ctx: ImportContext, raw: str) -> AppUser | None:
    return ctx.lookups["user_by_username"].get(_norm(raw)) or ctx.lookups[
        "user_by_email"
    ].get(_norm(raw))


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


async def _initial_stage(
    ctx: ImportContext, pipeline: str
) -> tuple[Workflow, WorkflowStage]:
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
    stmt = stmt.where(Counterparty.inn == inn) if inn else stmt.where(
        func.lower(Counterparty.org_name) == company.lower()
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
        title = (values.get("title") or "").strip() or (
            f"{counterparty.display_name} — {subject}"
        )
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


async def _apply_value_list(
    ctx: ImportContext, rows: list[PreparedRow], name: str
) -> ApplyStats:
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
