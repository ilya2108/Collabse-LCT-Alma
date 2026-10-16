"""Задачи «Сводки ИИ»: создание (кэш, дедупликация, режим по правилам), выполнение,
чтение с подстановкой ФИО, аудит.

POST (`create_job`): права и флаг проверены зависимостью; область — `ai_scope` (при фильтре
«все процессы» она обязана покрывать все процессы отчёта, иначе 403 с подсказкой выбрать
процесс); отчёты собираются в запросе (кэш отчётов 60 с — руководитель обычно уже на
странице) → факты → sha256. Режим `rules` — сводка сразу (200). Иначе: готовая сводка с тем
же ключом за последний час — 200 `reused` («Обновить» с `force` — тоже, если она моложе
`AI_SUMMARY_FORCE_COOLDOWN_SECONDS`); задача в полёте — присоединение (200 `reused`);
новых задач GigaChat у пользователя за час больше `AI_SUMMARY_USER_HOURLY_LIMIT` — задача
сразу `failed: rate_limited` (сводка по правилам доступна); иначе `INSERT … ON CONFLICT DO
NOTHING` по частичному UNIQUE задач в полёте: строка вставлена — 202 и `runner.spawn`.

`run_job` — своя сессия на каждый шаг: `queued → running` (commit), перечитывание флага
(выключили — `failed: disabled` без вызова), вызов провайдера вне транзакции (GigaChat
отвечает около минуты), запись результата и аудит одной транзакцией. Каждое фактическое
обращение к провайдеру попадает в аудит `ai.summary_generated` — даже если строку задачи
уже закрыли (stale-логика, рестарт).

Ключ кэша — ключ ЗАПРОСА: sha256(вид | КАМ | область | параметры | провайдер | версия
промпта). Цифры фактов в него не входят: на текущем периоде задержки растут каждую минуту, и
ключ с sha фактов менялся бы на каждом клике (новый вызов GigaChat вместо кэша). `input_sha256`
фактов хранится отдельно — для аудита. Свежие цифры — кнопка «Обновить» (`force`). Аудит без
текста сводки, фактов и ФИО.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import orjson
from fastapi import Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access import DealScope
from app.core.audit import client_ip, write_audit
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.errors import conflict, forbidden, not_found, validation_error
from app.models.ai_summary import PENDING_STATUSES, AiSummaryJob
from app.models.user import AppUser
from app.modules.admin.service import get_flags
from app.modules.ai_summary import runner
from app.modules.ai_summary.facts import (
    WORKFLOW_LABELS,
    Facts,
    build_kam_facts,
    build_team_facts,
)
from app.modules.ai_summary.gateway import ProviderError, error_message
from app.modules.ai_summary.parse import ParseError, parse_llm_output, response_schema
from app.modules.ai_summary.prompt import (
    PROMPT_VERSION,
    PURPOSES,
    build_messages,
    repair_messages,
)
from app.modules.ai_summary.providers import get_provider
from app.modules.ai_summary.rules import rules_sections
from app.modules.ai_summary.schemas import AiSummaryCreate
from app.modules.ai_summary.scope import FLAG, ai_scope
from app.modules.ai_summary.verify import (
    CANONICAL_ALIAS_RE,
    VerificationFailed,
    mentioned_aliases,
    verify,
)
from app.modules.reporting import kam_activity
from app.modules.reporting.kam_activity import (
    REPORT_TZ,
    KamActivityParams,
    build_kam_activity_summary,
    resolve_params,
)
from app.modules.reporting.service import report_scope
from app.modules.reporting.sla import build_sla, build_sla_kam, short_person

logger = logging.getLogger("app.ai_summary")

AUDIT_REQUESTED = "ai.summary_requested"
AUDIT_GENERATED = "ai.summary_generated"
ENTITY_TYPE = "ai_summary_job"
POLL_AFTER_MS = 2000
# вторая попытка (ремонт JSON) — только если с начала задачи прошло меньше 200 с
REPAIR_DEADLINE_SECONDS = 200.0
SECTION_KEYS = ("headline", "going_well", "lagging", "attention", "questions")
EMPTY_CHECKS: dict[str, Any] = {
    "numbers_total": 0,
    "numbers_matched": 0,
    "sentences_removed": 0,
    "advice_removed": 0,
    "removed_by_reason": {"numbers": 0, "advice": 0, "evaluative": 0, "other": 0},
}
# коды задач, которые не обращались к провайдеру и не идут в часовой лимит пользователя
NO_CALL_CODES = ("rate_limited", "disabled")


# --------------------------------------------------------------------------------------
# Факты и ключи
# --------------------------------------------------------------------------------------


def request_params(body: AiSummaryCreate) -> KamActivityParams:
    params = body.params
    return KamActivityParams(
        date_from=params.from_,
        date_to=params.to,
        workflow_type=params.workflow_type,
        # для сводки по КАМу фильтр kam_id не участвует, как у /reports/sla/kams/{id}
        kam_ids=() if body.kind == "kam" else tuple(params.kam_id),
        university_ids=tuple(params.university_id),
        direction_ids=tuple(params.direction_id),
    )


def params_json(params: KamActivityParams) -> dict[str, Any]:
    """Эхо параметров после подстановки периода (ключ `direction_id` — всегда)."""
    data = params.as_json()
    data.setdefault("direction_id", [])
    return data


async def collect_facts(
    session: AsyncSession,
    viewer: AppUser,
    kind: str,
    kam_id: uuid.UUID | None,
    params: KamActivityParams,
    scope: DealScope,
) -> Facts:
    """Отчёты (те же функции и кэш, что у вкладок) → факты. 400/403/404 — как у отчётов."""
    if kind == "team":
        summary = await build_kam_activity_summary(session, viewer, params, scope=scope)
        sla = await build_sla(session, viewer, params, scope=scope)
        return build_team_facts(summary, sla)
    assert kam_id is not None
    sla_kam = await build_sla_kam(session, viewer, kam_id, params, scope=scope)
    activity = await build_kam_activity_summary(
        session, viewer, replace(params, kam_ids=(kam_id,)), scope=scope
    )
    return build_kam_facts(str(kam_id), activity, sla_kam)


def make_cache_key(
    *,
    kind: str,
    kam_id: uuid.UUID | None,
    scope_key: str,
    params: dict[str, Any],
    provider: str,
) -> str:
    """Ключ запроса (кэш на час и дедупликация задач в полёте) — без sha фактов."""
    raw = "|".join(
        (
            kind,
            str(kam_id or ""),
            scope_key,
            orjson.dumps(params, option=orjson.OPT_SORT_KEYS).decode(),
            provider,
            PROMPT_VERSION,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_full_workflow_scope(user: AppUser, scope: DealScope, workflow_type: str | None) -> None:
    """Фильтр «все процессы», а право на сводки — не во всех процессах отчёта: сводка вышла
    бы по части данных при полном экране. 403 с подсказкой выбрать процесс в фильтре."""
    if workflow_type is not None:
        return
    report = set(report_scope(user).workflow_types)
    allowed = set(scope.workflow_types)
    if allowed == report:
        return
    labels = " и ".join(WORKFLOW_LABELS.get(wf, wf) for wf in sorted(allowed))
    raise forbidden(
        f"Сводка ИИ вам доступна только по процессу {labels}, а отчёт показывает все "
        "процессы — выберите процесс в фильтре отчёта"
    )


# --------------------------------------------------------------------------------------
# Хранилище задач
# --------------------------------------------------------------------------------------


def _stale_cutoff() -> Any:
    return func.now() - timedelta(seconds=get_settings().ai_summary_job_stale_seconds)


def _interrupted_values() -> dict[str, Any]:
    return {
        "status": "failed",
        "error_code": "interrupted",
        "error_message": error_message("interrupted"),
        "finished_at": func.now(),
    }


async def maintenance(session: AsyncSession) -> None:
    """Ленивое обслуживание: зависшие задачи → `failed: interrupted`, старые — удалить."""
    settings = get_settings()
    await session.execute(
        update(AiSummaryJob)
        .where(AiSummaryJob.status.in_(PENDING_STATUSES), AiSummaryJob.created_at < _stale_cutoff())
        .values(**_interrupted_values())
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(AiSummaryJob)
        .where(
            AiSummaryJob.created_at
            < func.now() - timedelta(days=settings.ai_summary_retention_days)
        )
        .execution_options(synchronize_session=False)
    )


async def _find_done(
    session: AsyncSession, cache_key: str, *, ttl_seconds: int | None = None
) -> AiSummaryJob | None:
    seconds = get_settings().ai_summary_cache_ttl_seconds if ttl_seconds is None else ttl_seconds
    ttl = timedelta(seconds=seconds)
    return (
        await session.execute(
            select(AiSummaryJob)
            .where(
                AiSummaryJob.cache_key == cache_key,
                AiSummaryJob.status == "done",
                AiSummaryJob.finished_at > func.now() - ttl,
            )
            .order_by(AiSummaryJob.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _find_inflight(session: AsyncSession, cache_key: str) -> AiSummaryJob | None:
    return (
        await session.execute(
            select(AiSummaryJob)
            .where(
                AiSummaryJob.cache_key == cache_key,
                AiSummaryJob.status.in_(PENDING_STATUSES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _user_jobs_last_hour(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Новые задачи провайдера (не rules) пользователя за скользящий час."""
    return (
        await session.execute(
            select(func.count())
            .select_from(AiSummaryJob)
            .where(
                AiSummaryJob.requested_by == user_id,
                AiSummaryJob.provider != "rules",
                AiSummaryJob.created_at > func.now() - timedelta(hours=1),
                or_(
                    AiSummaryJob.error_code.is_(None),
                    AiSummaryJob.error_code.not_in(NO_CALL_CODES),
                ),
            )
        )
    ).scalar_one()


