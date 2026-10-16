"""Выгрузка «Взаимодействия за период» по выбранным колонкам (ТЗ ФТ4): XLSX, PDF, CSV, JSON.

Вызывается механизмом выгрузок `POST /export/jobs {entity_type: "report:interactions",
columns: [...], filters: {...}}` (MinIO, аудит `export.created`, журнал выгрузок, та же
зона видимости, что у API). Данные — `interactions.build_interactions(for_export=True)`:
все строки, без предохранителя размера ответа.
- XLSX: лист «Взаимодействия» — таблица выбранных колонок с A1 (автофильтр, закреплённая
  шапка, настоящие даты), лист «О выгрузке» — период, фильтры, колонки, определения;
  защита CWE-1236 — строки «=…» пишутся текстом;
- PDF (fpdf2): заголовок, период и фильтры, таблица с повтором шапки на каждой странице
  (альбомная ориентация при > 6 колонках), «Как считается»;
- CSV/JSON — общими писателями выгрузки.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AppUser
from app.modules.reporting import interactions as it
from app.modules.reporting.kam_activity import REPORT_TZ, WORKFLOW_LABELS
from app.modules.reporting.kam_activity_export import _Sheet, period_text
from app.modules.reporting.pdf import Column, make_report_pdf

REPORT_TITLE = "Взаимодействия с вузами за период"
SHEET_NAMES = ("Взаимодействия", "О выгрузке")
EMPTY = "—"
_OUTCOMES = {"won": "Выиграна", "lost": "Отказ"}
_DATE_COLUMNS = frozenset({"created_at", "stage_since", "last_contact_at"})
_DATE_FORMAT = "DD.MM.YYYY"
_MONEY_FORMAT = "#,##0"
_XLSX_WIDTHS = {
    "university": 44,
    "it_direction": 18,
    "product": 30,
    "status": 24,
    "responsible": 30,
    "program": 30,
    "title": 44,
    "workflow_type": 9,
    "created_at": 13,
    "stage_since": 13,
    "last_contact_at": 16,
    "days_on_stage": 11,
    "closed_in_period": 12,
    "interactions": 14,
    "amount": 14,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _msk_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(REPORT_TZ).date()


def _money_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value)))
    except InvalidOperation:
        return None


def place_name(row: Mapping[str, Any]) -> str:
    """Наименование вуза; у B2C-заявки — псевдоним клиента с пометкой «(B2C)»."""
    if row.get("university"):
        return str(row["university"])
    if row.get("client"):
        return f"{row['client']}{it.B2C_SUFFIX}"
    return EMPTY


def cell_value(row: Mapping[str, Any], key: str) -> Any:
    """Значение колонки для файла: даты — `date` (МСК), числа — числами, пусто — «—»
    там, где пустота значима (ИТ-направление до справочника направлений)."""
    if key == "university":
        return place_name(row)
    if key == "it_direction":
        return row.get("it_direction") or EMPTY
    if key == "status":
        return row["stage"]["name"]
    if key == "workflow_type":
        return WORKFLOW_LABELS.get(row["workflow_type"], row["workflow_type"])
    if key in _DATE_COLUMNS:
        return _msk_date(row.get(key))
    if key == "days_on_stage":
        return None if row["stage"]["is_terminal"] else row["days_on_stage"]
    if key == "closed_in_period":
        return _OUTCOMES.get(row.get("closed_in_period") or "")
    if key == "interactions":
        return row["interactions"]["total"]
    if key == "amount":
        return _money_number(row.get("amount"))
    return row.get(key)


def flat_rows(
    report: Mapping[str, Any], columns: Sequence[str]
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    labels = [(key, it.COLUMNS_BY_KEY[key].label) for key in columns]
    rows = [{key: cell_value(row, key) for key in columns} for row in report.get("rows", [])]
    return labels, rows


def describe_filters(report: Mapping[str, Any]) -> str:
    """Фильтры текстом для шапки файлов: имена — из самого отчёта, без запросов."""
    params = report.get("params") or {}
    rows = report.get("rows", [])
    parts = ["Процесс: " + WORKFLOW_LABELS.get(params.get("workflow_type") or "", "B2B и B2C")]

    def names(ids: Sequence[str], pairs: Sequence[tuple[str | None, str | None]]) -> str:
        known = {key: value for key, value in pairs if key and value}
        return ", ".join(known.get(item, item) for item in ids)

    scope = report.get("scope") or {}
    kam_ids = params.get("kam_id") or []
    if kam_ids:
        label = "Только свои заявки" if scope.get("own_only") else "Ответственные"
        pairs = [(row["responsible_id"], row["responsible"]) for row in rows]
        parts.append(f"{label}: {names(kam_ids, pairs)}")
    if params.get("university_id"):
        pairs = [(row["university_id"], row["university"]) for row in rows]
        parts.append("Вузы: " + names(params["university_id"], pairs))
    if params.get("product_id"):
        pairs = [(row["product_id"], row["product"]) for row in rows]
        parts.append("ИТ-продукты: " + names(params["product_id"], pairs))
    if params.get("program_id"):
        pairs = [(row["program_id"], row["program"]) for row in rows]
        parts.append("Программы: " + names(params["program_id"], pairs))
    if params.get("stage_id"):
        pairs = [(stage["id"], stage["name"]) for stage in report.get("stages", [])]
        parts.append("Статусы: " + names(params["stage_id"], pairs))
    return " · ".join(parts)


# --------------------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------------------


def render_interactions_xlsx(
    report: Mapping[str, Any],
    columns: Sequence[str],
    *,
    generated_by: str,
    generated_at: datetime,
) -> bytes:
    import openpyxl

    labels, rows = flat_rows(report, columns)
    workbook = openpyxl.Workbook()
    data_sheet = workbook.active
    data_sheet.title = SHEET_NAMES[0]
    sheet = _Sheet(data_sheet)
    header = sheet.header([label for _key, label in labels])
    formats = [
        _DATE_FORMAT if key in _DATE_COLUMNS else _MONEY_FORMAT if key == "amount" else None
        for key, _label in labels
    ]
    for row in rows:
        sheet.line([row[key] for key, _label in labels], formats)
    sheet.finish_table(header, len(labels))
    sheet.widths([_XLSX_WIDTHS.get(key, 16) for key, _label in labels])

    about = _Sheet(workbook.create_sheet(SHEET_NAMES[1]))
    about.title(REPORT_TITLE)
    about.subtitle(period_text(report))
    about.subtitle(f"Фильтры: {describe_filters(report)}")
    about.subtitle(
        f"Сформировал: {generated_by}, {generated_at.astimezone(REPORT_TZ):%d.%m.%Y %H:%M} МСК"
    )
    about.subtitle(f"Строк: {len(rows)}")
    about.skip()
    about.subtitle("Колонки", bold=True)
    for _key, label in labels:
        about.line([f"• {label}"])
    about.skip()
    about.subtitle("Как считается", bold=True)
    for text in it.DEFINITIONS:
        about.line([text])
    about.widths([120])

    workbook.properties.title = REPORT_TITLE
    workbook.properties.creator = "Альма"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------------


def _pdf_text(key: str, value: Any) -> Any:
    if value is None:
        return EMPTY
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if key == "amount" and isinstance(value, float):
        return f"{round(value):,}".replace(",", " ") + " ₽"
    return value


def render_interactions_pdf(
    report: Mapping[str, Any],
    columns: Sequence[str],
    *,
    generated_by: str,
    generated_at: datetime,
) -> bytes:
    labels, rows = flat_rows(report, columns)
    generated = f"{generated_at.astimezone(REPORT_TZ):%d.%m.%Y %H:%M} МСК"
    pdf = make_report_pdf(
        title=REPORT_TITLE,
        generated=generated,
        orientation="L" if len(labels) > 6 else "P",
    )
    totals = report.get("totals") or {}
    pdf.title_block(
        REPORT_TITLE,
        [
            period_text(report),
            f"Фильтры: {describe_filters(report)}",
            f"Сформировал: {generated_by}",
            f"Заявок: {totals.get('requests', len(rows))}, вузов: "
            f"{totals.get('universities', 0)}, с взаимодействиями КАМа: "
            f"{totals.get('with_interactions', 0)}",
        ],
    )
    pdf.data_table(
        [
            Column(
                key,
                label,
                it.COLUMNS_BY_KEY[key].width,
                "R" if key in ("days_on_stage", "interactions", "amount") else "L",
            )
            for key, label in labels
        ],
        [{key: _pdf_text(key, row[key]) for key, _label in labels} for row in rows],
        empty_text="За выбранный период и фильтры взаимодействий нет.",
    )
    pdf.section("Как считается")
    for text in it.DEFINITIONS:
        pdf.paragraph(text, size=7.5)
        pdf.ln(0.8)
    return pdf.to_bytes()


# --------------------------------------------------------------------------------------
# Точка входа выгрузки
# --------------------------------------------------------------------------------------

_EXTENSIONS = {"xlsx": "xlsx", "pdf": "pdf", "csv": "csv", "json": "json"}


def _json_ready(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value.isoformat() if isinstance(value, date) else value for key, value in row.items()}
        for row in rows
    ]


async def build_interactions_file(
    session: AsyncSession,
    *,
    user: AppUser,
    filters: Mapping[str, Any],
    export_format: str,
    columns: Sequence[str] | None = None,
    options: Mapping[str, Any] | None = None,
) -> tuple[bytes, int, str]:
    """Файл отчёта: `(байты, строк, имя файла)`; область — как у API (403 на чужих)."""
    if export_format not in _EXTENSIONS:
        from app.core.errors import unprocessable

        raise unprocessable(f"Формат «{export_format}» не поддерживается для этого отчёта")
    selected = it.resolve_columns(columns)
    params = it.InteractionsParams.from_filters(filters)
    report = await it.build_interactions(session, user, params, for_export=True)
    period = report["period"]
    stamp = f"{date.fromisoformat(period['from']):%Y%m%d}-{date.fromisoformat(period['to']):%Y%m%d}"
    file_name = f"interactions_{stamp}.{_EXTENSIONS[export_format]}"
    rows_count = len(report["rows"])

    if export_format in ("csv", "json"):
        from app.modules.import_export.export import render_export

        labels, rows = flat_rows(report, selected)
        opts = options or {}
        data = await asyncio.to_thread(
            render_export,
            labels,
            _json_ready(rows) if export_format == "json" else rows,
            export_format=export_format,
            encoding=(opts.get("encoding") or "utf-8").lower(),
            delimiter=opts.get("csv_delimiter") or ";",
        )
        return data, rows_count, file_name

    renderer = render_interactions_xlsx if export_format == "xlsx" else render_interactions_pdf
    data = await asyncio.to_thread(
        renderer,
        report,
        selected,
        generated_by=user.full_name or user.username,
        generated_at=_now(),
    )
    return data, rows_count, file_name
