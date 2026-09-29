"""Приём push-событий из CRM (api-contract §13.2).

Каждый запрос: проверка HMAC `X-Webhook-Signature` → валидация конверта →
идемпотентность по `event_id` (повтор возвращает тот же ответ с
`duplicate: true`, побочные эффекты не выполняются).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .common import error_response
from .db import Storage
from .schemas import IntegrationEnvelope
from .security import verify_signature

router = APIRouter(prefix="/api/v1", tags=["Приём из CRM"])

#: Обработчик побочного эффекта: возвращает доп. поля ответа либо готовую ошибку.
ApplyFn = Callable[[Storage, IntegrationEnvelope, str], dict[str, Any] | JSONResponse]


async def _receive(request: Request, expected_type: str, apply: ApplyFn) -> JSONResponse:
    raw = await request.body()
    settings = request.app.state.settings
    signature = request.headers.get("X-Webhook-Signature")
    if not verify_signature(settings.webhook_secret, raw, signature):
        return error_response(
            401, "unauthorized", "Неверная или отсутствующая подпись X-Webhook-Signature"
        )
    try:
        envelope = IntegrationEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        details = [
            {
                "field": ".".join(str(part) for part in err["loc"]),
                "code": "invalid",
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return error_response(
            400, "validation_error", "Некорректный конверт IntegrationEvent", details
        )
    if envelope.event_type != expected_type:
        return error_response(
            400,
            "validation_error",
            f"Эндпоинт принимает событие {expected_type}, получено {envelope.event_type}",
        )
    storage: Storage = request.app.state.storage
    event_id = str(envelope.event_id)
    with storage.lock:
        stored = storage.get_inbound_response(event_id)
        if stored is not None:
            stored["duplicate"] = True
            return JSONResponse(stored)
        result = apply(storage, envelope, raw.decode("utf-8", errors="replace"))
        if isinstance(result, JSONResponse):
            return result
        response = {"accepted": True, "duplicate": False, **result, "received_event_id": event_id}
        storage.save_inbound(
            event_id=event_id,
            event_type=envelope.event_type,
            occurred_at=envelope.occurred_at.isoformat(),
            source=envelope.source,
            envelope_raw=raw.decode("utf-8", errors="replace"),
            response=response,
        )
    return JSONResponse(response)


def _apply_enrollment(
    storage: Storage, envelope: IntegrationEnvelope, raw: str
) -> dict[str, Any] | JSONResponse:
    crm_request_id = envelope.correlation.crm_request_id
    if not crm_request_id:
        db_keys = (envelope.block("links") or {}).get("db_keys") or {}
        crm_request_id = db_keys.get("request_id")
    if not crm_request_id:
        return error_response(
            422,
            "unprocessable",
            "В событии не передан crm_request_id (correlation или links.db_keys.request_id)",
        )
    enrollment_id, _created = storage.upsert_enrollment(
        crm_request_id=str(crm_request_id), envelope_raw=raw
    )
    return {"lms_enrollment_id": enrollment_id}


def _apply_program(
    storage: Storage, envelope: IntegrationEnvelope, raw: str
) -> dict[str, Any] | JSONResponse:
    program = envelope.block("program")
    if not program or not program.get("crm_program_id"):
        return error_response(422, "unprocessable", "В payload.program отсутствует crm_program_id")
    course_id = storage.upsert_program(
        crm_program_id=str(program["crm_program_id"]),
        lms_course_id=program.get("lms_course_id"),
        name=program.get("name"),
        envelope_raw=raw,
    )
    return {"lms_course_id": course_id}


@router.post("/enrollments", summary="crm.request.handed_over — заявка передана в обучение")
async def receive_enrollment(request: Request) -> JSONResponse:
    return await _receive(request, "crm.request.handed_over", _apply_enrollment)


@router.post("/programs/upsert", summary="crm.program.upserted — синхронизация программы")
async def receive_program(request: Request) -> JSONResponse:
    return await _receive(request, "crm.program.upserted", _apply_program)
