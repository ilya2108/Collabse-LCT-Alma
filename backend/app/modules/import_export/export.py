"""Экспорт данных и отчётов: XLSX / CSV / JSON (api-contract.md §7.1).

Устойчивость к кодировкам при выгрузке (решение кейсодержателя): CSV — `utf-8`
с BOM (Excel не ломает кириллицу) либо `cp1251`, разделитель по умолчанию `;`.
Резолюции (data-model приоритетнее api-contract):
- таблицы `export_job` в DDL нет — job_id = id файла-результата (`file_object`,
  `entity_type='report'`, bucket `crm-exports`); статус для поллинга UI живёт в
  KeyDB `crm:v1:export:{id}` (TTL 30 мин), деградация — чтение файла из PG;
- объёмы хакатона < 10 000 строк — экспорт всегда синхронный (`201 done`),
  жёсткий предохранитель — 50 000 строк;
- ПДн студентов в файле экспорта ВСЕГДА маскируются, независимо от роли
  (api-contract §8.2: раскрытие — только точечный POST /students/{id}/reveal
  с аудитом; упоминание «расшифровки на лету» в data-model §8.3 признано
  противоречащим контракту и критериям приёмки — маски по умолчанию);
- значения-«формулы» (=, +, -, @) экранируются в CSV и пишутся строками в XLSX
  (CWE-1236: инъекционные значения попадают в БД легитимно через импорт).
"""

from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import date, datetime, timezone
from typing import Any

import orjson
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.errors import not_found, unprocessable, validation_error
from app.core.pii import mask_email, mask_full_name, mask_phone
from app.models.crm import Contract, Counterparty, Product, Program, University
from app.models.deal import Deal
from app.models.service import FileObject
from app.models.talent import FederalProject, Student
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.files.service import store_bytes
from app.modules.reporting.service import REPORT_CODES, ReportParams, build_report

EXPORT_STATUS_TTL_SECONDS = 30 * 60
MAX_EXPORT_ROWS = 50_000

EXPORT_FORMATS = ("xlsx", "csv", "json")
EXPORT_ENTITY_TYPES = ("requests", "universities", "contracts", "programs", "students")

_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "json": "application/json",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


# --------------------------------------------------------------------------------------
# Датасеты реестров: (колонки, строки)
# --------------------------------------------------------------------------------------

Dataset = tuple[list[tuple[str, str]], list[dict[str, Any]]]