async def _insert(
    session: AsyncSession, values: dict[str, Any], *, dedup: bool
) -> uuid.UUID | None:
    stmt = insert(AiSummaryJob).values(**values)
    if dedup:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[AiSummaryJob.cache_key],
            index_where=AiSummaryJob.status.in_(PENDING_STATUSES),
        )
    return (await session.execute(stmt.returning(AiSummaryJob.id))).scalar_one_or_none()


async def _load(session: AsyncSession, job_id: uuid.UUID) -> AiSummaryJob:
    job = await session.get(AiSummaryJob, job_id, populate_existing=True)
    assert job is not None
    return job


async def _audit_requested(
    session: AsyncSession,
    *,
    user: AppUser,
    job: AiSummaryJob,
    body: AiSummaryCreate,
    outcome: str,
    request: Request | None,
) -> None:
    await write_audit(
        session,
        actor_user_id=user.id,
        action=AUDIT_REQUESTED,
        entity_type=ENTITY_TYPE,
        entity_id=job.id,
        after={
            "kind": job.kind,
            "kam_id": str(job.kam_id) if job.kam_id else None,
            "mode": body.mode,
            "provider": job.provider,
            "force": body.force,
            "params": job.params,
            "input_sha256": job.input_sha256,
            "outcome": outcome,
        },
        ip=client_ip(request),
    )


