"""Безопасность входящих вебхуков LMS/CMS (api-contract.md §13.6).

Основной режим: `X-Webhook-Signature: sha256=<hex HMAC-SHA256(secret, raw_body)>`,
секреты per-system. Альтернатива — Bearer сервисного клиента `crm-integration`
(роль `svc_integration`). Replay-защита: `occurred_at` не старше 10 минут ЛИБО
`event_id` уже виден (тогда это корректный duplicate).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.core.errors import unauthorized
from app.core.security import decode_token

REPLAY_WINDOW = timedelta(minutes=10)


def signature_valid(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    """Constant-time сверка подписи `sha256=<hex>` (чистая функция для тестов)."""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header_value[len("sha256="):].lower(), expected)


def occurred_at_fresh(occurred_at: datetime, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return abs(now - occurred_at) <= REPLAY_WINDOW


async def authenticate_webhook(request: Request, raw_body: bytes, secret: str) -> None:
    """HMAC-подпись либо Bearer с ролью svc_integration; иначе 401."""
    signature = request.headers.get("X-Webhook-Signature")
    if signature is not None:
        if signature_valid(secret, raw_body, signature):
            return
        raise unauthorized("Неверная подпись вебхука")
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        claims = await decode_token(authorization[len("Bearer "):])
        if "svc_integration" in claims.roles:
            return
        raise unauthorized("Токен не имеет роли svc_integration")
    raise unauthorized("Вебхук не подписан: требуется X-Webhook-Signature или Bearer")
