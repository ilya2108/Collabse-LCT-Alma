"""Запись в append-only `audit_log` — в той же транзакции, что и бизнес-изменение."""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.service import AuditLog


def client_ip(request: Request | None) -> str | None:
    """IP клиента для колонки inet; невалидные значения (прокси-мусор в
    X-Forwarded-For, тестовый host) не должны ронять бизнес-транзакцию.

    X-Forwarded-For — клиентский заголовок и подделывается тривиально, поэтому
    он учитывается только за доверенным прокси (TRUSTED_PROXY / контур k8s,
    где ingress-nginx перезаписывает заголовок реальным адресом), и берётся
    ПОСЛЕДНИЙ элемент — ближайший к доверенному hop. Иначе — request.client.host.
    """
    if request is None or request.client is None:
        return None
    candidate = request.client.host
    if get_settings().trust_forwarded_for:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            candidate = forwarded.split(",")[-1].strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


async def write_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Добавляет запись аудита в текущую сессию (commit — на вызывающей стороне).

    Внимание: значения ПДн в before/after обязаны быть замаскированы вызывающим кодом
    до записи (data-model.md §8.5).
    """
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            ip=ip,
        )
    )
