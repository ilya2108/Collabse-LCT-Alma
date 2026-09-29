"""Форматирование: шаблоны §9.4, плюрализация, HTML Telegram, карточки для бота."""
from __future__ import annotations

import pytest

from app.bot import texts
from app.channels.telegram import build_crm_url, build_html_text, build_reply_markup
from app.templates import UnknownTemplateError, render_template, ru_days


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "день"), (2, "дня"), (5, "дней"), (11, "дней"), (14, "дней"), (21, "день"),
     (22, "дня"), (114, "дней")],
)
def test_ru_days_pluralization(n: int, expected: str) -> None:
    assert ru_days(n) == expected


def test_request_stuck_template() -> None:
    text = render_template(
        "request_stuck",
        {"title": "МФТИ — ДПО", "status": "Переговоры", "stuck_days": 15,
         "threshold_days": 14},
    )
    assert text == (
        "🔥 Заявка «МФТИ — ДПО» висит на этапе «Переговоры» уже 15 дней (порог — 14 дней)."
    )


def test_request_stuck_escalation_variant() -> None:
    text = render_template(
        "request_stuck",
        {"title": "T", "status": "S", "stuck_days": 21, "escalation": True},
    )
    assert text.startswith("⚠ Эскалация:")
    assert "21 день" in text


def test_test_message_template_and_unknown() -> None:
    assert "Telegram" in render_template("test_message", {"channel": "Telegram"})
    with pytest.raises(UnknownTemplateError):
        render_template("nope", {})


def test_transition_template_tolerates_missing_fields() -> None:
    text = render_template("request_transitioned", {"title": "X", "status": "Договор"})
    assert text == "Заявка «X» переведена на этап «Договор»."


def test_telegram_html_escapes_injection() -> None:
    html_text = build_html_text("<b>Тема</b>", "Текст <script>alert(1)</script>")
    assert "<script>" not in html_text
    assert html_text.startswith("<b>&lt;b&gt;Тема&lt;/b&gt;</b>\n\n")


def test_telegram_crm_button_url() -> None:
    assert build_crm_url("http://crm.local", "/requests/rq-1") == "http://crm.local/requests/rq-1"
    assert build_crm_url("http://crm.local/", "requests/rq-1") == "http://crm.local/requests/rq-1"
    assert build_crm_url("http://crm.local", "https://other/x") == "https://other/x"

    markup = build_reply_markup("http://crm.local", {"url": "/requests/rq-1"})
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.text == "Открыть в «Альме»"
    assert button.url == "http://crm.local/requests/rq-1"
    assert build_reply_markup("http://crm.local", {}) is None


def test_request_line_formatting() -> None:
    line = texts.format_request_line(
        {
            "id": "rq-1",
            "title": "МФТИ — ДПО Data Science",
            "university": {"name": "МФТИ"},
            "status": {"id": "st", "code": "negotiation", "name": "Переговоры"},
            "stuck_days": 6,
        }
    )
    assert "МФТИ — ДПО Data Science" in line
    assert "этап «Переговоры»" in line
    assert "6 дней на этапе" in line


def test_stuck_list_escalation_mark() -> None:
    text = texts.stuck_requests_text(
        [
            {"title": "A", "status": "Переговоры", "stuck_days": 21, "threshold_days": 14},
            {"title": "B", "status": "Переговоры", "stuck_days": 15, "threshold_days": 14},
        ]
    )
    assert text.count("⚠ эскалировано руководителю") == 1  # только 21 >= 1.5 * 14
    assert text.startswith("🔥")


def test_summary_text() -> None:
    text = texts.summary_text(
        {"active_requests": 12, "stuck": 2, "waiting_approval": 1, "role_labels": ["KAM"]}
    )
    assert "Активных заявок: 12" in text
    assert "Зависших: 2" in text
    assert "Роль: KAM" in text


def test_greeting_text_roles() -> None:
    text = texts.greeting_text("Пётр", "Иванов Пётр Сергеевич", ["kam"])
    assert "Иванов Пётр Сергеевич" in text
    assert "КАМ" in text
