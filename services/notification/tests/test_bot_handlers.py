"""Хэндлеры бота: /start с кодом, ошибки привязки, /my, /stuck, /summary, /inbox, /unlink."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from app.backend_client import BackendUnavailableError, NotLinkedError
from app.bot import texts
from app.bot.handlers import (
    cmd_inbox,
    cmd_link,
    cmd_my,
    cmd_start,
    cmd_stuck,
    cmd_summary,
    cmd_unlink,
)
from app.metrics import Metrics


def make_message(chat_id: int = 199401234) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=chat_id, first_name="Пётр", username="ivanov_p"),
        answer=AsyncMock(),
    )


class FakeBackend:
    """Мок BackendClient: программируемые ответы/исключения по методам."""

    def __init__(self, **results: Any) -> None:
        self._results = results
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _resolve(self, name: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((name, kwargs))
        result = self._results.get(name)
        if isinstance(result, Exception):
            raise result
        return result

    async def bind(self, **kwargs: Any) -> dict[str, Any]:
        return self._resolve("bind", kwargs)

    async def unbind(self, chat_id: int) -> None:
        return self._resolve("unbind", {"chat_id": chat_id})

    async def summary(self, chat_id: int) -> dict[str, Any]:
        return self._resolve("summary", {"chat_id": chat_id})

    async def requests(self, chat_id: int, **kwargs: Any) -> list[dict[str, Any]]:
        return self._resolve("requests", {"chat_id": chat_id, **kwargs})

    async def notifications(self, chat_id: int, **kwargs: Any) -> list[dict[str, Any]]:
        return self._resolve("notifications", {"chat_id": chat_id, **kwargs})


def _reply(message: SimpleNamespace) -> str:
    return message.answer.call_args.args[0]


async def test_start_with_code_binds_and_greets() -> None:
    backend = FakeBackend(
        bind={"user_id": "u-1", "full_name": "Иванов Пётр Сергеевич", "roles": ["kam"]}
    )
    message = make_message()
    await cmd_start(message, CommandObject(command="start", args="482913"), backend)

    name, kwargs = backend.calls[0]
    assert name == "bind"
    assert kwargs == {"code": "482913", "chat_id": 199401234, "tg_username": "ivanov_p"}
    reply = _reply(message)
    assert "Иванов Пётр Сергеевич" in reply
    assert "КАМ" in reply


async def test_start_without_code_shows_instructions() -> None:
    message = make_message()
    await cmd_start(message, CommandObject(command="start", args=None), FakeBackend())
    assert _reply(message) == texts.START_TEXT


async def test_link_rejects_malformed_code_without_backend_call() -> None:
    backend = FakeBackend()
    message = make_message()
    await cmd_link(message, CommandObject(command="link", args="12ab"), backend)
    assert backend.calls == []
    assert _reply(message) == texts.CODE_FORMAT_TEXT


async def test_bind_expired_code_hint() -> None:
    backend = FakeBackend(bind=NotLinkedError(404, "not_found", "Код не найден"))
    message = make_message()
    await cmd_link(message, CommandObject(command="link", args="000000"), backend)
    assert _reply(message) == texts.CODE_INVALID_TEXT


async def test_bind_conflict_hint() -> None:
    from app.backend_client import BackendApiError

    backend = FakeBackend(bind=BackendApiError(409, "conflict", "Уже привязан"))
    message = make_message()
    await cmd_link(message, CommandObject(command="link", args="482913"), backend)
    assert _reply(message) == texts.CODE_CONFLICT_TEXT


async def test_my_lists_requests() -> None:
    backend = FakeBackend(
        requests=[
            {"title": "МФТИ — ДПО", "status": {"name": "Переговоры"}, "stuck_days": 6},
        ]
    )
    message = make_message()
    await cmd_my(message, backend)
    assert backend.calls[0][1] == {"chat_id": 199401234, "stuck": False, "limit": 10}
    reply = _reply(message)
    assert "МФТИ — ДПО" in reply
    assert "Переговоры" in reply
    assert "6 дней на этапе" in reply


async def test_my_not_linked_hint() -> None:
    backend = FakeBackend(requests=NotLinkedError(404, "not_found", "не привязан"))
    message = make_message()
    await cmd_my(message, backend)
    assert _reply(message) == texts.NOT_LINKED_TEXT


async def test_stuck_marks_escalation() -> None:
    backend = FakeBackend(
        requests=[{"title": "A", "status": "Переговоры", "stuck_days": 30,
                   "threshold_days": 14}]
    )
    message = make_message()
    await cmd_stuck(message, backend)
    assert backend.calls[0][1]["stuck"] is True
    assert "⚠ эскалировано руководителю" in _reply(message)


async def test_stuck_empty_list() -> None:
    backend = FakeBackend(requests=[])
    message = make_message()
    await cmd_stuck(message, backend)
    assert _reply(message) == texts.NO_STUCK_REQUESTS_TEXT


async def test_summary_output() -> None:
    backend = FakeBackend(
        summary={"active_requests": 12, "stuck": 2, "waiting_approval": 1,
                 "role_labels": ["KAM"]}
    )
    message = make_message()
    await cmd_summary(message, backend)
    assert "Активных заявок: 12" in _reply(message)


async def test_backend_unavailable_reply() -> None:
    backend = FakeBackend(summary=BackendUnavailableError("timeout"))
    message = make_message()
    await cmd_summary(message, backend)
    assert _reply(message) == texts.BACKEND_UNAVAILABLE_TEXT


async def test_unlink() -> None:
    backend = FakeBackend()
    message = make_message()
    await cmd_unlink(message, backend)
    assert backend.calls == [("unbind", {"chat_id": 199401234})]
    assert _reply(message) == texts.UNLINKED_TEXT


async def test_unlink_not_linked_hint() -> None:
    backend = FakeBackend(unbind=NotLinkedError(404, "not_found", "не привязан"))
    message = make_message()
    metrics = Metrics()
    await cmd_unlink(message, backend, metrics)
    assert _reply(message) == texts.NOT_LINKED_TEXT
    assert metrics.bindings_removed == 0  # неуспешная отвязка не считается


async def test_unlink_backend_unavailable() -> None:
    backend = FakeBackend(unbind=BackendUnavailableError("timeout"))
    message = make_message()
    await cmd_unlink(message, backend)
    assert _reply(message) == texts.BACKEND_UNAVAILABLE_TEXT


async def test_bind_and_unlink_update_metrics() -> None:
    metrics = Metrics()
    backend = FakeBackend(bind={"user_id": "u-1", "full_name": "Иванов П.С.", "roles": ["kam"]})
    message = make_message()
    await cmd_start(message, CommandObject(command="start", args="482913"), backend, metrics)
    assert metrics.bindings_created == 1

    await cmd_unlink(message, backend, metrics)
    assert metrics.bindings_removed == 1


async def test_inbox_lists_unread_notifications() -> None:
    backend = FakeBackend(
        notifications=[
            {"subject": "Заявка зависла", "text": "Заявка «МФТИ» висит 15 дней.\nПодробности…"},
            {"title": "Новый комментарий"},
        ]
    )
    message = make_message()
    await cmd_inbox(message, backend)
    assert backend.calls[0] == (
        "notifications",
        {"chat_id": 199401234, "unread": True, "limit": 10},
    )
    reply = _reply(message)
    assert "Непрочитанные уведомления" in reply
    assert "Заявка зависла" in reply
    assert "висит 15 дней" in reply  # только первая строка текста
    assert "Подробности" not in reply
    assert "Новый комментарий" in reply


async def test_inbox_empty() -> None:
    backend = FakeBackend(notifications=[])
    message = make_message()
    await cmd_inbox(message, backend)
    assert _reply(message) == texts.NO_UNREAD_TEXT


async def test_inbox_not_linked_hint() -> None:
    backend = FakeBackend(notifications=NotLinkedError(404, "not_found", "не привязан"))
    message = make_message()
    await cmd_inbox(message, backend)
    assert _reply(message) == texts.NOT_LINKED_TEXT


@pytest.mark.parametrize("code", ["48291", "4829133", "abc123", ""])
async def test_start_rejects_bad_code_formats(code: str) -> None:
    backend = FakeBackend()
    message = make_message()
    await cmd_start(message, CommandObject(command="start", args=code or None), backend)
    assert backend.calls == []
