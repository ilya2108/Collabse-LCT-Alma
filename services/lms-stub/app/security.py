"""HMAC-подпись вебхуков: `X-Webhook-Signature: sha256=<hex>` (api-contract §13.6).

Оба направления симметричны: push CRM → lms-stub и вебхуки lms-stub → CRM
подписываются одним per-system секретом `LMS_WEBHOOK_SECRET`.
"""
from __future__ import annotations

import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


def sign_body(secret: str, body: bytes) -> str:
    """Подпись сырых байтов тела запроса."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    """Проверка заголовка X-Webhook-Signature; сравнение — constant-time."""
    if not header_value or not header_value.startswith(SIGNATURE_PREFIX):
        return False
    return hmac.compare_digest(sign_body(secret, body), header_value)
