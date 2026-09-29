"""HTML-страница журнала интеграции lms-stub.

Без CDN и внешних ресурсов: стили и скрипт — inline (закрытый контур).
Это витрина «двусторонности» для экспертов: таблица принятых событий
и кнопки-триггеры жизненного цикла обучения, шлющие вебхуки в CRM.
"""
from __future__ import annotations

import html
from typing import Any

from .common import pretty_json

_STATUS_BADGES = {
    "accepted": ("принято", "ok"),
    "pending": ("в очереди", "wait"),
    "retrying": ("повтор", "wait"),
    "delivered": ("доставлено", "ok"),
    "dead": ("DLQ", "err"),
}

_LIFECYCLE_LABELS = {
    "received": "зачислен",
    "started": "обучается",
    "progress": "прогресс 50%",
    "completed": "обучение завершено",
}

_CSS = """
:root { --bg:#f4f5f7; --card:#ffffff; --ink:#1f2430; --muted:#6b7280; --line:#e5e7eb;
        --accent:#2456d6; --ok:#04785e; --okbg:#d6f5e9; --wait:#8a5a00; --waitbg:#fdf0cd;
        --err:#b42318; --errbg:#fde3e1; }
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       background:var(--bg); color:var(--ink); font-size:14px; }
header { background:#101828; color:#fff; padding:14px 24px; display:flex; flex-wrap:wrap;
         gap:12px 24px; align-items:baseline; }
header h1 { margin:0; font-size:18px; font-weight:600; }
header .sub { color:#98a2b3; font-size:12px; }
main { max-width:1200px; margin:0 auto; padding:16px 24px 48px; }
.counters { display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }
.counter { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:10px 16px; min-width:130px; }
.counter b { display:block; font-size:20px; }
.counter span { color:var(--muted); font-size:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:16px 20px; margin:16px 0; overflow-x:auto; }
.card h2 { margin:0 0 4px; font-size:15px; }
.card .hint { color:var(--muted); font-size:12px; margin:0 0 12px; }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line);
         vertical-align:top; }
th { color:var(--muted); font-weight:500; font-size:12px; white-space:nowrap; }
td.nowrap { white-space:nowrap; }
code { background:#f2f4f7; border-radius:4px; padding:1px 5px; font-size:12px; }
.badge { display:inline-block; border-radius:999px; padding:2px 10px; font-size:12px;
         white-space:nowrap; }
.badge.ok { color:var(--ok); background:var(--okbg); }
.badge.wait { color:var(--wait); background:var(--waitbg); }
.badge.err { color:var(--err); background:var(--errbg); }
.dir { font-weight:600; white-space:nowrap; }
.dir.in { color:var(--accent); }
.dir.out { color:var(--ok); }
button { font:inherit; font-size:12px; border:1px solid var(--line); background:#fff;
         border-radius:8px; padding:5px 10px; margin:2px 4px 2px 0; cursor:pointer; }
button:hover { border-color:var(--accent); color:var(--accent); }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.primary:hover { opacity:.9; color:#fff; }
details summary { cursor:pointer; color:var(--accent); font-size:12px; }
details pre { background:#101828; color:#d5e0f5; border-radius:8px; padding:12px;
              font-size:12px; line-height:1.5; overflow-x:auto; max-width:640px; }
.flash { background:var(--okbg); color:var(--ok); border:1px solid #9fdec8;
         border-radius:10px; padding:10px 16px; margin:16px 0; }
.empty { color:var(--muted); padding:14px 0; }
footer { color:var(--muted); font-size:12px; margin-top:24px; line-height:1.7; }
.error-text { color:var(--err); font-size:12px; }
"""

# Автообновление раз в 15 секунд — но не под руками пользователя:
# пропускаем перезагрузку, если фокус в форме или открыт просмотр JSON.
_JS = """
<script>
setInterval(function () {
  var el = document.activeElement;
  var typing = el && ["INPUT", "TEXTAREA", "SELECT"].indexOf(el.tagName) !== -1;
  var reading = document.querySelector("details[open]") !== null;
  if (!typing && !reading) { location.reload(); }
}, 15000);
</script>
"""


def _e(value: Any) -> str:
    """HTML-экранирование; None и пустые значения → «—»."""
    if value is None or value == "":
        return "—"
    return html.escape(str(value), quote=True)


def _json_details(raw: str) -> str:
    return (
        "<details><summary>JSON</summary><pre>"
        + html.escape(pretty_json(raw))
        + "</pre></details>"
    )


def _badge(status: str, attempts: int | None = None) -> str:
    label, kind = _STATUS_BADGES.get(status, (status, "wait"))
    if status == "retrying" and attempts:
        label = f"повтор ({attempts})"
    return f'<span class="badge {kind}">{html.escape(label)}</span>'


