"""Демо-стенд без БД: якорь дат сида, палитра и пороги этапов, форма демо-плана (P0-9).

Проверяет то, на чём держится показ 23.10: даты считаются от «сегодня» (или
DEMO_ANCHOR_DATE), закрытия приходятся на последние 3–20 дней, открытые заявки в
большинстве «в норме», вузов 40–60 с реальными названиями, цвета этапов — из
STAGE_COLOR_PRESET фронта, у этапа обучения порог 120 дней.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tools import seed
from app.tools.seed_activity import (
    CLOSE_GAP_DAYS,
    KAMS,
    NEW_UNIVERSITIES,
    build_activity_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKENS_TS = REPO_ROOT / "frontend" / "src" / "shared" / "config" / "tokens.ts"
MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
# дни показа и «далёкие» якоря: план не должен зависеть от хакатонного окна
ANCHORS = (
    datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc),
    datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc),
)


def _seed_spec(code: str) -> dict:
    return json.loads((seed.SEEDS_DIR / f"{code}.json").read_text(encoding="utf-8"))


def _thresholds(code: str) -> dict[str, int | None]:
    spec = _seed_spec(code)
    return {
        stage["code"]: stage["stuck_threshold_days"] or spec["stuck_threshold_days"]
        for stage in spec["stages"]
        if not stage["is_terminal"]
    }


# --- якорь дат ---------------------------------------------------------------------------


class TestDemoAnchor:
    def test_empty_means_now(self) -> None:
        assert seed.resolve_demo_anchor(None, now=NOW) == NOW
        assert seed.resolve_demo_anchor("  ", now=NOW) == NOW

    def test_date_is_nine_msk(self) -> None:
        anchor = seed.resolve_demo_anchor("2026-10-23", now=NOW)
        assert anchor == datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)
        assert anchor.astimezone(MSK).hour == seed.DEMO_ANCHOR_HOUR_MSK

    def test_today_is_not_in_future(self) -> None:
        early = datetime(2026, 10, 23, 4, 30, tzinfo=timezone.utc)  # 07:30 МСК
        assert seed.resolve_demo_anchor("2026-10-23", now=early) == early

    def test_iso_datetime(self) -> None:
        assert seed.resolve_demo_anchor("2026-10-23T10:00", now=NOW) == datetime(
            2026, 10, 23, 7, 0, tzinfo=timezone.utc
        )
        assert seed.resolve_demo_anchor("2026-10-23T10:00+00:00", now=NOW) == datetime(
            2026, 10, 23, 10, 0, tzinfo=timezone.utc
        )

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError, match="DEMO_ANCHOR_DATE"):
            seed.resolve_demo_anchor("23.10.2026", now=NOW)

    def test_cli_anchor_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []

        async def fake_amain(*, reset: bool = False) -> None:
            calls.append(reset)

        monkeypatch.setattr(seed, "_amain", fake_amain)
        monkeypatch.setattr(seed, "_NOW", seed.demo_now())
        assert seed.main(["--reset", "--anchor", "2026-10-01"]) == 0
        assert calls == [True]
        assert seed.demo_now() == datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)


# --- схемы процессов: палитра и пороги ----------------------------------------------------


def _stage_color_preset() -> list[str]:
    source = TOKENS_TS.read_text(encoding="utf-8")
    block = re.search(r"export const STAGE_COLOR_PRESET = \[(.*?)\] as const", source, re.S)
    assert block, "STAGE_COLOR_PRESET не найден в tokens.ts"
    return [color.upper() for color in re.findall(r"'(#[0-9A-Fa-f]{6})'", block.group(1))]


@pytest.mark.parametrize("code", ["b2b", "b2c"])
def test_stage_colors_from_preset(code: str) -> None:
    preset = _stage_color_preset()
    assert len(preset) == 8
    colors = [stage["color"].upper() for stage in _seed_spec(code)["stages"]]
    assert set(colors) <= set(preset), set(colors) - set(preset)
    assert len(set(colors)) == len(colors), "у этапов одного процесса цвета не повторяются"
    by_code = {stage["code"]: stage["color"].upper() for stage in _seed_spec(code)["stages"]}
    assert by_code["completed"] == "#09AD4D"  # success
    assert by_code["new"] == "#585D69"  # нейтральный


@pytest.mark.parametrize("code", ["b2b", "b2c"])
def test_learning_stage_threshold(code: str) -> None:
    spec = _seed_spec(code)
    stages = {stage["code"]: stage for stage in spec["stages"]}
    assert stages["in_learning"]["stuck_threshold_days"] == 120
    for stage in spec["stages"]:
        value = stage["stuck_threshold_days"]
        assert value is None or 1 <= value <= 365
        if stage["is_terminal"]:
            assert value is None


def test_threshold_clamped_to_db_limit(capsys: pytest.CaptureFixture[str]) -> None:
    stage = {"name": "Передано в обучение", "stuck_threshold_days": 120}
    assert seed._clamped_threshold(stage, 365) == 120
    assert seed._clamped_threshold(stage, 60) == 60
    assert "выше предела БД (60)" in capsys.readouterr().out
    assert seed._clamped_threshold({"name": "x", "stuck_threshold_days": None}, 60) is None


# --- демо-план: состав и «свежесть» --------------------------------------------------------


def test_roster_kams_and_universities() -> None:
    new_kams = [username for username, *_ in KAMS if username not in ("kam1@demo", "kam2@demo")]
    assert len(new_kams) == 18
    names = [spec["name"] for spec in seed.UNIVERSITIES] + [row[0] for row in NEW_UNIVERSITIES]
    assert 40 <= len(names) <= 60
    assert len(set(names)) == len(names)
    shorts = {row[1] for row in NEW_UNIVERSITIES}
    # публично подтверждённые партнёры «ИТ Школы» есть в сиде
    assert {"МЭИ", "МТУСИ", "Мосполитех", "РАНХиГС", "Центральный университет"} <= shorts
    assert {"ИТМО", "ТГУ"} <= shorts  # закреплены, но без контакта (отчёт КАМов)


@pytest.mark.parametrize("now", ANCHORS, ids=lambda d: d.date().isoformat())
def test_plan_closures_are_recent_but_not_today(now: datetime) -> None:
    plan = build_activity_plan(now)
    closed = [
        (now - deal.steps[-1].at).total_seconds() / 86400
        for deal in plan.deals
        if deal.steps[-1].stage in ("completed", "rejected")
    ]
    assert closed, "в плане есть закрытые заявки"
    assert min(closed) >= CLOSE_GAP_DAYS
    assert sum(1 for days in closed if 3 <= days <= 20) >= 4  # «Завершено за месяц» не 0


@pytest.mark.parametrize("now", ANCHORS, ids=lambda d: d.date().isoformat())
def test_plan_open_deals_mostly_on_time(now: datetime) -> None:
    plan = build_activity_plan(now)
    thresholds = {code: _thresholds(code) for code in ("b2b", "b2c")}
    opened = [d for d in plan.deals if d.steps[-1].stage not in ("completed", "rejected")]
    stuck = [
        deal
        for deal in opened
        if (now - deal.steps[-1].at).days > thresholds[deal.pipeline][deal.steps[-1].stage]
    ]
    assert len(opened) >= 30
    assert stuck, "зависшие есть — фактура для эскалаций и отчёта"
    assert len(stuck) / len(opened) <= 0.3, f"зависших {len(stuck)} из {len(opened)}"


def test_plan_lms_only_writes_history() -> None:
    # ЛМС заявок не создаёт и этапы не двигает: событие — запись без смены этапа,
    # закрывает заявку человек (integrations/service.py: автоперехода нет)
    plan = build_activity_plan(NOW)
    lms_events = 0
    for deal in plan.deals:
        assert deal.steps[0].kind == "manual"
        for previous, step in zip(deal.steps, deal.steps[1:], strict=False):
            if step.kind == "integration":
                lms_events += 1
                assert step.stage == previous.stage == "in_learning"
                assert step.actor is None and step.reason == "lms.learning.completed"
            else:
                assert step.kind == "manual" and step.stage != previous.stage
    assert lms_events > 0


def test_base_closures_within_month() -> None:
    closed = [
        spec
        for spec in seed.DEALS_B2B + seed.DEALS_B2C
        if spec["stage"] in ("completed", "rejected")
    ]
    assert closed
    assert all(3 <= spec["stage_days"] <= 20 for spec in closed)


def test_reset_keeps_users_and_audit() -> None:
    assert {"app_user", "audit_log", "app_role", "workflow"} <= seed.RESET_KEEP_TABLES
    assert "deal" not in seed.RESET_KEEP_TABLES and "university" not in seed.RESET_KEEP_TABLES
    assert not any(pattern.startswith("tg:") for pattern in seed.RESET_CACHE_PATTERNS)
