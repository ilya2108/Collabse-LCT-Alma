"""Сводные уведомления массового переноса (workflow-engine.md §6.3 п.4): получатели —
те же правила `request.transitioned` и личные настройки, что у поштучного
`emit_event_notifications`, но одна строка на получателя. Чистые функции, без БД."""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.core.events import EVENT_REQUEST_TRANSITIONED
from app.models.service import NotificationRule
from app.models.user import AppUser
from app.modules.notifications.prefs import merge_settings
from app.modules.workflow.migration import (
    SKIPPED_CHANNEL_DISABLED,
    DigestRow,
    MovedGroup,
    plan_migration_digests,
    render_digest,
    ru_requests,
    rules_for_target,
)

WF = uuid.uuid4()
OTHER_WF = uuid.uuid4()
FIRST_CONTACT = uuid.uuid4()
CONTRACT_PREP = uuid.uuid4()
NEW = uuid.uuid4()
NEGOTIATION = uuid.uuid4()
CHANGE_ID = uuid.uuid4()

_BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
_tick = itertools.count()


def _rule(
    recipient_type: str,
    *,
    channel: str = "telegram",
    value: str | None = None,
    workflow_id: uuid.UUID | None = None,
    stage_id: uuid.UUID | None = None,
    is_active: bool = True,
    event_type: str = EVENT_REQUEST_TRANSITIONED,
    created_at: datetime | None = None,
) -> NotificationRule:
    rule = NotificationRule(
        id=uuid.uuid4(),
        event_type=event_type,
        recipient_type=recipient_type,
        recipient_value=value,
        channel=channel,
        workflow_id=workflow_id,
        stage_id=stage_id,
        is_active=is_active,
        params={},
    )
    rule.created_at = created_at or _BASE + timedelta(minutes=next(_tick))
    return rule


def _user(
    roles: tuple[str, ...] = ("kam",),
    *,
    manager: AppUser | None = None,
    tg_chat_id: int | None = 1,
) -> AppUser:
    return AppUser(
        id=uuid.uuid4(),
        keycloak_sub=uuid.uuid4(),
        username=f"u{uuid.uuid4().hex[:6]}@test",
        email="user@test",
        roles=list(roles),
        manager_id=manager.id if manager else None,
        tg_chat_id=tg_chat_id,
        is_active=True,
    )


def _group(
    responsible: AppUser,
    count: int,
    *,
    from_id: uuid.UUID = FIRST_CONTACT,
    from_name: str = "Первичный контакт",
    to_id: uuid.UUID = NEW,
    to_name: str = "Новая",
    titles: tuple[str, ...] = (),
) -> MovedGroup:
    return MovedGroup(
        from_stage_id=from_id,
        from_stage_name=from_name,
        to_stage_id=to_id,
        to_stage_name=to_name,
        responsible_user_id=responsible.id,
        count=count,
        sample_ids=tuple(uuid.uuid4() for _ in range(min(count, 20))),
        sample_titles=titles or tuple(f"Заявка {i}" for i in range(min(count, 3))),
    )


def _prefs(
    *, enabled: bool = True, only_mine: bool | None = None, telegram: bool = True
) -> dict[str, Any]:
    event: dict[str, Any] = {"enabled": enabled}
    if only_mine is not None:
        event["only_mine"] = only_mine
    return merge_settings(
        {
            "events": {EVENT_REQUEST_TRANSITIONED: event},
            "channels": {"telegram": {"enabled": telegram}},
        }
    )


