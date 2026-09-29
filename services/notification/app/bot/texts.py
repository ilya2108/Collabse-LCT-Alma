"""Русские тексты ответов бота (HTML-разметка Telegram).

Все интерполируемые значения экранируются: данные приходят из CRM, но защита
от HTML-инъекции в разметку — обязанность этого слоя. ПДн студентов/клиентов
в бота не передаются by design (payload формирует backend).
"""
from __future__ import annotations

import html
from typing import Any

from app.templates import ru_days

ROLE_LABELS: dict[str, str] = {
    "admin": "Администратор",
    "head_kam": "Руководитель КАМов",
    "kam": "КАМ",
    "observer": "Наблюдатель",
}

HELP_TEXT = (
    "<b>Команды бота «Альмы»</b>\n"
    "/my — мои открытые заявки\n"
    "/stuck — зависшие заявки\n"
    "/summary — сводка по воронке\n"
    "/inbox — непрочитанные уведомления\n"
    "/link &lt;код&gt; — привязать аккаунт «Альмы»\n"
    "/unlink — отвязать Telegram\n"
    "/help — эта справка\n\n"
    "Данные отдаются строго по роли привязанного пользователя «Альмы»."
)

START_TEXT = (
    "Здравствуйте! Это бот уведомлений «Альмы» — CRM взаимодействия с вузами.\n\n"
    "Чтобы получать уведомления, привяжите аккаунт: в «Альме» откройте "
    "<b>Настройки → Уведомления → Привязать Telegram</b>, получите 6-значный код "
    "и отправьте его сюда командой /link &lt;код&gt; (или перейдите по deep-link из «Альмы»).\n\n"
    + HELP_TEXT
)

NOT_LINKED_TEXT = (
    "Ваш Telegram не привязан к «Альме».\n"
    "Получите код в «Альме» (<b>Настройки → Уведомления → Привязать Telegram</b>) "
    "и отправьте: /link &lt;код&gt;."
)

CODE_FORMAT_TEXT = "Код должен состоять из 6 цифр. Пример: /link 482913"
CODE_INVALID_TEXT = (
    "Код неверен или истёк (коды действуют 10 минут).\n"
    "Получите новый в «Альме»: <b>Настройки → Уведомления → Привязать Telegram</b>."
)
CODE_CONFLICT_TEXT = (
    "Этот Telegram уже привязан к другому пользователю «Альмы».\n"
    "Сначала отвяжите его командой /unlink."
)
BACKEND_UNAVAILABLE_TEXT = "«Альма» временно недоступна. Попробуйте ещё раз через минуту."
UNLINKED_TEXT = "Готово: Telegram отвязан от «Альмы». Уведомления сюда больше не приходят."
RATE_LIMITED_TEXT = "Слишком много запросов. Подождите минуту и повторите."
UNKNOWN_COMMAND_TEXT = "Не понимаю эту команду. Справка — /help"
NO_OPEN_REQUESTS_TEXT = "У вас нет открытых заявок. 👍"
NO_STUCK_REQUESTS_TEXT = "Зависших заявок нет. Отличная работа! ✅"
NO_UNREAD_TEXT = "Непрочитанных уведомлений нет. 📭"


def role_labels(roles: list[str]) -> str:
    labels = [ROLE_LABELS.get(role, role) for role in roles if role in ROLE_LABELS] or roles
    return ", ".join(labels) if labels else "без роли"


def greeting_text(tg_first_name: str | None, full_name: str, roles: list[str]) -> str:
    hello = f"Готово, {html.escape(tg_first_name)}!" if tg_first_name else "Готово!"
    return (
        f"{hello} Telegram привязан к аккаунту «Альмы».\n"
        f"Вы — <b>{html.escape(full_name)}</b> ({html.escape(role_labels(roles))}).\n"
        f"Уведомления «Альмы» будут приходить сюда.\n\n"
        f"Команды: /my, /stuck, /summary, /help"
    )


def backend_error_text(message: str) -> str:
    return f"Ошибка «Альмы»: {html.escape(message)}"


# --- Форматирование карточек заявок ---------------------------------------------------

def _status_name(item: dict[str, Any]) -> str:
    status = item.get("status")
    if isinstance(status, dict):
        return str(status.get("name") or status.get("code") or "—")
    return str(status or "—")


def _party_name(item: dict[str, Any]) -> str | None:
    """Вуз (B2B) или контрагент (B2C), если backend положил их в сокращённую карточку."""
    for key in ("university", "counterparty"):
        value = item.get(key)
        if isinstance(value, dict) and value.get("name"):
            return str(value["name"])
        if isinstance(value, str) and value:
            return value
    for key in ("university_name", "counterparty_name"):
        if item.get(key):
            return str(item[key])
    return None


def _days_on_stage(item: dict[str, Any]) -> int | None:
    for key in ("stuck_days", "days_on_stage"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def format_request_line(item: dict[str, Any], *, escalation_mark: bool = False) -> str:
    title = html.escape(str(item.get("title") or "Без названия"))
    parts = [f"<b>{title}</b>"]
    party = _party_name(item)
    if party:
        parts.append(html.escape(party))
    parts.append(f"этап «{html.escape(_status_name(item))}»")
    days = _days_on_stage(item)
    if days is not None:
        parts.append(f"{days} {ru_days(days)} на этапе")
    line = "• " + " — ".join(parts)
    if escalation_mark and _is_escalated(item):
        line += "\n  ⚠ эскалировано руководителю"
    return line


def _is_escalated(item: dict[str, Any]) -> bool:
    if item.get("escalated"):
        return True
    days = _days_on_stage(item)
    threshold = item.get("threshold_days")
    if days is not None and isinstance(threshold, (int, float)) and threshold > 0:
        return days >= 1.5 * threshold
    return False


def my_requests_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return NO_OPEN_REQUESTS_TEXT
    lines = [format_request_line(item) for item in items]
    return "<b>Мои открытые заявки</b>\n" + "\n".join(lines)


def stuck_requests_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return NO_STUCK_REQUESTS_TEXT
    lines = [format_request_line(item, escalation_mark=True) for item in items]
    return "🔥 <b>Зависшие заявки</b>\n" + "\n".join(lines)


def inbox_text(items: list[dict[str, Any]]) -> str:
    """Лента непрочитанных уведомлений §9.1 (команда /inbox): тема + первая строка текста."""
    if not items:
        return NO_UNREAD_TEXT
    lines = []
    for item in items:
        subject = str(item.get("subject") or item.get("title") or "Уведомление")
        body = str(item.get("text") or item.get("body") or "").strip().splitlines()
        line = f"• <b>{html.escape(subject)}</b>"
        if body and body[0] and body[0] != subject:
            line += f" — {html.escape(body[0])}"
        lines.append(line)
    return "📥 <b>Непрочитанные уведомления</b>\n" + "\n".join(lines)


def summary_text(data: dict[str, Any]) -> str:
    roles = data.get("role_labels") or []
    role_line = f"\nРоль: {html.escape(', '.join(map(str, roles)))}" if roles else ""
    return (
        "📊 <b>Сводка по воронке</b>\n"
        f"Активных заявок: {data.get('active_requests', 0)}\n"
        f"Зависших: {data.get('stuck', 0)}\n"
        f"Ждут согласования: {data.get('waiting_approval', 0)}"
        f"{role_line}"
    )