async def _audit_generated(
    session: AsyncSession, job: AiSummaryJob, checks: dict[str, Any] | None
) -> None:
    await write_audit(
        session,
        actor_user_id=job.requested_by,
        action=AUDIT_GENERATED,
        entity_type=ENTITY_TYPE,
        entity_id=job.id,
        after={
            "status": job.status,
            "provider": job.provider,
            "model": job.model,
            "prompt_version": job.prompt_version,
            "tokens_in": job.tokens_in,
            "tokens_out": job.tokens_out,
            "latency_ms": job.latency_ms,
            "attempts": job.attempts,
            "input_sha256": job.input_sha256,
            "checks": checks,
            "error_code": job.error_code,
        },
    )


# --------------------------------------------------------------------------------------
# POST /reports/ai-summaries
# --------------------------------------------------------------------------------------


async def create_job(
    session: AsyncSession,
    user: AppUser,
    body: AiSummaryCreate,
    request: Request | None = None,
) -> tuple[dict[str, Any], int]:
    """(ответ `AiSummaryJobOut`, HTTP-статус 200 | 202)."""
    if body.kind == "kam" and body.kam_id is None:
        raise validation_error(
            details=[
                {
                    "field": "kam_id",
                    "code": "required",
                    "message": "Для сводки по КАМу укажите kam_id",
                }
            ]
        )
    scope = ai_scope(user)
    if scope.is_empty:
        raise forbidden("Сводки ИИ недоступны вашей роли")
    require_full_workflow_scope(user, scope, body.params.workflow_type)
    today = kam_activity._now().astimezone(REPORT_TZ).date()
    params = resolve_params(request_params(body), today=today)
    kam_id = body.kam_id if body.kind == "kam" else None
    facts = await collect_facts(session, user, body.kind, kam_id, params, scope)

    settings = get_settings()
    provider = "rules" if body.mode == "rules" else settings.ai_summary_provider
    echo = params_json(params)
    input_sha256 = facts.sha256
    scope_key = scope.cache_key()
    cache_key = make_cache_key(
        kind=body.kind, kam_id=kam_id, scope_key=scope_key, params=echo, provider=provider
    )
    values: dict[str, Any] = {
        "kind": body.kind,
        "kam_id": kam_id,
        "scope_key": scope_key,
        "params": echo,
        "mode": body.mode,
        "provider": provider,
        "prompt_version": PROMPT_VERSION,
        "input_sha256": input_sha256,
        "cache_key": cache_key,
        "facts": facts.facts,
        "aliases": facts.aliases,
        "requested_by": user.id,
    }
    await maintenance(session)

    if provider == "rules":
        job = await _create_rules_job(session, values, facts)
        await _audit_requested(
            session, user=user, job=job, body=body, outcome="rules_done", request=request
        )
        await _audit_generated(session, job, (job.result or {}).get("checks"))
        await session.commit()
        _log_finished(job)
        return await render_job(session, job), 200

    # «Обновить» (force) не чаще раза в N секунд на ключ: свежая готовая сводка и так актуальна
    cooldown = settings.ai_summary_force_cooldown_seconds
    cached = (
        await _find_done(session, cache_key, ttl_seconds=cooldown)
        if body.force
        else await _find_done(session, cache_key)
    )
    if cached is not None and (not body.force or cooldown > 0):
        outcome = "reused_done_cooldown" if body.force else "reused_done"
        await _audit_requested(
            session, user=user, job=cached, body=body, outcome=outcome, request=request
        )
        await session.commit()
        return await render_job(session, cached, reused=True), 200

    inflight = await _find_inflight(session, cache_key)
    if inflight is not None:
        await _audit_requested(
            session, user=user, job=inflight, body=body, outcome="reused_inflight", request=request
        )
        await session.commit()
        return await render_job(session, inflight, reused=True), 200

    limit = settings.ai_summary_user_hourly_limit
    if limit and await _user_jobs_last_hour(session, user.id) >= limit:
        job = await _create_failed_job(session, values, "rate_limited")
        await _audit_requested(
            session, user=user, job=job, body=body, outcome="rate_limited", request=request
        )
        await session.commit()
        logger.info("ai_summary.rate_limited user_id=%s limit=%s", user.id, limit)
        return await render_job(session, job), 200

    for _attempt in range(2):
        job_id = await _insert(session, {**values, "status": "queued"}, dedup=True)
        if job_id is not None:
            job = await _load(session, job_id)
            await _audit_requested(
                session, user=user, job=job, body=body, outcome="queued", request=request
            )
            await session.commit()
            runner.spawn(job.id)
            logger.info(
                "ai_summary.job_queued job_id=%s kind=%s provider=%s", job.id, job.kind, provider
            )
            return await render_job(session, job), 202
        inflight = await _find_inflight(session, cache_key)
        if inflight is not None:
            await _audit_requested(
                session,
                user=user,
                job=inflight,
                body=body,
                outcome="reused_inflight",
                request=request,
            )
            await session.commit()
            return await render_job(session, inflight, reused=True), 200
        # задача в полёте успела завершиться между INSERT и SELECT
        finished = await _find_done(session, cache_key)
        if finished is not None:
            await _audit_requested(
                session, user=user, job=finished, body=body, outcome="reused_done", request=request
            )
            await session.commit()
            return await render_job(session, finished, reused=True), 200
    raise conflict("Сводка с этими параметрами сейчас формируется — повторите запрос")


