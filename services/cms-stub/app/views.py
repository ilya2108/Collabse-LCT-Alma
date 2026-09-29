"""UI «сайта», демо-форма лида и служебные эндпоинты cms-stub."""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .db import Storage
from .ui import render_page

router = APIRouter()


def _redirect(message: str, *, kind: str = "ok") -> RedirectResponse:
    return RedirectResponse(
        url=f"/?flash={quote(message)}&flash_kind={kind}", status_code=303
    )


# ------------------------------------------------------------------- страница


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(
    request: Request, flash: str | None = None, flash_kind: str = "ok"
) -> HTMLResponse:
    storage: Storage = request.app.state.storage
    return HTMLResponse(
        render_page(
            crm_webhook_url=request.app.state.settings.crm_webhook_url,
            counters=storage.counters(),
            catalog=storage.list_catalog(),
            leads=storage.list_leads(),
            journal=storage.journal(),
            flash=flash,
            flash_kind=flash_kind,
        )
    )


# ------------------------------------------------------------ демо-форма лида


@router.post("/demo/leads", include_in_schema=False)
def submit_lead(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    client_kind: str = Form("person"),
    company_name: str = Form(""),
    inn: str = Form(""),
    program_id: str = Form(""),
    comment: str = Form(""),
) -> RedirectResponse:
    """Сабмит демо-формы: cms-stub шлёт подписанный вебхук cms.lead.created в CRM."""
    full_name = full_name.strip()
    email = email.strip()
    if not full_name or not email:
        return _redirect("Заполните обязательные поля: ФИО и email", kind="err")
    if client_kind not in ("person", "company"):
        client_kind = "person"

    storage: Storage = request.app.state.storage
    with storage.lock:
        item: dict[str, Any] | None = None
        if program_id:
            item = storage.get_catalog_item(program_id)
            if item is None:
                return _redirect("Выбранная программа не найдена в каталоге", kind="err")

        program_block: dict[str, Any] | None = None
        product_block: dict[str, Any] | None = None
        db_keys: dict[str, Any] = {}
        if item is not None:
            program_block = {
                "crm_program_id": item["crm_program_id"],
                "name": item["name"],
                "cms_external_id": item["cms_external_id"],
                "lms_course_id": None,
            }
            if item.get("product"):
                try:
                    parsed = json.loads(item["product"])
                    product_block = parsed if isinstance(parsed, dict) else None
                except (TypeError, ValueError):
                    product_block = None
            db_keys = {"program_id": item["crm_program_id"]}

        cms_lead_id = storage.reserve_lead_id()
        payload: dict[str, Any] = {
            "status": {"code": "new", "name": "Новая заявка с сайта"},
            "responsible": None,
            "university": None,
            "program": program_block,
            "product": product_block,
            "links": {"db_keys": db_keys, "s3_keys": []},
            "lead": {
                "client_kind": client_kind,
                "full_name": full_name,
                "email": email,
                "phone": phone.strip() or None,
                "company_name": company_name.strip() or None,
                "inn": inn.strip() or None,
                "comment": comment.strip() or None,
            },
        }
        event_id = request.app.state.relay.enqueue(
            event_type="cms.lead.created",
            correlation={
                "crm_request_id": None,
                "lms_enrollment_id": None,
                "cms_lead_id": cms_lead_id,
            },
            payload=payload,
            local_ref=cms_lead_id,
        )
        storage.create_lead(
            full_name=full_name,
            email=email,
            phone=phone.strip() or None,
            client_kind=client_kind,
            company_name=company_name.strip() or None,
            inn=inn.strip() or None,
            comment=comment.strip() or None,
            crm_program_id=item["crm_program_id"] if item else None,
            program_name=item["name"] if item else None,
            event_id=event_id,
        )
    return _redirect(
        f"Заявка {cms_lead_id} принята: событие cms.lead.created поставлено "
        "в очередь доставки в CRM"
    )


# ------------------------------------------------------------- JSON-эндпоинты


@router.get("/api/v1/catalog", summary="Опубликованный каталог программ")
def list_catalog(request: Request) -> dict[str, Any]:
    items = []
    for item in request.app.state.storage.list_catalog(published_only=True):
        product = None
        if item.get("product"):
            try:
                product = json.loads(item["product"])
            except (TypeError, ValueError):
                product = None
        items.append(
            {
                "cms_external_id": item["cms_external_id"],
                "crm_program_id": item["crm_program_id"],
                "name": item["name"],
                "product": product,
                "updated_at": item["updated_at"],
            }
        )
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
