"""Маскирование ПДн блока lead в уже записанных integration_event.payload.

До этого фикса `_store_inbound` сохранял конверт cms.lead.created целиком —
включая открытые full_name/email/phone физлица — в JSONB `integration_event.payload`
в обход схемы шифрования (data-model.md §8: ПДн в БД только шифртекстом,
выдачи — с маскированием §8.5). Новые записи маскируются при приёме
(app.modules.integrations.service.mask_lead_payload); эта миграция чистит
уже накопленные строки. Маски скопированы из app.core.pii, чтобы миграция
оставалась самодостаточной. Даунгрейд — no-op: открытые ПДн восстановлению
не подлежат намеренно.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SPACES_RE = re.compile(r"\s+")


def _mask_word(word: str) -> str:
    if len(word) <= 2:
        return f"{word[:1]}***"
    return f"{word[0]}***{word[-1]}"


def _mask_full_name(full_name: str) -> str:
    parts = _SPACES_RE.sub(" ", full_name.strip()).split(" ")
    if not parts or not parts[0]:
        return ""
    masked = _mask_word(parts[0])
    initials = "".join(f"{p[0].upper()}." for p in parts[1:3] if p)
    return f"{masked} {initials}".strip()


def _mask_email(email: str) -> str:
    value = email.strip()
    if "@" not in value:
        return _mask_word(value)
    local, _, domain = value.partition("@")
    return f"{_mask_word(local)}@{domain}"


def _mask_phone(phone: str) -> str:
    digits = [c for c in phone if c.isdigit()]
    if len(digits) < 4:
        return "***"
    return f"+{digits[0]} *** *** {''.join(digits[-4:-2])}-{''.join(digits[-2:])}"


_MASKS = {"full_name": _mask_full_name, "email": _mask_email, "phone": _mask_phone}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, payload FROM integration_event "
            "WHERE payload #> '{payload,lead}' IS NOT NULL"
        )
    ).fetchall()
    for event_pk, payload in rows:
        if isinstance(payload, str):  # драйвер может отдать jsonb строкой
            payload = json.loads(payload)
        lead = payload.get("payload", {}).get("lead")
        if not isinstance(lead, dict):
            continue
        changed = False
        for field, mask in _MASKS.items():
            value = lead.get(field)
            # уже маскированные значения содержат '***' — повторный прогон идемпотентен
            if isinstance(value, str) and value and "***" not in value:
                lead[field] = mask(value)
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE integration_event SET payload = CAST(:payload AS jsonb) "
                    "WHERE id = :id"
                ),
                {"payload": json.dumps(payload, ensure_ascii=False), "id": event_pk},
            )


def downgrade() -> None:
    # Открытые ПДн из журнала не восстанавливаются — это и есть цель миграции.
    pass