async def _create_failed_job(
    session: AsyncSession, values: dict[str, Any], code: str
) -> AiSummaryJob:
    """Задача, завершённая без обращения к провайдеру (лимит пользователя)."""
    job_id = await _insert(
        session,
        {
            **values,
            "status": "failed",
            "error_code": code,
            "error_message": error_message(code),
            "attempts": 0,
            "finished_at": func.now(),
        },
        dedup=False,
    )
    assert job_id is not None
    return await _load(session, job_id)


async def _create_rules_job(
    session: AsyncSession, values: dict[str, Any], facts: Facts
) -> AiSummaryJob:
    verified = verify(rules_sections(facts.facts), facts.facts, facts.aliases, enforce=False)
    if verified.checks["sentences_removed"]:
        # сводка по правилам обязана проходить проверку целиком — это ошибка шаблона
        logger.warning("ai_summary.rules_removed removed=%s", verified.checks["sentences_removed"])
    job_id = await _insert(
        session,
        {
            **values,
            "status": "done",
            "result": {
                "source": "rules",
                "model": None,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "sections": verified.sections,
                "checks": verified.checks,
            },
            "attempts": 0,
            "latency_ms": 0,
            "started_at": func.now(),
            "finished_at": func.now(),
        },
        dedup=False,
    )
    assert job_id is not None
    return await _load(session, job_id)