def _plan(
    groups: list[MovedGroup],
    rules: list[NotificationRule],
    users: list[AppUser],
    *,
    role_members: dict[str, list[AppUser]] | None = None,
    prefs: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> list[DigestRow]:
    all_prefs = {u.id: merge_settings(None) for u in users}
    all_prefs.update(prefs or {})
    return plan_migration_digests(
        groups,
        rules,
        workflow_id=WF,
        users={u.id: u for u in users},
        role_members=role_members or {},
        prefs=all_prefs,
    )


def _by_recipient(rows: list[DigestRow]) -> dict[Any, DigestRow]:
    return {(r.recipient_user_id or r.address, r.channel): r for r in rows}


# --- получатели ------------------------------------------------------------------------


def test_responsible_one_row_per_kam_with_own_count() -> None:
    kams = [_user() for _ in range(3)]
    groups = [_group(k, n) for k, n in zip(kams, (7, 3, 1), strict=True)]
    rows = _plan(groups, [_rule("responsible")], kams)
    assert len(rows) == 3
    by = _by_recipient(rows)
    for kam, n in zip(kams, (7, 3, 1), strict=True):
        row = by[(kam.id, "telegram")]
        assert row.count == n and row.only_own and row.skipped_reason is None


def test_manager_gets_one_row_for_all_subordinates() -> None:
    manager = _user(("head_kam",))
    kam1, kam2 = _user(manager=manager), _user(manager=manager)
    orphan = _user()  # без руководителя — эскалация некому
    groups = [_group(kam1, 4), _group(kam2, 6), _group(orphan, 5)]
    users = [manager, kam1, kam2, orphan]
    rule = _rule("manager_of_responsible")
    # как и поштучно: «только мои» по умолчанию отсекает чужие заявки и у руководителя
    assert _plan(groups, [rule], users) == []
    rows = _plan(groups, [rule], users, prefs={manager.id: _prefs(only_mine=False)})
    assert len(rows) == 1
    (row,) = rows
    assert row.recipient_user_id == manager.id
    assert row.count == 10 and len(row.groups) == 2 and not row.only_own


def test_role_rule_respects_only_mine() -> None:
    head = _user(("head_kam",))
    kams = [_user() for _ in range(2)]
    groups = [_group(k, 5) for k in kams]
    rule = _rule("role", value="head_kam", channel="email")
    members = {"head_kam": [head]}
    # по умолчанию «только мои» для request.transitioned: чужие заявки — не уведомлять
    assert _plan(groups, [rule], [head, *kams], role_members=members) == []
    rows = _plan(
        groups,
        [rule],
        [head, *kams],
        role_members=members,
        prefs={head.id: _prefs(only_mine=False)},
    )
    assert len(rows) == 1 and rows[0].count == 10 and rows[0].channel == "email"


def test_user_rule_plus_responsible_no_double_count() -> None:
    kam1, kam2, kam3 = _user(), _user(), _user()
    groups = [_group(kam1, 3), _group(kam2, 4), _group(kam3, 5)]
    rules = [_rule("responsible"), _rule("user", value=str(kam1.id))]
    users = [kam1, kam2, kam3]
    only_mine = _by_recipient(_plan(groups, rules, users))
    assert only_mine[(kam1.id, "telegram")].count == 3  # только свои
    assert len(only_mine) == 3
    everything = _by_recipient(
        _plan(groups, rules, users, prefs={kam1.id: _prefs(only_mine=False)})
    )
    row = everything[(kam1.id, "telegram")]
    assert row.count == 12 and not row.only_own  # свои 3 не посчитаны дважды
    assert everything[(kam2.id, "telegram")].count == 4


def test_channel_disabled_skipped_event_disabled_dropped() -> None:
    muted, silent, normal = _user(), _user(), _user()
    groups = [_group(muted, 2), _group(silent, 3), _group(normal, 4)]
    rows = _by_recipient(
        _plan(
            groups,
            [_rule("responsible")],
            [muted, silent, normal],
            prefs={muted.id: _prefs(telegram=False), silent.id: _prefs(enabled=False)},
        )
    )
    assert rows[(muted.id, "telegram")].skipped_reason == SKIPPED_CHANNEL_DISABLED
    assert (silent.id, "telegram") not in rows
    assert rows[(normal.id, "telegram")].skipped_reason is None


def test_direct_address_gets_all_groups_without_prefs() -> None:
    kams = [_user(), _user()]
    groups = [_group(kams[0], 2), _group(kams[1], 3)]
    rule = _rule("tg_username", value="@sales_channel")
    rows = _plan(groups, [rule], kams, prefs={k.id: _prefs(enabled=False) for k in kams})
    assert len(rows) == 1
    (row,) = rows
    assert row.recipient_user_id is None and row.address == "@sales_channel"
    assert row.count == 5 and row.rule_id == rule.id and row.skipped_reason is None


def test_rules_narrowed_by_target_stage_and_workflow() -> None:
    kam = _user()
    fired = _rule("responsible", stage_id=NEW, workflow_id=WF)
    other_stage = _rule("responsible", channel="email", stage_id=FIRST_CONTACT)
    other_wf = _rule("responsible", channel="max", workflow_id=OTHER_WF)
    inactive = _rule("manager_of_responsible", is_active=False)
    other_event = _rule("responsible", channel="email", event_type="request.stuck")
    rules = [fired, other_stage, other_wf, inactive, other_event]
    assert rules_for_target(rules, WF, NEW) == [fired]
    rows = _plan([_group(kam, 3)], rules, [kam])
    assert [(r.recipient_user_id, r.channel, r.rule_id) for r in rows] == [
        (kam.id, "telegram", fired.id)
    ]


def test_rule_by_target_stage_counts_only_its_groups() -> None:
    kam = _user()
    groups = [
        _group(kam, 3),
        _group(
            kam,
            4,
            from_id=CONTRACT_PREP,
            from_name="Подготовка договора",
            to_id=NEGOTIATION,
            to_name="Переговоры",
        ),
    ]
    rows = _plan(groups, [_rule("responsible", channel="email", stage_id=NEGOTIATION)], [kam])
    assert len(rows) == 1 and rows[0].count == 4


def test_rule_id_is_first_rule_by_created_at() -> None:
    kam = _user()
    late = _rule("responsible", created_at=_BASE + timedelta(days=2))
    early = _rule("user", value=str(kam.id), created_at=_BASE)
    rows = _plan([_group(kam, 2)], [late, early], [kam])
    assert len(rows) == 1 and rows[0].rule_id == early.id
    # порядок входа не влияет
    rows = _plan([_group(kam, 2)], [early, late], [kam])
    assert rows[0].rule_id == early.id


def test_user_rule_with_bad_or_unknown_id() -> None:
    kam = _user()
    rows = _plan(
        [_group(kam, 1)],
        [_rule("user", value="не-uuid"), _rule("user", value=str(uuid.uuid4()))],
        [kam],
    )
    assert rows == []


# --- текст и payload сводки ------------------------------------------------------------


def test_ru_requests_plural_forms() -> None:
    assert ru_requests(1) == "1 заявка"
    assert ru_requests(2) == "2 заявки"
    assert ru_requests(5) == "5 заявок"
    assert ru_requests(11) == "11 заявок"
    assert ru_requests(21) == "21 заявка"
    assert ru_requests(112) == "112 заявок"
    assert ru_requests(10_000) == "10 000 заявок"


def _row(groups: list[MovedGroup], *, user: AppUser | None, only_own: bool) -> DigestRow:
    return DigestRow(
        rule_id=uuid.uuid4(),
        recipient_user_id=user.id if user else None,
        channel="telegram",
        address=None if user else "@channel",
        skipped_reason=None,
        groups=tuple(groups),
        only_own=only_own,
    )


def _render(row: DigestRow) -> tuple[str, str, dict[str, Any]]:
    return render_digest(
        row,
        workflow_name="Взаимодействие с вузами",
        workflow_code="b2b",
        change_id=CHANGE_ID,
        workflow_id=WF,
    )


def test_render_digest_body_and_payload_for_own_requests() -> None:
    kam = _user()
    group = _group(kam, 7, titles=("Альфа", "Бета", "Гамма"))
    subject, body, payload = _render(_row([group], user=kam, only_own=True))
    assert subject == "Заявки перенесены"
    assert body.splitlines() == [
        "Схема «Взаимодействие с вузами» изменена администратором.",
        "• этап «Первичный контакт» удалён — 7 заявок перенесено в «Новая»",
        "Например: «Альфа», «Бета», «Гамма» и ещё 4.",
        "Срок нахождения на этапе сохранён — счётчик «зависания» не сброшен.",
    ]
    assert payload["digest"] is True and payload["migrated"] is True
    assert payload["count"] == 7 and payload["title"] == "7 заявок"
    assert payload["status"] == "Новая"
    assert payload["change_request_id"] == str(CHANGE_ID)
    assert payload["workflow_id"] == str(WF)
    assert payload["by_status"] == [{"from": "Первичный контакт", "to": "Новая", "count": 7}]
    assert payload["request_ids"] == [str(i) for i in group.sample_ids]
    url = urlsplit(payload["url"])
    assert url.path == "/board"
    assert parse_qs(url.query) == {"wf": ["b2b"], "kam": [str(kam.id)], "status": [str(NEW)]}


def test_render_digest_single_request_and_agreement() -> None:
    kam = _user()
    _, body, _ = _render(_row([_group(kam, 1, titles=("Одна",))], user=kam, only_own=True))
    assert "1 заявка перенесена в «Новая»" in body
    assert "Заявка: «Одна»." in body
    _, body, _ = _render(_row([_group(kam, 3, titles=("А", "Б", "В"))], user=kam, only_own=True))
    assert "3 заявки перенесены" in body and "и ещё" not in body


def test_render_digest_url_without_kam_and_status() -> None:
    manager, kam = _user(("head_kam",)), _user()
    groups = [
        _group(kam, 2),
        _group(
            kam,
            3,
            from_id=CONTRACT_PREP,
            from_name="Подготовка договора",
            to_id=NEGOTIATION,
            to_name="Переговоры",
        ),
    ]
    _, _, payload = _render(_row(groups, user=manager, only_own=False))
    assert parse_qs(urlsplit(payload["url"]).query) == {"wf": ["b2b"]}
    assert "status" not in payload
    assert payload["count"] == 5 and len(payload["by_status"]) == 2


def test_render_digest_clips_long_names() -> None:
    kam = _user()
    long_name = "Очень длинное название этапа " * 10
    group = _group(kam, 2, from_name=long_name, titles=("З" * 300,))
    _, body, _ = _render(_row([group], user=kam, only_own=True))
    for line in body.splitlines():
        assert len(line) <= 220
    assert "…" in body


def test_render_digest_body_bounded_for_many_deleted_stages() -> None:
    kam = _user()
    groups = [
        _group(
            kam,
            100 + i,
            from_id=uuid.uuid4(),
            from_name=f"Этап {i} " + "х" * 200,
            to_name="Цель " + "ц" * 200,
            titles=("Т" * 200, "Т" * 200, "Т" * 200),
        )
        for i in range(10)
    ]
    _, body, payload = _render(_row(groups, user=kam, only_own=True))
    assert len(body) < 1500
    assert "• ещё 5 этапов" in body
    assert len(payload["request_ids"]) == 20
    assert payload["count"] == sum(100 + i for i in range(10))
    assert len(payload["by_status"]) == 10  # в payload — полностью