def _journal_rows(journal: list[dict[str, Any]]) -> str:
    if not journal:
        return '<div class="empty">Событий пока нет — отправьте push из CRM.</div>'
    rows = []
    for entry in journal:
        direction = (
            '<span class="dir in">← из CRM</span>'
            if entry["direction"] == "in"
            else '<span class="dir out">→ в CRM</span>'
        )
        error_note = (
            f'<div class="error-text">{_e(entry["last_error"])}</div>'
            if entry.get("last_error")
            else ""
        )
        rows.append(
            "<tr>"
            f"<td class='nowrap'>{direction}</td>"
            f"<td class='nowrap'>{_e(entry['at'])}</td>"
            f"<td><code>{_e(entry['event_type'])}</code></td>"
            f"<td><code>{_e(entry['event_id'])}</code></td>"
            f"<td>{_badge(entry['status'], entry.get('attempts'))}{error_note}</td>"
            f"<td>{_json_details(entry['envelope'])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Направление</th><th>Время</th><th>Тип события</th>"
        "<th>event_id</th><th>Статус</th><th>Тело</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _enrollment_rows(enrollments: list[dict[str, Any]]) -> str:
    if not enrollments:
        return (
            '<div class="empty">Зачислений нет. Переведите заявку CRM в статус с флагом '
            "«передача в LMS» — событие crm.request.handed_over появится здесь.</div>"
        )
    rows = []
    for item in enrollments:
        lifecycle = _LIFECYCLE_LABELS.get(item["lifecycle"], item["lifecycle"])
        actions = (
            f'<form method="post" action="/demo/enrollments/{_e(item["lms_enrollment_id"])}'
            '/lifecycle">'
            '<button class="primary" name="kind" value="started">Студент начал обучение</button>'
            '<button name="kind" value="progress">Прогресс 50%</button>'
            '<button name="kind" value="completed">Завершил обучение</button>'
            "</form>"
        )
        rows.append(
            "<tr>"
            f"<td class='nowrap'><code>{_e(item['lms_enrollment_id'])}</code></td>"
            f"<td class='nowrap'><code>{_e(item['crm_request_id'])}</code></td>"
            f"<td>{_e(item.get('program_name'))}</td>"
            f"<td>{_e(item.get('university_name'))}</td>"
            f"<td class='nowrap'><span class='badge ok'>{_e(lifecycle)}</span></td>"
            f"<td>{actions}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Зачисление</th><th>Заявка CRM</th><th>Программа</th>"
        "<th>Вуз</th><th>Состояние</th><th>Триггеры демо → вебхук в CRM</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _program_rows(programs: list[dict[str, Any]]) -> str:
    if not programs:
        return '<div class="empty">Программы из CRM ещё не синхронизировались.</div>'
    rows = [
        "<tr>"
        f"<td class='nowrap'><code>{_e(item['lms_course_id'])}</code></td>"
        f"<td>{_e(item['name'])}</td>"
        f"<td class='nowrap'><code>{_e(item['crm_program_id'])}</code></td>"
        f"<td class='nowrap'>{_e(item['updated_at'])}</td>"
        "</tr>"
        for item in programs
    ]
    return (
        "<table><thead><tr><th>Курс LMS</th><th>Название</th><th>Программа CRM</th>"
        "<th>Обновлено</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_page(
    *,
    crm_webhook_url: str,
    counters: dict[str, int],
    enrollments: list[dict[str, Any]],
    programs: list[dict[str, Any]],
    journal: list[dict[str, Any]],
    flash: str | None,
) -> str:
    flash_html = f'<div class="flash">{_e(flash)}</div>' if flash else ""
    counters_html = (
        '<div class="counters">'
        f'<div class="counter"><b>{counters["received"]}</b><span>← принято из CRM</span></div>'
        f'<div class="counter"><b>{counters["delivered"]}</b><span>→ доставлено в CRM</span></div>'
        f'<div class="counter"><b>{counters["pending"]}</b><span>в очереди / повтор</span></div>'
        f'<div class="counter"><b>{counters["dead"]}</b><span>DLQ</span></div>'
        "</div>"
    )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>LMS-заглушка — журнал интеграции</title>"
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>LMS-заглушка</h1>"
        '<span class="sub">двусторонняя интеграция CRM ↔ LMS · вебхуки в CRM: '
        f"<code>{_e(crm_webhook_url)}</code></span></header>"
        "<main>"
        + flash_html
        + counters_html
        + '<div class="card"><h2>Зачисления (приняты из CRM)</h2>'
        '<p class="hint">Кнопки имитируют жизненный цикл обучения: по клику LMS шлёт '
        "подписанный HMAC вебхук lms.learning.* обратно в CRM — живая демонстрация "
        "двусторонности.</p>"
        + _enrollment_rows(enrollments)
        + "</div>"
        + '<div class="card"><h2>Программы (синхронизированы из CRM)</h2>'
        + _program_rows(programs)
        + "</div>"
        + '<div class="card"><h2>Журнал обмена</h2>'
        '<p class="hint">← принято из CRM · → отправлено в CRM. '
        'JSON-тело каждого события — по клику. Машиночитаемый список: '
        "<code>GET /api/events</code>.</p>"
        + _journal_rows(journal)
        + "</div>"
        "<footer>Приём из CRM: <code>POST /api/v1/enrollments</code>, "
        "<code>POST /api/v1/programs/upsert</code> (HMAC X-Webhook-Signature, "
        "идемпотентность по event_id) · Списки: <code>GET /api/v1/enrollments</code>, "
        "<code>GET /api/events</code> · Пробы: <code>GET /healthz</code>, "
        "<code>GET /readyz</code> · Страница обновляется автоматически раз в 15 секунд."
        "</footer></main>"
        + _JS
        + "</body></html>"
    )
