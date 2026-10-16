"""«Сводка ИИ» (`/reports/ai-summaries`) на настоящем PostgreSQL.

Провайдер по умолчанию — stub (без сети), `runner.spawn` подменён: задача выполняется
явным `service.run_job(..., session_factory=db_session_factory)` на соединении теста.
KeyDB в тестах недоступен — отчёты читаются из PG.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.core.config import get_settings
from app.models import AiSummaryJob, AppSetting, AppUser, AuditLog
from app.modules.ai_summary import runner, service
from app.modules.ai_summary.gateway import ProviderError
from app.modules.ai_summary.providers import LlmReply

from . import factories
from .rbac_factories import KAM_PERMISSIONS, make_role

API = "/api/v1/reports/ai-summaries"
NAMES = {
    "head": "Морозов Дмитрий Александрович",
    "kam1": "Петров Константин Андреевич",
    "kam2": "Иванова Мария Сергеевна",
}


@pytest.fixture(autouse=True)
def _stub_provider(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    monkeypatch.setattr(get_settings(), "ai_summary_provider", "stub")
    spawned: list[uuid.UUID] = []
    monkeypatch.setattr(runner, "spawn", spawned.append)
    return spawned


async def _set_flag(session, enabled: bool) -> None:  # noqa: ANN001
    row = await session.get(AppSetting, "feature_flags")
    if row is None:
        session.add(AppSetting(key="feature_flags", value={"ai_summaries": enabled}))
    else:
        row.value = {**row.value, "ai_summaries": enabled}
    await session.flush()


@pytest.fixture
async def team(db_session, workflows) -> dict[str, Any]:  # noqa: ANN001
    make = factories.make_user
    head = await make(db_session, roles=("head_kam",), full_name=NAMES["head"])
    kam1 = await make(db_session, roles=("kam",), full_name=NAMES["kam1"], manager=head)
    kam2 = await make(db_session, roles=("kam",), full_name=NAMES["kam2"], manager=head)
    now = factories.utcnow()
    wf, stages = workflows["b2b"]
    university = await factories.make_university(
        db_session, name="Секретный государственный университет", kam=kam1
    )
    deals = []
    for index in range(3):  # просрочены: «Переговоры» дольше 14 дней
        deals.append(
            await factories.make_deal(
                db_session,
                workflow=wf,
                stages=stages,
                responsible=kam1,
                university=university,
                title=f"Тайная заявка {index}",
                path=[("new", now - timedelta(days=40)), ("negotiation", now - timedelta(days=25))],
                amount=990000,
            )
        )
    for _index in range(2):  # в норме
        await factories.make_deal(
            db_session,
            workflow=wf,
            stages=stages,
            responsible=kam2,
            path=[("new", now - timedelta(days=2))],
        )
    wf_c, stages_c = workflows["b2c"]
    client = await factories.make_counterparty(db_session, full_name="Клиентова Ольга Ивановна")
    await factories.make_deal(
        db_session,
        workflow=wf_c,
        stages=stages_c,
        responsible=kam2,
        counterparty=client,
        title="Курс для Клиентовой",
        path=[("new", now - timedelta(days=20))],
    )
    await _set_flag(db_session, True)
    return {"head": head, "kam1": kam1, "kam2": kam2, "deals": deals}


async def _post(api, user: AppUser, **body: Any):  # noqa: ANN001, ANN202
    payload = {"kind": "team", **body}
    return await api.as_user(user).post(API, json=payload)


async def _audit(session, action: str) -> list[AuditLog]:  # noqa: ANN001
    return list(
        (
            await session.execute(
                sa.select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
            )
        ).scalars()
    )


# --------------------------------------------------------------------------------------
# Основной поток: задача, выполнение, чтение, кэш, дедупликация
# --------------------------------------------------------------------------------------


async def test_team_summary_flow(db_session, db_session_factory, api, team, _stub_provider) -> None:  # noqa: ANN001
    head = team["head"]
    created = await _post(api, head)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "queued" and body["reused"] is False
    assert body["provider"] == "stub" and body["poll_after_ms"] == 2000
    assert body["queue_ahead"] is None  # очередь считается только для gigachat
    assert body["params"]["from"] and body["params"]["direction_id"] == []
    job_id = uuid.UUID(body["id"])
    assert _stub_provider == [job_id]

    job = await db_session.get(AiSummaryJob, job_id)
    dumped = json.dumps(job.facts, ensure_ascii=False)
    for secret in (
        *NAMES.values(),
        "Петров",
        "Иванова",
        "Секретный государственный университет",
        "Тайная заявка",
        "Клиентова",
        "990000",
        str(team["kam1"].id),
    ):
        assert secret not in dumped, secret
    assert set(job.aliases.values()) >= {str(team["kam1"].id)}

    await service.run_job(job_id, session_factory=db_session_factory)
    done = await api.as_user(head).get(f"{API}/{job_id}")
    assert done.status_code == 200
    result = done.json()
    assert result["status"] == "done" and result["poll_after_ms"] is None
    assert result["result"]["source"] == "stub" and result["result"]["model"] == "stub"
    sections = result["result"]["sections"]
    assert sections["headline"].startswith("На ")
    # КАМ с просрочками — в «Кто требует внимания» под ФИО, подставленным на стороне CRM
    text = json.dumps(sections, ensure_ascii=False)
    assert "Петров К. А." in text and "КАМ-0" not in text
    people = {person["name"]: person for person in result["result"]["people"]}
    assert people["Петров К. А."]["kam_id"] == str(team["kam1"].id)
    checks = result["result"]["checks"]
    assert checks["sentences_removed"] == 0 and checks["numbers_total"] > 0
    assert checks["removed_by_reason"] == {"numbers": 0, "advice": 0, "evaluative": 0, "other": 0}

    # повтор — тот же результат из кэша
    again = await _post(api, head)
    assert again.status_code == 200
    assert again.json()["id"] == str(job_id) and again.json()["reused"] is True
    # «Обновить» сразу после готовой сводки — та же сводка (не чаще раза в 5 минут)
    cooled = await _post(api, head, force=True)
    assert cooled.status_code == 200 and cooled.json()["id"] == str(job_id)
    assert cooled.json()["reused"] is True
    # сводке больше 5 минут — «Обновить» ставит новую задачу мимо кэша
    await db_session.execute(
        sa.update(AiSummaryJob)
        .where(AiSummaryJob.id == job_id)
        .values(finished_at=sa.func.now() - timedelta(minutes=6))
    )
    forced = await _post(api, head, force=True)
    assert forced.status_code == 202 and forced.json()["id"] != str(job_id)

    # аудит: запрос и результат, без ФИО и текста сводки
    requested = await _audit(db_session, "ai.summary_requested")
    assert [entry.after["outcome"] for entry in requested] == [
        "queued",
        "reused_done",
        "reused_done_cooldown",
        "queued",
    ]
    first = requested[0]
    assert first.actor_user_id == head.id and first.entity_type == "ai_summary_job"
    assert first.entity_id == job_id
    assert set(first.after) == {
        "kind",
        "kam_id",
        "mode",
        "provider",
        "force",
        "params",
        "input_sha256",
        "outcome",
    }
    (generated,) = await _audit(db_session, "ai.summary_generated")
    assert generated.actor_user_id == head.id and generated.entity_id == job_id
    assert generated.after["status"] == "done" and generated.after["provider"] == "stub"
    assert generated.after["prompt_version"] == "kam-summary.v2"
    assert generated.after["checks"] == checks and generated.after["error_code"] is None
    audit_json = json.dumps([e.after for e in requested + [generated]], ensure_ascii=False)
    for name in (*NAMES.values(), "Петров", "Иванова", sections["headline"]):
        assert name not in audit_json


async def test_inflight_requests_share_one_job(api, team, _stub_provider) -> None:  # noqa: ANN001
    first = await _post(api, team["head"])
    second = await _post(api, team["head"])
    forced = await _post(api, team["head"], force=True)
    assert first.status_code == 202
    assert second.status_code == 200 and second.json()["reused"] is True
    assert forced.status_code == 200 and forced.json()["reused"] is True
    assert first.json()["id"] == second.json()["id"] == forced.json()["id"]
    assert second.json()["status"] == "queued"
    assert len(_stub_provider) == 1


async def test_rules_mode_answers_immediately(db_session, api, team, _stub_provider) -> None:  # noqa: ANN001
    response = await _post(api, team["head"], mode="rules")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done" and body["provider"] == "rules"
    assert body["result"]["source"] == "rules" and body["result"]["model"] is None
    assert body["result"]["checks"]["sentences_removed"] == 0
    assert _stub_provider == []
    outcomes = [e.after["outcome"] for e in await _audit(db_session, "ai.summary_requested")]
    assert outcomes == ["rules_done"]
    (generated,) = await _audit(db_session, "ai.summary_generated")
    assert generated.after["provider"] == "rules" and generated.after["status"] == "done"


async def test_kam_summary(db_session, db_session_factory, api, team) -> None:  # noqa: ANN001
    kam1 = team["kam1"]
    missing = await api.as_user(team["head"]).post(API, json={"kind": "kam"})
    assert missing.status_code == 400
    (detail,) = missing.json()["error"]["details"]
    assert (detail["field"], detail["code"]) == ("kam_id", "required")

    response = await api.as_user(team["head"]).post(
        API, json={"kind": "kam", "kam_id": str(kam1.id), "params": {"kam_id": [str(uuid.uuid4())]}}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kam_id"] == str(kam1.id) and body["kam_name"] == "Петров К. А."
    assert body["params"]["kam_id"] == []  # фильтр kam_id для сводки по КАМу не участвует
    job = await db_session.get(AiSummaryJob, uuid.UUID(body["id"]))
    assert job.facts["report"] == "kam" and job.aliases == {"КАМ-01": str(kam1.id)}
    assert "team" in job.facts["overdue"]
    await service.run_job(job.id, session_factory=db_session_factory)
    done = (await api.as_user(team["head"]).get(f"{API}/{job.id}")).json()
    assert done["status"] == "done" and done["result"]["people"] == []

    unknown = await api.as_user(team["head"]).post(
        API, json={"kind": "kam", "kam_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 404


# --------------------------------------------------------------------------------------
# Права, флаг, область
# --------------------------------------------------------------------------------------


async def test_flag_disabled_is_422(db_session, api, team) -> None:  # noqa: ANN001
    created = await _post(api, team["head"], mode="rules")
    await _set_flag(db_session, False)
    for response in (
        await _post(api, team["head"]),
        await api.as_user(team["head"]).get(f"{API}/{created.json()['id']}"),
    ):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable"
        assert "ai_summaries" in response.json()["error"]["message"]


async def test_kam_and_observer_have_no_access(db_session, api, team) -> None:  # noqa: ANN001
    observer = await factories.make_user(db_session, roles=("observer",))
    for user in (team["kam1"], observer):
        response = await _post(api, user)
        assert response.status_code == 403
        assert "Сводки ИИ по отчёту КАМов" in response.json()["error"]["message"]
        assert (await api.as_user(user).get(f"{API}/{uuid.uuid4()}")).status_code == 403


async def test_own_scope_role(db_session, api, team, workflows) -> None:  # noqa: ANN001
    # своя роль: права КАМа + сводки ИИ, область «только свои»
    await make_role(
        db_session,
        code="kam_ai",
        permissions={*KAM_PERMISSIONS, "reports:ai_summary"},
        record_scope="own",
    )
    own_kam = await factories.make_user(
        db_session, roles=("kam_ai",), full_name="Свойский Пётр Ильич"
    )
    wf, stages = workflows["b2b"]
    now = factories.utcnow()
    await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=own_kam,
        path=[("new", now - timedelta(days=30)), ("negotiation", now - timedelta(days=20))],
    )
    team_resp = await _post(api, own_kam, mode="rules")
    assert team_resp.status_code == 200, team_resp.text
    job = await db_session.get(AiSummaryJob, uuid.UUID(team_resp.json()["id"]))
    # в фактах только сам пользователь (чужих КАМов команды нет)
    assert set(job.aliases.values()) == {str(own_kam.id)}
    assert job.scope_key == f"b2b:{own_kam.id}|b2c:{own_kam.id}"
    assert job.facts["overdue"]["kpi"]["overdue"]["value"] == 1
    foreign = await api.as_user(own_kam).post(
        API, json={"kind": "kam", "kam_id": str(team["kam2"].id)}
    )
    assert foreign.status_code == 403
    own = await api.as_user(own_kam).post(
        API, json={"kind": "kam", "kam_id": str(own_kam.id), "mode": "rules"}
    )
    assert own.status_code == 200
    own_job = await db_session.get(AiSummaryJob, uuid.UUID(own.json()["id"]))
    assert "team" not in own_job.facts["overdue"]  # справки команды при «только свои» нет


async def test_workflow_outside_scope_and_foreign_scope_404(db_session, api, team) -> None:  # noqa: ANN001
    await make_role(
        db_session,
        code="head_b2b",
        permissions={"reports:read", "reports:all", "reports:ai_summary", "requests:read"},
        workflow_types=("b2b",),
    )
    b2b_head = await factories.make_user(db_session, roles=("head_b2b",))
    forbidden = await _post(api, b2b_head, params={"workflow_type": "b2c"})
    assert forbidden.status_code == 403
    assert "B2C" in forbidden.json()["error"]["message"]

    created = await _post(api, team["head"], mode="rules")
    job_id = created.json()["id"]
    # другая область (только B2B) — задачу не видно, существование не раскрываем
    assert (await api.as_user(b2b_head).get(f"{API}/{job_id}")).status_code == 404
    # другой руководитель с той же областью — видит
    other_head = await factories.make_user(db_session, roles=("head_kam",))
    assert (await api.as_user(other_head).get(f"{API}/{job_id}")).status_code == 200
    assert (await api.as_user(team["head"]).get(f"{API}/{uuid.uuid4()}")).status_code == 404


async def test_ai_permission_only_in_some_workflows(db_session, api, team) -> None:  # noqa: ANN001
    # reports:* во всех процессах, но сводки ИИ — только роль с B2B
    await make_role(
        db_session,
        code="ai_b2b",
        permissions={"reports:read", "reports:ai_summary"},
        workflow_types=("b2b",),
    )
    # наблюдатель: отчёты по всем КАМам в обоих процессах, но без сводок ИИ
    user = await factories.make_user(db_session, roles=("observer", "ai_b2b"))
    # фильтр «все процессы»: экран показывает B2B и B2C, сводка вышла бы только по B2B
    everything = await _post(api, user, mode="rules")
    assert everything.status_code == 403
    message = everything.json()["error"]["message"]
    assert "только по процессу B2B" in message and "фильтре" in message
    response = await _post(api, user, mode="rules", params={"workflow_type": "b2b"})
    assert response.status_code == 200
    job = await db_session.get(AiSummaryJob, uuid.UUID(response.json()["id"]))
    assert job.scope_key == "b2b:*"
    assert set(job.facts["overdue"]["by_workflow"]) == {"B2B"}


# --------------------------------------------------------------------------------------
# Сбои, проверка цифр, обслуживание
# --------------------------------------------------------------------------------------


class _Provider:
    name = "gigachat"

    def __init__(self, *, error: str | None = None, text: str | None = None) -> None:
        self.error, self.text = error, text

    async def complete(self, messages, *, schema, purpose, request_id):  # noqa: ANN001, ANN201
        if self.error:
            raise ProviderError(self.error)
        return LlmReply(
            text=self.text or "", model="GigaChat-2-Pro", tokens_in=10, tokens_out=5, latency_ms=7
        )


async def test_provider_error_marks_job_failed(
    db_session, db_session_factory, api, team, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "ai_summary_provider", "gigachat")
    monkeypatch.setattr(service, "get_provider", lambda name: _Provider(error="llm_busy"))
    created = await _post(api, team["head"])
    assert created.status_code == 202
    assert created.json()["queue_ahead"] == 0  # gigachat: очередь считается
    job_id = created.json()["id"]
    await service.run_job(uuid.UUID(job_id), session_factory=db_session_factory)
    body = (await api.as_user(team["head"]).get(f"{API}/{job_id}")).json()
    assert body["status"] == "failed" and body["result"] is None
    assert body["error"] == {
        "code": "llm_busy",
        "message": "GigaChat занят другими сводками — он обрабатывает по одной",
        "rules_available": True,
    }
    (generated,) = await _audit(db_session, "ai.summary_generated")
    assert generated.after["status"] == "failed" and generated.after["error_code"] == "llm_busy"
    # провал не кэшируется: следующий запрос — новая задача
    assert (await _post(api, team["head"])).status_code == 202


async def test_invented_numbers_fail_verification(
    db_session, db_session_factory, api, team, monkeypatch
) -> None:  # noqa: ANN001
    answer = {
        "headline": "Просрочено 777 заявок из 9999.",
        "going_well": ["Рост на 555 п.п."],
        "lagging": ["Рекомендуется перераспределить 31337 заявок."],
    }
    monkeypatch.setattr(get_settings(), "ai_summary_provider", "gigachat")
    monkeypatch.setattr(
        service, "get_provider", lambda name: _Provider(text=json.dumps(answer, ensure_ascii=False))
    )
    job_id = (await _post(api, team["head"])).json()["id"]
    await service.run_job(uuid.UUID(job_id), session_factory=db_session_factory)
    body = (await api.as_user(team["head"]).get(f"{API}/{job_id}")).json()
    assert body["status"] == "failed" and body["error"]["code"] == "llm_bad_output"
    job = await db_session.get(AiSummaryJob, uuid.UUID(job_id), populate_existing=True)
    assert job.tokens_in == 10 and job.attempts == 1 and job.model == "GigaChat-2-Pro"
    (generated,) = await _audit(db_session, "ai.summary_generated")
    assert generated.after["checks"]["sentences_removed"] == 3
    assert generated.after["checks"]["advice_removed"] == 1


async def test_stale_job_is_interrupted_and_old_jobs_deleted(db_session, api, team) -> None:  # noqa: ANN001
    created = await _post(api, team["head"])
    job_id = uuid.UUID(created.json()["id"])
    await db_session.execute(
        sa.update(AiSummaryJob)
        .where(AiSummaryJob.id == job_id)
        .values(created_at=sa.func.now() - timedelta(minutes=20))
    )
    body = (await api.as_user(team["head"]).get(f"{API}/{job_id}")).json()
    assert body["status"] == "failed" and body["error"]["code"] == "interrupted"
    assert body["error"]["message"] == "Формирование прервано"

    old = await _post(api, team["head"], mode="rules")
    old_id = uuid.UUID(old.json()["id"])
    await db_session.execute(
        sa.update(AiSummaryJob)
        .where(AiSummaryJob.id == old_id)
        .values(created_at=sa.func.now() - timedelta(days=8))
    )
    await _post(api, team["head"], mode="rules")  # ленивое обслуживание при создании
    assert await db_session.get(AiSummaryJob, old_id, populate_existing=True) is None


async def test_cache_key_ignores_changing_facts(db_session, api, team, workflows) -> None:  # noqa: ANN001
    """Цифры изменились (на текущем периоде задержки растут каждую минуту) — повторный клик
    всё равно получает готовую сводку из кэша, а не новый вызов GigaChat."""
    head = team["head"]
    created = await _post(api, head)
    assert created.status_code == 202
    job_id = uuid.UUID(created.json()["id"])
    first_sha = (await db_session.get(AiSummaryJob, job_id)).input_sha256
    wf, stages = workflows["b2b"]
    now = factories.utcnow()
    await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=team["kam2"],
        path=[("new", now - timedelta(days=30)), ("negotiation", now - timedelta(days=20))],
    )
    # задача ещё в полёте — присоединение, несмотря на новые цифры
    joined = await _post(api, head)
    assert joined.status_code == 200 and joined.json()["id"] == str(job_id)
    requested = await _audit(db_session, "ai.summary_requested")
    assert requested[-1].after["outcome"] == "reused_inflight"
    facts = await service.collect_facts(
        db_session,
        head,
        "team",
        None,
        service.resolve_params(
            service.request_params(service.AiSummaryCreate(kind="team")),
            today=service.kam_activity._now().astimezone(service.REPORT_TZ).date(),
        ),
        service.ai_scope(head),
    )
    assert facts.sha256 != first_sha  # цифры действительно другие


async def test_user_hourly_limit(db_session, db_session_factory, api, team, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "ai_summary_user_hourly_limit", 2)
    head = team["head"]
    first = await _post(api, head)
    second = await _post(api, head, params={"workflow_type": "b2b"})
    assert first.status_code == 202 and second.status_code == 202
    # кэш и задачи в полёте лимит не тратят
    assert (await _post(api, head)).json()["reused"] is True
    # третья новая задача за час — сразу failed: rate_limited, без вызова провайдера
    third = await _post(api, head, params={"workflow_type": "b2c"})
    assert third.status_code == 200, third.text
    body = third.json()
    assert body["status"] == "failed" and body["error"]["code"] == "rate_limited"
    assert body["error"]["rules_available"] is True
    assert len(team) and body["id"] not in {first.json()["id"], second.json()["id"]}
    # сводка по правилам лимитом не ограничена
    rules = await _post(api, head, mode="rules", params={"workflow_type": "b2c"})
    assert rules.status_code == 200 and rules.json()["status"] == "done"
    # другой руководитель — свой лимит
    other = await factories.make_user(db_session, roles=("head_kam",))
    assert (await _post(api, other, params={"workflow_type": "b2c"})).status_code == 202
    outcomes = [e.after["outcome"] for e in await _audit(db_session, "ai.summary_requested")]
    assert "rate_limited" in outcomes
    # отклонённые лимитом задачи сами лимит не продлевают
    assert await service._user_jobs_last_hour(db_session, head.id) == 2


async def test_flag_off_stops_queued_job(
    db_session, db_session_factory, api, team, monkeypatch
) -> None:  # noqa: ANN001
    calls: list[str] = []

    class _Spy:
        name = "gigachat"

        async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            calls.append("called")
            raise AssertionError("провайдер не должен вызываться")

    monkeypatch.setattr(get_settings(), "ai_summary_provider", "gigachat")
    monkeypatch.setattr(service, "get_provider", lambda name: _Spy())
    job_id = uuid.UUID((await _post(api, team["head"])).json()["id"])
    await _set_flag(db_session, False)  # аварийный выключатель, пока задача в очереди
    await service.run_job(job_id, session_factory=db_session_factory)
    assert calls == []
    job = await db_session.get(AiSummaryJob, job_id, populate_existing=True)
    assert job.status == "failed" and job.error_code == "disabled" and job.attempts == 0
    (generated,) = await _audit(db_session, "ai.summary_generated")
    assert generated.after["error_code"] == "disabled"


async def test_late_answer_is_audited_when_job_gone(
    db_session, db_session_factory, api, team, monkeypatch
) -> None:  # noqa: ANN001
    """Строку закрыли, пока GigaChat отвечал: результат не пишется, но вызов — в аудите."""
    monkeypatch.setattr(get_settings(), "ai_summary_provider", "gigachat")
    job_id = uuid.UUID((await _post(api, team["head"])).json()["id"])

    class _Closing:
        name = "gigachat"

        async def complete(self, messages, *, schema, purpose, request_id):  # noqa: ANN001, ANN201
            async with db_session_factory() as other:
                await other.execute(
                    sa.update(AiSummaryJob)
                    .where(AiSummaryJob.id == job_id)
                    .values(status="failed", error_code="interrupted")
                )
                await other.commit()
            return LlmReply(
                text=json.dumps({"headline": "Итог."}, ensure_ascii=False),
                model="GigaChat-2-Pro",
                tokens_in=100,
                tokens_out=50,
                latency_ms=9,
            )

    monkeypatch.setattr(service, "get_provider", lambda name: _Closing())
    await service.run_job(job_id, session_factory=db_session_factory)
    job = await db_session.get(AiSummaryJob, job_id, populate_existing=True)
    assert job.status == "failed" and job.error_code == "interrupted" and job.result is None
    (generated,) = await _audit(db_session, "ai.summary_generated")
    assert generated.entity_id == job_id and generated.actor_user_id == team["head"].id
    assert generated.after["status"] == "interrupted_late"
    assert generated.after["tokens_in"] == 100 and generated.after["model"] == "GigaChat-2-Pro"


async def test_mark_interrupted_on_shutdown(db_session, db_session_factory, api, team) -> None:  # noqa: ANN001
    queued = uuid.UUID((await _post(api, team["head"])).json()["id"])
    done = uuid.UUID((await _post(api, team["head"], mode="rules")).json()["id"])
    marked = await service.mark_interrupted(
        [queued, done, uuid.uuid4()], session_factory=db_session_factory
    )
    assert marked == 1
    body = (await api.as_user(team["head"]).get(f"{API}/{queued}")).json()
    assert body["status"] == "failed" and body["error"]["code"] == "interrupted"
    assert (await api.as_user(team["head"]).get(f"{API}/{done}")).json()["status"] == "done"
    interrupted = [
        e for e in await _audit(db_session, "ai.summary_generated") if e.entity_id == queued
    ]
    assert [e.after["error_code"] for e in interrupted] == ["interrupted"]
    # новый запрос с тем же ключом — новая задача, а не присоединение к мёртвой
    again = await _post(api, team["head"])
    assert again.status_code == 202 and again.json()["id"] != str(queued)


async def test_run_job_ignores_non_queued(db_session_factory, api, team) -> None:  # noqa: ANN001
    job_id = uuid.UUID((await _post(api, team["head"], mode="rules")).json()["id"])
    await service.run_job(job_id, session_factory=db_session_factory)  # done — без действий
    await service.run_job(uuid.uuid4(), session_factory=db_session_factory)


# --------------------------------------------------------------------------------------
# Миграция 0010
# --------------------------------------------------------------------------------------


def test_migration_grants_permission_to_admin_and_head(migrated_db) -> None:  # noqa: ANN001
    rows = migrated_db.seed_rows
    roles = {row["id"]: row["code"] for row in rows["app_role"]}
    holders = {
        roles[link["role_id"]]
        for link in rows["app_role_permission"]
        if link["permission_code"] == "reports:ai_summary"
    }
    assert holders == {"admin", "head_kam"}
    (permission,) = [row for row in rows["app_permission"] if row["code"] == "reports:ai_summary"]
    assert permission["is_scoped"] is True and permission["group_code"] == "reports"
    assert "ai_summary_job" not in migrated_db.seed_tables  # таблица пустая после миграций
