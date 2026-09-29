"""HTML-страница cms-stub: демо-форма лида, каталог программ, журнал интеграции.

Без CDN и внешних ресурсов: стили и скрипт — inline (закрытый контур).
Форма «Оставить заявку на программу» — ключ демо: сабмит порождает подписанный
вебхук cms.lead.created в CRM, где появляется B2C-заявка.
"""
from __future__ import annotations

import html
import json
from typing import Any

from .common import pretty_json

_STATUS_BADGES = {
    "accepted": ("принято", "ok"),
    "pending": ("в очереди", "wait"),
    "retrying": ("повтор", "wait"),
    "delivered": ("доставлено", "ok"),
    "dead": ("DLQ", "err"),
}

_CSS = """
:root { --bg:#f4f5f7; --card:#ffffff; --ink:#1f2430; --muted:#6b7280; --line:#e5e7eb;
        --accent:#7c3aed; --ok:#04785e; --okbg:#d6f5e9; --wait:#8a5a00; --waitbg:#fdf0cd;
        --err:#b42318; --errbg:#fde3e1; }
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       background:var(--bg); color:var(--ink); font-size:14px; }
header { background:#2a1a4d; color:#fff; padding:14px 24px; display:flex; flex-wrap:wrap;
         gap:12px 24px; align-items:baseline; }
header h1 { margin:0; font-size:18px; font-weight:600; }
header .sub { color:#b6a8dd; font-size:12px; }
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
form.lead { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr));
            gap:12px; max-width:860px; }
form.lead label { display:flex; flex-direction:column; gap:4px; font-size:12px;
                  color:var(--muted); }
form.lead input, form.lead select, form.lead textarea { font:inherit; border:1px solid
    var(--line); border-radius:8px; padding:8px 10px; background:#fff; color:var(--ink); }
form.lead textarea { resize:vertical; min-height:38px; }
form.lead .full { grid-column:1 / -1; }
button { font:inherit; font-size:13px; border:1px solid var(--line); background:#fff;
         border-radius:8px; padding:8px 14px; cursor:pointer; }
button:hover { border-color:var(--accent); color:var(--accent); }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.primary:hover { opacity:.9; color:#fff; }
details summary { cursor:pointer; color:var(--accent); font-size:12px; }
details pre { background:#1c1230; color:#e2d9f8; border-radius:8px; padding:12px;
              font-size:12px; line-height:1.5; overflow-x:auto; max-width:640px; }
.flash { background:var(--okbg); color:var(--ok); border:1px solid #9fdec8;
         border-radius:10px; padding:10px 16px; margin:16px 0; }
.flash.err { background:var(--errbg); color:var(--err); border-color:#f2b8b2; }
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


def _badge(status: str | None, attempts: int | None = None) -> str:
    if status is None:
        return '<span class="badge wait">нет события</span>'
    label, kind = _STATUS_BADGES.get(status, (status, "wait"))
    if status == "retrying" and attempts:
        label = f"повтор ({attempts})"
    return f'<span class="badge {kind}">{html.escape(label)}</span>'


def _lead_form(catalog: list[dict[str, Any]]) -> str:
    options = ['<option value="">— программа не выбрана —</option>']
    options.extend(
        f'<option value="{_e(item["crm_program_id"])}">{_e(item["name"])}</option>'
        for item in catalog
    )
    catalog_hint = (
        ""
        if catalog
        else '<p class="hint">Каталог пуст — опубликуйте программу из CRM, '
        "и она появится в списке.</p>"
    )
    return (
        catalog_hint
        + '<form class="lead" method="post" action="/demo/leads">'
        '<label>ФИО *<input name="full_name" required maxlength="200" '
        'placeholder="Смирнова Ольга Викторовна"></label>'
        '<label>Email *<input name="email" type="email" required maxlength="200" '
        'placeholder="smirnova@example.com"></label>'
        '<label>Телефон<input name="phone" maxlength="30" placeholder="+7 916 000-00-00">'
        "</label>"
        '<label>Кто оставляет заявку<select name="client_kind">'
        '<option value="person">Физлицо</option>'
        '<option value="company">Юрлицо</option></select></label>'
        '<label>Компания (для юрлиц)<input name="company_name" maxlength="200"></label>'
        '<label>ИНН (для юрлиц)<input name="inn" maxlength="12"></label>'
        f'<label class="full">Программа<select name="program_id">{"".join(options)}</select>'
        "</label>"
        '<label class="full">Комментарий<textarea name="comment" maxlength="500" '
        'placeholder="Интересует рассрочка"></textarea></label>'
        '<div class="full"><button class="primary" type="submit">Оставить заявку → '
        "вебхук в CRM</button></div>"
        "</form>"
    )


def _catalog_rows(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return (
            '<div class="empty">Каталог пуст. Опубликуйте программу в CRM '
            "(флаг «публикация на сайте») — событие crm.program.published появится здесь.</div>"
        )
    rows = []
    for item in catalog:
        product_name = None
        if item.get("product"):
            try:
                product_name = (json.loads(item["product"]) or {}).get("name")
            except (TypeError, ValueError):
                product_name = None
        published = (
            '<span class="badge ok">опубликована</span>'
            if item["published"]
            else '<span class="badge err">снята с публикации</span>'
        )
        rows.append(
            "<tr>"
            f"<td class='nowrap'><code>{_e(item['cms_external_id'])}</code></td>"
            f"<td>{_e(item['name'])}</td>"
            f"<td>{_e(product_name)}</td>"
            f"<td class='nowrap'><code>{_e(item['crm_program_id'])}</code></td>"
            f"<td class='nowrap'>{published}</td>"
            f"<td class='nowrap'>{_e(item['updated_at'])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Позиция сайта</th><th>Программа</th><th>Продукт</th>"
        "<th>Программа CRM</th><th>Статус</th><th>Обновлено</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _lead_rows(leads: list[dict[str, Any]]) -> str:
    if not leads:
        return '<div class="empty">Лидов пока нет — заполните форму выше.</div>'
    rows = []
    for lead in leads:
        delivery = _badge(lead.get("delivery_status"), lead.get("delivery_attempts"))
        error_note = (
            f'<div class="error-text">{_e(lead["delivery_error"])}</div>'
            if lead.get("delivery_error")
            else ""
        )
        crm_ref = (
            f"<code>{_e(lead['crm_request_id'])}</code>"
            if lead.get("crm_request_id")
            else '<span class="badge wait">ожидает CRM</span>'
        )
        rows.append(
            "<tr>"
            f"<td class='nowrap'><code>{_e(lead['cms_lead_id'])}</code></td>"
            f"<td>{_e(lead['full_name'])}</td>"
            f"<td>{_e(lead['email'])}</td>"
            f"<td>{_e(lead.get('program_name'))}</td>"
            f"<td class='nowrap'>{delivery}{error_note}</td>"
            f"<td class='nowrap'>{crm_ref}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Лид</th><th>ФИО</th><th>Email</th><th>Программа</th>"
        "<th>Доставка в CRM</th><th>Заявка в CRM</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _journal_rows(journal: list[dict[str, Any]]) -> str:
    if not journal:
        return '<div class="empty">Событий пока нет.</div>'
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


def render_page(
    *,
    crm_webhook_url: str,
    counters: dict[str, int],
    catalog: list[dict[str, Any]],
    leads: list[dict[str, Any]],
    journal: list[dict[str, Any]],
    flash: str | None,
    flash_kind: str = "ok",
) -> str:
    flash_class = "flash err" if flash_kind == "err" else "flash"
    flash_html = f'<div class="{flash_class}">{_e(flash)}</div>' if flash else ""
    published = [item for item in catalog if item["published"]]
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
        "<title>CMS-заглушка — сайт и журнал интеграции</title>"
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>CMS-заглушка — «сайт» с каталогом программ</h1>"
        '<span class="sub">двусторонняя интеграция CRM ↔ CMS · вебхуки в CRM: '
        f"<code>{_e(crm_webhook_url)}</code></span></header>"
        "<main>"
        + flash_html
        + counters_html
        + '<div class="card"><h2>Оставить заявку на программу (демо-форма сайта)</h2>'
        '<p class="hint">Сабмит формы порождает подписанное HMAC событие cms.lead.created '
        "→ CRM создаёт B2C-заявку и возвращает её идентификатор — живая демонстрация "
        "двусторонности.</p>"
        + _lead_form(published)
        + "</div>"
        + '<div class="card"><h2>Каталог программ (опубликован из CRM)</h2>'
        + _catalog_rows(catalog)
        + "</div>"
        + '<div class="card"><h2>Лиды с сайта</h2>'
        + _lead_rows(leads)
        + "</div>"
        + '<div class="card"><h2>Журнал обмена</h2>'
        '<p class="hint">← принято из CRM · → отправлено в CRM. '
        'JSON-тело каждого события — по клику. Машиночитаемый список: '
        "<code>GET /api/events</code>.</p>"
        + _journal_rows(journal)
        + "</div>"
        "<footer>Приём из CRM: <code>POST /api/v1/catalog/upsert</code>, "
        "<code>POST /api/v1/catalog/unpublish</code> (HMAC X-Webhook-Signature, "
        "идемпотентность по event_id) · Списки: <code>GET /api/v1/catalog</code>, "
        "<code>GET /api/events</code> · Пробы: <code>GET /healthz</code>, "
        "<code>GET /readyz</code> · Страница обновляется автоматически раз в 15 секунд."
        "</footer></main>"
        + _JS
        + "</body></html>"
    )
