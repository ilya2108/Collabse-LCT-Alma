"""Хэндлеры команд бота (aiogram 3, Router).

Зависимости (`backend: BackendClient`) инжектируются диспетчером из workflow_data —
хэндлеры остаются чистыми функциями и тестируются с моками напрямую.
"""
from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from app.backend_client import (
    BackendApiError,
    BackendClient,
    BackendUnavailableError,
    NotLinkedError,
)
from app.bot import texts
from app.metrics import Metrics

logger = logging.getLogger(__name__)

router = Router(name="crm-bot")

_CODE_RE = re.compile(r"^\d{6}$")


async def _bind(
    message: Message,
    backend: BackendClient,
    raw_code: str,
    metrics: Metrics | None = None,
) -> None:
    code = raw_code.strip()
    if not _CODE_RE.match(code):
        await message.answer(texts.CODE_FORMAT_TEXT)
        return
    tg_user = message.from_user
    try:
        result = await backend.bind(
            code=code,
            chat_id=message.chat.id,
            tg_username=tg_user.username if tg_user else None,
        )
    except BackendUnavailableError:
        await message.answer(texts.BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendApiError as exc:
        if exc.status_code == 404:
            await message.answer(texts.CODE_INVALID_TEXT)
        elif exc.status_code == 409:
            await message.answer(texts.CODE_CONFLICT_TEXT)
        else:
            await message.answer(texts.backend_error_text(exc.message))
        return
    logger.info(
        "Telegram привязан к пользователю CRM",
        extra={"chat_id": message.chat.id, "user_id": result.get("user_id")},
    )
    if metrics is not None:
        metrics.mark_binding_created()
    await message.answer(
        texts.greeting_text(
            tg_first_name=tg_user.first_name if tg_user else None,
            full_name=str(result.get("full_name", "")),
            roles=list(result.get("roles", [])),
        )
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    backend: BackendClient,
    metrics: Metrics | None = None,
) -> None:
    """`/start <code>` по deep-link из CRM либо просто `/start` — приветствие."""
    if command.args:
        await _bind(message, backend, command.args, metrics)
    else:
        await message.answer(texts.START_TEXT)


@router.message(Command("link"))
async def cmd_link(
    message: Message,
    command: CommandObject,
    backend: BackendClient,
    metrics: Metrics | None = None,
) -> None:
    if not command.args:
        await message.answer(texts.CODE_FORMAT_TEXT)
        return
    await _bind(message, backend, command.args, metrics)


@router.message(Command("my"))
async def cmd_my(message: Message, backend: BackendClient) -> None:
    try:
        items = await backend.requests(message.chat.id, stuck=False, limit=10)
    except NotLinkedError:
        await message.answer(texts.NOT_LINKED_TEXT)
        return
    except BackendUnavailableError:
        await message.answer(texts.BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendApiError as exc:
        await message.answer(texts.backend_error_text(exc.message))
        return
    await message.answer(texts.my_requests_text(items))


@router.message(Command("stuck"))
async def cmd_stuck(message: Message, backend: BackendClient) -> None:
    try:
        items = await backend.requests(message.chat.id, stuck=True, limit=10)
    except NotLinkedError:
        await message.answer(texts.NOT_LINKED_TEXT)
        return
    except BackendUnavailableError:
        await message.answer(texts.BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendApiError as exc:
        await message.answer(texts.backend_error_text(exc.message))
        return
    await message.answer(texts.stuck_requests_text(items))


@router.message(Command("summary", "status"))
async def cmd_summary(message: Message, backend: BackendClient) -> None:
    """`/summary` (алиас `/status` из api-contract §9.3) — сводка по воронке."""
    try:
        data = await backend.summary(message.chat.id)
    except NotLinkedError:
        await message.answer(texts.NOT_LINKED_TEXT)
        return
    except BackendUnavailableError:
        await message.answer(texts.BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendApiError as exc:
        await message.answer(texts.backend_error_text(exc.message))
        return
    await message.answer(texts.summary_text(data))


@router.message(Command("inbox"))
async def cmd_inbox(message: Message, backend: BackendClient) -> None:
    """`/inbox` — непрочитанные уведомления (лента §9.1, api-contract §9.3)."""
    try:
        items = await backend.notifications(message.chat.id, unread=True, limit=10)
    except NotLinkedError:
        await message.answer(texts.NOT_LINKED_TEXT)
        return
    except BackendUnavailableError:
        await message.answer(texts.BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendApiError as exc:
        await message.answer(texts.backend_error_text(exc.message))
        return
    await message.answer(texts.inbox_text(items))


@router.message(Command("unlink"))
async def cmd_unlink(
    message: Message, backend: BackendClient, metrics: Metrics | None = None
) -> None:
    try:
        await backend.unbind(message.chat.id)
    except NotLinkedError:
        await message.answer(texts.NOT_LINKED_TEXT)
        return
    except BackendUnavailableError:
        await message.answer(texts.BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendApiError as exc:
        await message.answer(texts.backend_error_text(exc.message))
        return
    logger.info("Telegram отвязан", extra={"chat_id": message.chat.id})
    if metrics is not None:
        metrics.mark_binding_removed()
    await message.answer(texts.UNLINKED_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP_TEXT)


@router.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer(texts.UNKNOWN_COMMAND_TEXT)