# --------------------------------------------------------------------------------------
# Выполнение задачи
# --------------------------------------------------------------------------------------


@dataclass
class _Run:
    attempts: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    model: str | None = None
    checks: dict[str, Any] | None = None


async def _generate(
    run: _Run,
    *,
    job_id: uuid.UUID,
    kind: str,
    provider_name: str,
    facts: dict[str, Any],
    aliases: dict[str, str],
    started: float,
) -> dict[str, Any]:
    """Провайдер → JSON → проверка; `ProviderError` при любом сбое (код задачи)."""
    provider = get_provider(provider_name)
    messages = build_messages(kind, facts)
    schema = response_schema()
    parsed = None
    last_error = "пустой ответ"
    for attempt in range(get_settings().ai_summary_llm_attempts):
        if attempt and time.monotonic() - started >= REPAIR_DEADLINE_SECONDS:
            break
        reply = await provider.complete(
            messages, schema=schema, purpose=PURPOSES[kind], request_id=str(job_id)
        )
        run.attempts += 1
        run.tokens_in += reply.tokens_in
        run.tokens_out += reply.tokens_out
        run.latency_ms += reply.latency_ms
        run.model = reply.model or run.model
        try:
            parsed = parse_llm_output(reply.text)
            break
        except ParseError as exc:
            last_error = exc.message
            messages = [*messages, *repair_messages(reply.text, exc.message)]
    if parsed is None:
        logger.warning("ai_summary.bad_json job_id=%s error=%s", job_id, last_error[:200])
        raise ProviderError("llm_bad_output")
    try:
        verified = verify(parsed.model_dump(), facts, aliases)
    except VerificationFailed as exc:
        run.checks = exc.checks
        logger.warning("ai_summary.verify_failed job_id=%s reason=%s", job_id, exc.reason)
        raise ProviderError("llm_bad_output") from exc
    run.checks = verified.checks
    return {
        "source": provider.name,
        "model": run.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": verified.sections,
        "checks": verified.checks,
    }


