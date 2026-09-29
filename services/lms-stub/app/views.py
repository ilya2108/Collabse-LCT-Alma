"""UI журнала, демо-триггеры жизненного цикла обучения и служебные эндпоинты."""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .common import error_response
from .db import Storage
from .ui import render_page

router = APIRouter()

#: kind → (event_type, status.code, status.name) исходящего события (api-contract §13.3).
LIFECYCLE_EVENTS: dict[str, tuple[str, str, str]] = {
    "started": ("lms.learning.started", "in_learning", "Обучение началось"),
    "progress": ("lms.learning.progress", "in_learning", "Прогресс обучения: 50%"),
    "completed": ("lms.learning.completed", "completed", "Обучение завершено"),
}


def _payload_of(envelope_raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(envelope_raw).get("payload")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _enrollment_items(storage: Storage) -> list[dict[str, Any]]:
    items = []
    for row in storage.list_enrollments():
        payload = _payload_of(row["envelope"])
        program = payload.get("program") or {}
        university = payload.get("university") or {}
        items.append(
            {
                "lms_enrollment_id": row["lms_enrollment_id"],
                "crm_request_id": row["crm_request_id"],
                "lifecycle": row["lifecycle"],
                "program_name": program.get("name") if isinstance(program, dict) else None,
                "university_name": (
                    university.get("name") if isinstance(university, dict) else None
                ),
                "received_at": row["received_at"],
                "updated_at": row["updated_at"],
            }
        )
    return items


# ------------------------------------------------------------------- страница


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request, flash: str | None = None) -> HTMLResponse:
    storage: Storage = request.app.state.storage
    return HTMLResponse(
        render_page(
            crm_webhook_url=request.app.state.settings.crm_webhook_url,
            counters=storage.counters(),
            enrollments=_enrollment_items(storage),
            programs=storage.list_programs(),
            journal=storage.journal(),
            flash=flash,
        )
    )


# ---------------------------------------------------------------- демо-триггер


@router.post("/demo/enrollments/{lms_enrollment_id}/lifecycle", include_in_schema=False)
def trigger_lifecycle(
    request: Request, lms_enrollment_id: str, kind: str = Form(...)
) -> Response:
    """Кнопка демо-страницы: LMS шлёт подписанный вебхук lms.learning.* в CRM."""
    if kind not in LIFECYCLE_EVENTS:
        return error_response(
            400, "validation_error", "Неизвестный шаг жизненного цикла обучения"
        )
    storage: Storage = request.app.state.storage
    row = storage.get_enrollment(lms_enrollment_id)
    if row is None:
        return error_response(404, "not_found", "Зачисление не найдено")
    event_type, status_code, status_name = LIFECYCLE_EVENTS[kind]
    source_payload = _payload_of(row["envelope"])
    payload: dict[str, Any] = {
        "status": {"code": status_code, "name": status_name},
        "responsible": source_payload.get("responsible"),
        "university": source_payload.get("university"),
        "program": source_payload.get("program"),
        "product": source_payload.get("product"),
        "links": {
            "db_keys": {
                "request_id": row["crm_request_id"],
                "lms_enrollment_id": lms_enrollment_id,
            },
            "s3_keys": (
                [f"lms-exports/{lms_enrollment_id}/final_report.pdf"]
                if kind == "completed"
                else []
            ),
        },
    }
    if kind == "progress":
        payload["progress"] = {"percent": 50}
    request.app.state.relay.enqueue(
        event_type=event_type,
        correlation={
            "crm_request_id": row["crm_request_id"],
            "lms_enrollment_id": lms_enrollment_id,
            "cms_lead_id": None,
        },
        payload=payload,
        local_ref=lms_enrollment_id,
    )
    storage.set_lifecycle(lms_enrollment_id, kind)
    flash = f"Событие {event_type} поставлено в очередь доставки в CRM"
    return RedirectResponse(url=f"/?flash={quote(flash)}", status_code=303)


# ------------------------------------------------------------- JSON-эндпоинты


@router.get("/api/v1/enrollments", summary="Принятые зачисления")
def list_enrollments(request: Request) -> dict[str, Any]:
    items = _enrollment_items(request.app.state.storage)
    return {"items": items, "total": len(items)}


@router.get("/api/events", summary="Журнал обмена (принятые и исходящие события)")
def list_events(request: Request, direction: str | None = None) -> dict[str, Any]:
    entries = request.app.state.storage.journal()
    if direction in ("in", "out"):
        entries = [entry for entry in entries if entry["direction"] == direction]
    items = []
    for entry in entries:
        try:
            envelope = json.loads(entry["envelope"])
        except (TypeError, ValueError):
            envelope = None
        items.append({**entry, "envelope": envelope})
    return {"items": items, "total": len(items)}


# ------------------------------------------------------------------- health


@router.get("/healthz", include_in_schema=False)
@router.get("/health/live", include_in_schema=False)
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
@router.get("/health/ready", include_in_schema=False)
def health_ready(request: Request) -> Any:
    try:
        request.app.state.storage.ensure_ready()
    except (sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}