async def _dataset_requests(
    session: AsyncSession, filters: dict[str, Any], viewer: AppUser
) -> Dataset:
    stmt = (
        select(Deal, WorkflowStage, Workflow, AppUser, University, Counterparty, Product, Program)
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .join(AppUser, AppUser.id == Deal.responsible_user_id)
        .outerjoin(University, University.id == Deal.university_id)
        .outerjoin(Counterparty, Counterparty.id == Deal.counterparty_id)
        .outerjoin(Product, Product.id == Deal.product_id)
        .outerjoin(Program, Program.id == Deal.program_id)
        .where(Deal.status != "archived")
        .order_by(Deal.created_at.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    if filters.get("workflow_type"):
        stmt = stmt.where(Workflow.code == filters["workflow_type"])
    status_ids = filters.get("status_id") or []
    if isinstance(status_ids, str):
        status_ids = [status_ids]
    if status_ids:
        stmt = stmt.where(Deal.stage_id.in_([uuid.UUID(str(s)) for s in status_ids]))
    if filters.get("assignee_id"):
        stmt = stmt.where(Deal.responsible_user_id == uuid.UUID(str(filters["assignee_id"])))
    if filters.get("university_id"):
        stmt = stmt.where(Deal.university_id == uuid.UUID(str(filters["university_id"])))
    if filters.get("product_id"):
        stmt = stmt.where(Deal.product_id == uuid.UUID(str(filters["product_id"])))
    if filters.get("source"):
        stmt = stmt.where(Deal.source == filters["source"])
    if filters.get("created_from"):
        stmt = stmt.where(Deal.created_at >= date.fromisoformat(str(filters["created_from"])))
    if filters.get("created_to"):
        stmt = stmt.where(Deal.created_at <= date.fromisoformat(str(filters["created_to"])))

    columns = [
        ("id", "ID"),
        ("title", "Название"),
        ("workflow_type", "Воронка"),
        ("university.name", "Вуз"),
        ("client", "Клиент"),
        ("status.name", "Этап"),
        ("assignee.full_name", "Ответственный"),
        ("amount", "Сумма"),
        ("currency", "Валюта"),
        ("source", "Источник"),
        ("product.name", "Продукт"),
        ("program.name", "Программа"),
        ("created_at", "Создана"),
        ("status_updated_at", "На этапе с"),
    ]
    rows = []
    for deal, stage, workflow, assignee, university, counterparty, product, program in (
        await session.execute(stmt)
    ).all():
        rows.append(
            {
                "id": str(deal.id),
                "title": deal.title,
                "workflow_type": workflow.code,
                "university.name": university.name if university else None,
                "client": counterparty.display_name if counterparty else None,
                "status.name": stage.name,
                "assignee.full_name": assignee.full_name or assignee.username,
                "amount": format(deal.amount, "f") if deal.amount is not None else None,
                "currency": deal.currency,
                "source": deal.source,
                "product.name": product.name if product else None,
                "program.name": program.name if program else None,
                "created_at": _fmt(deal.created_at),
                "status_updated_at": _fmt(deal.stage_entered_at),
            }
        )
    return columns, rows


async def _dataset_universities(
    session: AsyncSession, filters: dict[str, Any], viewer: AppUser
) -> Dataset:
    stmt = select(University, AppUser).outerjoin(
        AppUser, AppUser.id == University.kam_user_id
    ).order_by(University.name).limit(MAX_EXPORT_ROWS)
    if filters.get("region"):
        stmt = stmt.where(University.region == filters["region"])
    if filters.get("is_active") is not None:
        stmt = stmt.where(University.is_active.is_(bool(filters["is_active"])))
    columns = [
        ("name", "Название"),
        ("short_name", "Краткое название"),
        ("inn", "ИНН"),
        ("region", "Регион"),
        ("city", "Город"),
        ("website", "Сайт"),
        ("kam.full_name", "Ответственный КАМ"),
        ("is_active", "Активен"),
    ]
    rows = [
        {
            "name": u.name,
            "short_name": u.short_name,
            "inn": u.inn,
            "region": u.region,
            "city": u.city,
            "website": u.website,
            "kam.full_name": (kam.full_name or kam.username) if kam else None,
            "is_active": "да" if u.is_active else "нет",
        }
        for u, kam in (await session.execute(stmt)).all()
    ]
    return columns, rows


async def _dataset_contracts(
    session: AsyncSession, filters: dict[str, Any], viewer: AppUser
) -> Dataset:
    stmt = (
        select(Contract, University, Counterparty)
        .outerjoin(University, University.id == Contract.university_id)
        .outerjoin(Counterparty, Counterparty.id == Contract.counterparty_id)
        .order_by(Contract.created_at.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    if filters.get("status"):
        stmt = stmt.where(Contract.status == filters["status"])
    if filters.get("university_id"):
        stmt = stmt.where(Contract.university_id == uuid.UUID(str(filters["university_id"])))
    columns = [
        ("number", "Номер"),
        ("owner", "Владелец"),
        ("status", "Статус"),
        ("signed_at", "Подписан"),
        ("valid_from", "Действует с"),
        ("valid_to", "Действует по"),
        ("amount", "Сумма"),
        ("currency", "Валюта"),
    ]
    rows = [
        {
            "number": c.number,
            "owner": university.name if university else (
                counterparty.display_name if counterparty else None
            ),
            "status": c.status,
            "signed_at": _fmt(c.signed_at),
            "valid_from": _fmt(c.valid_from),
            "valid_to": _fmt(c.valid_to),
            "amount": format(c.amount, "f") if c.amount is not None else None,
            "currency": c.currency,
        }
        for c, university, counterparty in (await session.execute(stmt)).all()
    ]
    return columns, rows


async def _dataset_programs(
    session: AsyncSession, filters: dict[str, Any], viewer: AppUser
) -> Dataset:
    stmt = (
        select(Program, Product, University)
        .outerjoin(Product, Product.id == Program.product_id)
        .outerjoin(University, University.id == Program.university_id)
        .order_by(Program.priority_rank, Program.name)
        .limit(MAX_EXPORT_ROWS)
    )
    if filters.get("product_id"):
        stmt = stmt.where(Program.product_id == uuid.UUID(str(filters["product_id"])))
    if filters.get("is_active") is not None:
        stmt = stmt.where(Program.is_active.is_(bool(filters["is_active"])))
    columns = [
        ("name", "Программа"),
        ("product.name", "Продукт"),
        ("university.name", "Вуз"),
        ("priority_rank", "Приоритет (меньше = выше)"),
        ("seats", "Мест"),
        ("starts_on", "Старт"),
        ("published_to_cms", "Опубликована в CMS"),
    ]
    rows = [
        {
            "name": p.name,
            "product.name": product.name if product else None,
            "university.name": university.name if university else None,
            "priority_rank": p.priority_rank,
            "seats": p.seats,
            "starts_on": _fmt(p.starts_on),
            "published_to_cms": "да" if p.published_to_cms else "нет",
        }
        for p, product, university in (await session.execute(stmt)).all()
    ]
    return columns, rows


async def _dataset_students(
    session: AsyncSession, filters: dict[str, Any], viewer: AppUser
) -> Dataset:
    stmt = (
        select(Student, University, Program, FederalProject)
        .outerjoin(University, University.id == Student.university_id)
        .outerjoin(Program, Program.id == Student.program_id)
        .outerjoin(FederalProject, FederalProject.id == Student.federal_project_id)
        .order_by(Student.display_name)
        .limit(MAX_EXPORT_ROWS)
    )
    if filters.get("funnel_status"):
        stmt = stmt.where(Student.funnel_status == filters["funnel_status"])
    if filters.get("university_id"):
        stmt = stmt.where(Student.university_id == uuid.UUID(str(filters["university_id"])))
    if filters.get("federal_project_id"):
        stmt = stmt.where(
            Student.federal_project_id == uuid.UUID(str(filters["federal_project_id"]))
        )
    columns = [
        ("display_name", "Студент"),
        ("full_name", "ФИО"),
        ("email", "Email"),
        ("phone", "Телефон"),
        ("university.name", "Вуз"),
        ("program.name", "Программа"),
        ("federal_project.name", "Федпроект"),
        ("funnel_status", "Статус воронки"),
        ("created_at", "Создан"),
    ]
    rows = []
    for student, university, program, project in (await session.execute(stmt)).all():
        # ПДн всегда маскируются в файле (api-contract §8.2) — файл живёт дольше
        # сессии и прав выгрузившего; раскрытие — только /students/{id}/reveal
        full_name = mask_full_name(student.full_name)
        email = mask_email(student.email)
        phone = mask_phone(student.phone) if student.phone else None
        rows.append(
            {
                "display_name": student.display_name,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "university.name": university.name if university else None,
                "program.name": program.name if program else None,
                "federal_project.name": project.name if project else None,
                "funnel_status": student.funnel_status,
                "created_at": _fmt(student.created_at),
            }
        )
    return columns, rows


_DATASETS = {
    "requests": _dataset_requests,
    "universities": _dataset_universities,
    "contracts": _dataset_contracts,
    "programs": _dataset_programs,
    "students": _dataset_students,
}


async def _dataset_report(
    session: AsyncSession, code: str, filters: dict[str, Any], viewer: AppUser
) -> Dataset:
    """`report:<code>` — плоская таблица из series/rows отчёта §7.2."""
    if code not in REPORT_CODES:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "entity_type",
                    "code": "unknown_report",
                    "message": f"Неизвестный отчёт «{code}». Доступно: {', '.join(REPORT_CODES)}",
                }
            ],
        )

    def _get_uuid(key: str) -> uuid.UUID | None:
        raw = filters.get(key)
        return uuid.UUID(str(raw)) if raw else None

    params = ReportParams(
        workflow_type=filters.get("workflow_type"),
        date_from=date.fromisoformat(str(filters["from"])) if filters.get("from") else None,
        date_to=date.fromisoformat(str(filters["to"])) if filters.get("to") else None,
        kam_id=_get_uuid("kam_id"),
        product_id=_get_uuid("product_id"),
        university_id=_get_uuid("university_id"),
        region=filters.get("region"),
        federal_project_id=_get_uuid("federal_project"),
    )
    report = await build_report(session, code, viewer, params)
    if "series" in report:
        first = report["series"][0] if report["series"] else {}
        if "points" in first:  # dynamics: строка на дату, колонка на серию
            columns = [("date", "Дата")] + [
                (s["key"], s["label"]) for s in report["series"]
            ]
            by_date: dict[str, dict[str, Any]] = {}
            for series in report["series"]:
                for point in series["points"]:
                    by_date.setdefault(point["date"], {"date": point["date"]})[
                        series["key"]
                    ] = point["value"]
            return columns, [by_date[key] for key in sorted(by_date)]
        columns = [("label", "Показатель"), ("value", "Значение")]
        if any("amount" in s for s in report["series"]):
            columns.append(("amount", "Сумма"))
        return columns, [dict(s) for s in report["series"]]
    if "rows" in report:
        columns = [(c["key"], c["label"]) for c in report.get("columns", [])]
        if not columns and report["rows"]:
            columns = [(key, key) for key in report["rows"][0]]
        flat_rows = []
        for row in report["rows"]:
            flat = {}
            for key, _label in columns:
                value = row.get(key)
                if isinstance(value, dict):
                    value = value.get("full_name") or value.get("name") or str(value)
                flat[key] = value
            flat_rows.append(flat)
        return columns, flat_rows
    # dashboard: KPI-показатели одной таблицей. _kpi отдаёт метрики в форме
    # {value, delta} (ключи active/completed/conversion/stuck) + period —
    # разворачиваем словари в скаляры, иначе XLSX падает на dict-значении
    kpi = report.get("kpi", {})
    columns = [("metric", "Показатель"), ("value", "Значение"), ("delta", "Дельта")]
    labels = {
        "active": "Заявок активных",
        "completed": "Завершено за период",
        "conversion": "Конверсия, %",
        "stuck": "Зависших",
    }
    rows = []
    for key, label in labels.items():
        if key not in kpi:
            continue
        value = kpi[key]
        if isinstance(value, dict):
            rows.append(
                {"metric": label, "value": value.get("value"), "delta": value.get("delta")}
            )
        else:
            rows.append({"metric": label, "value": value, "delta": None})
    period = kpi.get("period")
    if isinstance(period, dict):
        rows.append(
            {
                "metric": "Период",
                "value": f"{period.get('from')} — {period.get('to')}",
                "delta": None,
            }
        )
    return columns, rows


# --------------------------------------------------------------------------------------
# Писатели форматов
# --------------------------------------------------------------------------------------


# CWE-1236 (formula injection): ячейка, начинающаяся с этих символов, может быть
# исполнена Excel как формула (DDE-вектор вида =cmd|' /c calc'!A1). Инъекционные
# значения попадают в БД легитимно через импорт — экранируем на выходе.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _is_formula_like(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(_FORMULA_PREFIXES):
        return False
    try:
        float(value.replace(" ", "").replace(",", "."))
    except ValueError:
        return True
    return False  # «-5», «+7.2» — числа со знаком, а не формулы


def _write_xlsx(columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> bytes:
    import openpyxl
    from openpyxl.styles import Font

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Выгрузка"
    sheet.append([label for _key, label in columns])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row.get(key) for key, _label in columns])
        for cell in sheet[sheet.max_row]:
            # openpyxl делает строку с ведущим «=» ЖИВОЙ формулой (data_type='f');
            # принудительно сохраняем как текст — значение не меняется
            if cell.data_type == "f":
                cell.data_type = "s"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_csv(
    columns: list[tuple[str, str]],
    rows: list[dict[str, Any]],
    *,
    encoding: str,
    delimiter: str,
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([label for _key, label in columns])
    for row in rows:
        cells = []
        for key, _label in columns:
            value = row.get(key)
            if value is None:
                value = ""
            elif _is_formula_like(value):
                value = f"'{value}"  # стандартное экранирование =+-@ (CWE-1236)
            cells.append(value)
        writer.writerow(cells)
    text = buffer.getvalue()
    if encoding == "cp1251":
        return text.encode("cp1251", errors="replace")
    # utf-8 c BOM — Excel корректно открывает кириллицу (§7.1)
    return text.encode("utf-8-sig")


def _write_json(columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> bytes:
    return orjson.dumps(
        {"columns": [{"key": k, "label": label} for k, label in columns], "items": rows},
        option=orjson.OPT_INDENT_2,
    )


def render_export(
    columns: list[tuple[str, str]],
    rows: list[dict[str, Any]],
    *,
    export_format: str,
    encoding: str = "utf-8",
    delimiter: str = ";",
) -> bytes:
    if export_format == "xlsx":
        return _write_xlsx(columns, rows)
    if export_format == "csv":
        return _write_csv(columns, rows, encoding=encoding, delimiter=delimiter)
    return _write_json(columns, rows)


def _select_columns(
    columns: list[tuple[str, str]], requested: list[str] | None
) -> list[tuple[str, str]]:
    if not requested:
        return columns
    available = dict(columns)
    unknown = [key for key in requested if key not in available]
    if unknown:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "columns",
                    "code": "unknown_column",
                    "message": (
                        f"Неизвестные колонки: {', '.join(unknown)}. "
                        f"Доступно: {', '.join(available)}"
                    ),
                }
            ],
        )
    return [(key, available[key]) for key in requested]