async def run_job(
    job_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Выполняет задачу `queued` (другие статусы — выход без действий)."""
    factory = session_factory or get_session_factory()
    started = time.monotonic()
    async with factory() as session:
        row = (
            await session.execute(
                update(AiSummaryJob)
                .where(AiSummaryJob.id == job_id, AiSummaryJob.status == "queued")
                .values(status="running", started_at=func.now())
                .returning(
                    AiSummaryJob.kind,
                    AiSummaryJob.provider,
                    AiSummaryJob.facts,
                    AiSummaryJob.aliases,
                    AiSummaryJob.requested_by,
                    AiSummaryJob.prompt_version,
                    AiSummaryJob.input_sha256,
                )
                .execution_options(synchronize_session=False)
            )
        ).first()
        await session.commit()
        # флаг — аварийный выключатель: задачи в очереди тоже не уходят в GigaChat
        enabled = bool(row is not None and (await get_flags(session)).get(FLAG))
    if row is None:
        return
    kind, provider_name, facts, aliases, requested_by, prompt_version, input_sha256 = row

    run = _Run()
    result: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
    try:
        if not enabled:
            error_code, message = "disabled", error_message("disabled")
        elif provider_name == "rules":
            verified = verify(rules_sections(facts), facts, aliases, enforce=False)
            run.checks = verified.checks
            result = {
                "source": "rules",
                "model": None,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "sections": verified.sections,
                "checks": verified.checks,
            }
        else:
            result = await _generate(
                run,
                job_id=job_id,
                kind=kind,
                provider_name=provider_name,
                facts=facts,
                aliases=aliases,
                started=started,
            )
    except ProviderError as exc:
        error_code, message = exc.code, exc.message
    except Exception:  # noqa: BLE001 — задача завершается failed, причина — в логе
        logger.exception("ai_summary.job_error job_id=%s", job_id)
        error_code, message = "internal", error_message("internal")

    async with factory() as session:
        job = await session.get(AiSummaryJob, job_id, populate_existing=True)
        if job is None or job.status != "running":
            logger.warning("ai_summary.job_gone job_id=%s attempts=%s", job_id, run.attempts)
            if run.attempts:
                # данные ушли к провайдеру и токены потрачены — фиксируем и без строки задачи
                await write_audit(
                    session,
                    actor_user_id=requested_by,
                    action=AUDIT_GENERATED,
                    entity_type=ENTITY_TYPE,
                    entity_id=job_id,
                    after={
                        "status": "interrupted_late",
                        "provider": provider_name,
                        "model": run.model,
                        "prompt_version": prompt_version,
                        "tokens_in": run.tokens_in,
                        "tokens_out": run.tokens_out,
                        "latency_ms": run.latency_ms,
                        "attempts": run.attempts,
                        "input_sha256": input_sha256,
                        "checks": run.checks,
                        "error_code": error_code,
                    },
                )
                await session.commit()
            return
        job.status = "done" if error_code is None else "failed"
        job.result = result if error_code is None else None
        job.error_code = error_code
        job.error_message = message
        job.model = run.model
        job.tokens_in = run.tokens_in
        job.tokens_out = run.tokens_out
        job.latency_ms = run.latency_ms
        job.attempts = run.attempts
        job.finished_at = datetime.now(timezone.utc)
        await _audit_generated(session, job, run.checks)
        await session.commit()
        _log_finished(job)


async def mark_interrupted(
    job_ids: list[uuid.UUID],
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Задачи процесса, отменённые при остановке пода (rollout, scale-down HPA), сразу
    закрываются `failed: interrupted` — не висят «Формируется…» до stale-порога и не
    присоединяют к себе новые запросы. В аудит — факт прерванного обращения."""
    if not job_ids:
        return 0
    factory = session_factory or get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                update(AiSummaryJob)
                .where(AiSummaryJob.id.in_(job_ids), AiSummaryJob.status.in_(PENDING_STATUSES))
                .values(**_interrupted_values())
                .returning(
                    AiSummaryJob.id,
                    AiSummaryJob.requested_by,
                    AiSummaryJob.provider,
                    AiSummaryJob.prompt_version,
                    AiSummaryJob.input_sha256,
                )
                .execution_options(synchronize_session=False)
            )
        ).all()
        for job_id, requested_by, provider, prompt_version, input_sha256 in rows:
            await write_audit(
                session,
                actor_user_id=requested_by,
                action=AUDIT_GENERATED,
                entity_type=ENTITY_TYPE,
                entity_id=job_id,
                after={
                    "status": "failed",
                    "provider": provider,
                    "prompt_version": prompt_version,
                    "input_sha256": input_sha256,
                    "error_code": "interrupted",
                },
            )
        await session.commit()
    return len(rows)


def _log_finished(job: AiSummaryJob) -> None:
    logger.info(
        "ai_summary.job_finished job_id=%s kind=%s provider=%s status=%s error_code=%s "
        "latency_ms=%s tokens_in=%s tokens_out=%s attempts=%s",
        job.id,
        job.kind,
        job.provider,
        job.status,
        job.error_code,
        job.latency_ms,
        job.tokens_in,
        job.tokens_out,
        job.attempts,
    )


# --------------------------------------------------------------------------------------
# GET /reports/ai-summaries/{job_id} и ответ
# --------------------------------------------------------------------------------------


