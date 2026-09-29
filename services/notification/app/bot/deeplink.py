"""Deep-link привязки Telegram: https://t.me/<bot>?start=<code> (api-contract.md §9.2).

Ссылку пользователю показывает backend (POST /notifications/telegram/link-code),
но формат — часть контракта бота, поэтому канонический генератор живёт здесь:
им пользуются тесты сервиса и GET /api/v1/stats (шаблон ссылки для демо).
"""
from __future__ import annotations

import re

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")  # правила username Bot API


def build_deep_link(bot_username: str, code: str) -> str:
    """Собрать deep-link `/start <code>`; username принимается и с ведущим @."""
    username = bot_username.lstrip("@")
    if not _USERNAME_RE.match(username):
        raise ValueError(f"Некорректный username бота: {bot_username!r}")
    return f"https://t.me/{username}?start={code}"
