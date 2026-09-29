"""Тексты нотификаций (Jinja2, русские) — реестр шаблонов api-contract.md §9.4.

Backend обычно присылает готовый body (`text`); если вместо текста пришли только
`template` + `meta` — сообщение рендерится здесь. Плейсхолдеры соответствуют payload
из конверта §9.4 (title, status, stuck_days, threshold_days, url и т.п.).
"""
from __future__ import annotations

from typing import Any

from jinja2 import ChainableUndefined, Environment


def ru_plural(n: Any, one: str, few: str, many: str) -> str:
    """Русская плюрализация: 1 день / 2 дня / 5 дней."""
    try:
        value = abs(int(n))
    except (TypeError, ValueError):
        return many
    if value % 10 == 1 and value % 100 != 11:
        return one
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return few
    return many


def ru_days(n: Any) -> str:
    return ru_plural(n, "день", "дня", "дней")


_env = Environment(undefined=ChainableUndefined, autoescape=False, trim_blocks=True)
_env.filters["ru_days"] = ru_days

_TEMPLATE_SOURCES: dict[str, str] = {
    "request_stuck": (
        "{% if escalation %}⚠ Эскалация: заявка вашего сотрудника зависла.\n{% endif %}"
        "🔥 Заявка «{{ title }}» висит на этапе «{{ status }}» уже "
        "{{ stuck_days }} {{ stuck_days|ru_days }}"
        "{% if threshold_days %} (порог — {{ threshold_days }} {{ threshold_days|ru_days }})"
        "{% endif %}."
    ),
    "request_transitioned": (
        "Заявка «{{ title }}» переведена на этап «{{ status }}»"
        "{% if actor %} — {{ actor }}{% endif %}."
        "{% if comment %}\nКомментарий: {{ comment }}{% endif %}"
    ),
    "request_assigned": (
        "Вам назначена заявка «{{ title }}» (этап «{{ status }}»)."
    ),
    "comment_added": (
        "💬 Новый комментарий к заявке «{{ title }}»"
        "{% if author %} от {{ author }}{% endif %}."
        "{% if comment %}\n{{ comment }}{% endif %}"
    ),
    "workflow_change_requested": (
        "Пакет изменений схемы процесса «{{ workflow }}» ждёт согласования администратором."
    ),
    "workflow_changed": (
        "Схема процесса «{{ workflow }}» обновлена. "
        "Заявки автоматически сопоставлены с новой схемой."
    ),
    "talent_pool_added": (
        "🎓 Студент {{ student }} добавлен в пул талантов."
    ),
    "lead_received": (
        "📥 Новый лид с сайта: «{{ title }}». Требуется распределение ответственного."
    ),
    "test_message": (
        "✅ Проверка связи: канал «{{ channel }}» подключён и работает."
    ),
}

_TEMPLATES = {name: _env.from_string(source) for name, source in _TEMPLATE_SOURCES.items()}


class UnknownTemplateError(KeyError):
    pass


def known_templates() -> list[str]:
    return sorted(_TEMPLATES)


def render_template(name: str, context: dict[str, Any]) -> str:
    template = _TEMPLATES.get(name)
    if template is None:
        raise UnknownTemplateError(name)
    return template.render(**context).strip()