async def get_job(session: AsyncSession, user: AppUser, job_id: uuid.UUID) -> dict[str, Any]:
    """Задача в области пользователя (иначе 404 — существование не раскрываем)."""
    job = await session.get(AiSummaryJob, job_id, populate_existing=True)
    if job is None or job.scope_key != ai_scope(user).cache_key():
        raise not_found("Сводка не найдена")
    if job.status in PENDING_STATUSES:
        marked = (
            await session.execute(
                update(AiSummaryJob)
                .where(
                    AiSummaryJob.id == job_id,
                    AiSummaryJob.status.in_(PENDING_STATUSES),
                    AiSummaryJob.created_at < _stale_cutoff(),
                )
                .values(**_interrupted_values())
                .returning(AiSummaryJob.id)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if marked is not None:
            await session.commit()
            job = await _load(session, job_id)
    return await render_job(session, job)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(
        select(AppUser.id, AppUser.full_name, AppUser.username).where(AppUser.id.in_(ids))
    )
    return {user_id: short_person(full_name or username) for user_id, full_name, username in rows}


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


def depseudonymize(text: str, names: dict[str, str]) -> str:
    """`КАМ-03` → «Иванов И. И.»; псевдоним без пользователя остаётся как есть."""
    return CANONICAL_ALIAS_RE.sub(lambda m: names.get(m.group(0), m.group(0)), text)


async def _queue_ahead(session: AsyncSession, job: AiSummaryJob) -> int | None:
    if job.status not in PENDING_STATUSES or job.provider != "gigachat":
        return None
    return (
        await session.execute(
            select(func.count())
            .select_from(AiSummaryJob)
            .where(
                AiSummaryJob.provider == "gigachat",
                AiSummaryJob.status.in_(PENDING_STATUSES),
                AiSummaryJob.created_at < job.created_at,
            )
        )
    ).scalar_one()


async def render_job(
    session: AsyncSession, job: AiSummaryJob, *, reused: bool = False
) -> dict[str, Any]:
    """`AiSummaryJobOut`: псевдонимы → «Фамилия И. О.» (только при чтении, на стороне CRM)."""
    aliases = {alias: _uuid(kam_id) for alias, kam_id in (job.aliases or {}).items()}
    ids = {kam_id for kam_id in aliases.values() if kam_id is not None}
    if job.kam_id is not None:
        ids.add(job.kam_id)
    names = await _names(session, ids)
    alias_names = {alias: names[kam_id] for alias, kam_id in aliases.items() if kam_id in names}

    result: dict[str, Any] | None = None
    if job.status == "done" and job.result:
        sections = job.result.get("sections") or {}
        rendered: dict[str, Any] = {
            "headline": depseudonymize(str(sections.get("headline") or ""), alias_names)
        }
        for key in SECTION_KEYS[1:]:
            rendered[key] = [depseudonymize(item, alias_names) for item in sections.get(key) or ()]
        people = []
        if job.kind == "team":
            for alias in mentioned_aliases(sections):
                kam_id = aliases.get(alias)
                if kam_id is not None and alias in alias_names:
                    people.append(
                        {"alias": alias, "kam_id": str(kam_id), "name": alias_names[alias]}
                    )
        result = {
            "source": job.result.get("source") or job.provider,
            "model": job.result.get("model"),
            "generated_at": job.result.get("generated_at"),
            "sections": rendered,
            "people": people,
            "checks": job.result.get("checks") or dict(EMPTY_CHECKS),
        }
    error: dict[str, Any] | None = None
    if job.status == "failed":
        code = job.error_code or "internal"
        error = {
            "code": code,
            "message": job.error_message or error_message(code),
            # флаг выключен — недоступна и сводка по правилам (те же право и флаг)
            "rules_available": code != "disabled",
        }
    pending = job.status in PENDING_STATUSES
    return {
        "id": str(job.id),
        "kind": job.kind,
        "kam_id": str(job.kam_id) if job.kam_id else None,
        "kam_name": names.get(job.kam_id) if job.kam_id else None,
        "status": job.status,
        "mode": job.mode,
        "provider": job.provider,
        "params": job.params,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "queue_ahead": await _queue_ahead(session, job),
        "poll_after_ms": POLL_AFTER_MS if pending else None,
        "reused": reused,
        "result": result,
        "error": error,
    }