# --------------------------------------------------------------------------------------
# Джобы экспорта
# --------------------------------------------------------------------------------------


def _job_status_payload(
    file_object: FileObject, *, entity_type: str, export_format: str, rows: int
) -> dict[str, Any]:
    return {
        "id": str(file_object.id),
        "status": "done",
        "entity_type": entity_type,
        "format": export_format,
        "rows": rows,
        "file": {
            "id": str(file_object.id),
            "file_name": file_object.original_name,
            "content_type": file_object.content_type,
            "size_bytes": file_object.size_bytes,
            "s3_key": file_object.s3_key,
        },
        "created_at": _now().isoformat(),
    }


async def create_export_job(
    session: AsyncSession,
    *,
    user: AppUser,
    entity_type: str,
    export_format: str,
    filters: dict[str, Any],
    columns: list[str] | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    if export_format not in EXPORT_FORMATS:
        raise unprocessable(f"Формат «{export_format}» не поддерживается (xlsx/csv/json)")
    encoding = (options.get("encoding") or "utf-8").lower()
    if encoding not in ("utf-8", "cp1251"):
        raise unprocessable("Кодировка CSV — только utf-8 (с BOM) или cp1251")
    delimiter = options.get("csv_delimiter") or ";"

    if entity_type.startswith("report:"):
        dataset = await _dataset_report(session, entity_type.split(":", 1)[1], filters, user)
    else:
        builder = _DATASETS.get(entity_type)
        if builder is None:
            raise validation_error(
                "Ошибка валидации данных",
                details=[
                    {
                        "field": "entity_type",
                        "code": "unsupported_entity_type",
                        "message": (
                            f"Экспорт «{entity_type}» не поддерживается. Доступно: "
                            + ", ".join(EXPORT_ENTITY_TYPES)
                            + ", report:<code>"
                        ),
                    }
                ],
            )
        dataset = await builder(session, filters, user)

    all_columns, rows = dataset
    selected = _select_columns(all_columns, columns)
    data = await asyncio.to_thread(
        render_export,
        selected,
        rows,
        export_format=export_format,
        encoding=encoding,
        delimiter=delimiter,
    )
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{entity_type.replace(':', '_')}_{stamp}.{export_format}"
    file_object = await store_bytes(
        session,
        data=data,
        original_name=file_name,
        content_type=_CONTENT_TYPES[export_format],
        bucket=get_settings().s3_bucket_exports,
        entity_type="report",
        entity_id=user.id,  # владелец выгрузки; сам файл — самостоятельная сущность
        uploaded_by=user.id,
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        action="student.exported" if entity_type == "students" else "export.created",
        entity_type="export",
        entity_id=file_object.id,
        after={"entity_type": entity_type, "format": export_format, "rows": len(rows)},
    )
    await session.commit()

    payload = _job_status_payload(
        file_object, entity_type=entity_type, export_format=export_format, rows=len(rows)
    )
    await get_cache().set_json(
        f"export:{file_object.id}", payload, EXPORT_STATUS_TTL_SECONDS
    )
    return payload


async def get_export_job(
    session: AsyncSession, job_id: uuid.UUID, user: AppUser
) -> tuple[dict[str, Any], FileObject]:
    """Статус джобы: KeyDB (быстро) с деградацией в file_object (истина в PG)."""
    file_object = await session.get(FileObject, job_id)
    if (
        file_object is None
        or file_object.entity_type != "report"
        or file_object.deleted_at is not None
    ):
        raise not_found("Выгрузка не найдена")
    if "admin" not in (user.roles or []) and file_object.uploaded_by != user.id:
        raise not_found("Выгрузка не найдена")
    cached = await get_cache().get_json(f"export:{job_id}")
    if cached is not None:
        return cached, file_object
    export_format = (file_object.original_name or "").rsplit(".", 1)[-1] or "xlsx"
    payload = _job_status_payload(
        file_object,
        entity_type="export",
        export_format=export_format,
        rows=-1,  # после истечения KeyDB-статуса точное число строк недоступно
    )
    payload["created_at"] = _fmt(file_object.created_at)
    return payload, file_object


async def list_export_jobs(session: AsyncSession, user: AppUser) -> list[dict[str, Any]]:
    stmt = (
        select(FileObject)
        .where(FileObject.entity_type == "report", FileObject.deleted_at.is_(None))
        .order_by(FileObject.created_at.desc())
        .limit(100)
    )
    if "admin" not in (user.roles or []):
        stmt = stmt.where(FileObject.uploaded_by == user.id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(f.id),
            "status": "done",
            "file": {
                "id": str(f.id),
                "file_name": f.original_name,
                "content_type": f.content_type,
                "size_bytes": f.size_bytes,
            },
            "created_at": _fmt(f.created_at),
        }
        for f in rows
    ]
