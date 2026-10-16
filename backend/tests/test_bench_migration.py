"""Инструмент замера массового переноса (app/tools/bench_migration.py): отчёт и защита БД."""

from __future__ import annotations

import pytest

from app.tools import bench_migration as bench


def _result(**overrides: object) -> bench.BenchResult:
    values: dict[str, object] = {
        "n": 10_000,
        "mode": "batch",
        "kams": 20,
        "migrated": 10_000,
        "approve_ms": 331.6,
        "lock_wait_ms": 1.0,
        "lock_held_ms": 334.2,
        "migrate_ms": 318.0,
        "probe_wait_ms": 326.4,
        "probe_outcome": "ok",
        "history_rows": 10_000,
        "notification_rows": 20,
        "digest_notifications": 20,
        "wal_mb": 12.34,
    }
    values.update(overrides)
    return bench.BenchResult(**values)  # type: ignore[arg-type]


def test_render_markdown_header_and_numbers() -> None:
    env = bench.BenchEnvironment(
        postgres="PostgreSQL 16.15",
        platform="Darwin 25.6.0 arm64",
        python="Python 3.12.7",
        date="2026-10-16 12:00 UTC",
    )
    legacy = _result(
        mode="legacy",
        approve_ms=30_120.4,
        lock_held_ms=30_100.0,
        probe_wait_ms=30_090.9,
        notification_rows=10_000,
        wal_mb=None,
    )
    busy = _result(n=250_000, probe_wait_ms=10_003.0, probe_outcome="workflow_busy")
    text = bench.render_markdown([legacy, _result(), busy], env)
    lines = text.splitlines()
    assert "PostgreSQL 16.15, Darwin 25.6.0 arm64, Python 3.12.7, 2026-10-16 12:00 UTC" in lines
    header = next(line for line in lines if line.startswith("| N |"))
    assert header == (
        "| N | Режим | Применение, мс | Лок удерживался, мс | Ожидание перехода, мс "
        "| Строк истории | Уведомлений | WAL, МБ |"
    )
    nbsp = " "
    assert (
        f"| 10{nbsp}000 | поштучный (до) | 30{nbsp}120 | 30{nbsp}100 | 30{nbsp}091 "
        f"| 10{nbsp}000 | 10{nbsp}000 | — |"
    ) in lines
    assert f"| 10{nbsp}000 | пакетный (CTE) | 332 | 334 | 326 | 10{nbsp}000 | 20 | 12,3 |" in lines
    assert any(f"| 10{nbsp}003 (workflow_busy) |" in line for line in lines)


def test_number_formatting() -> None:
    assert bench.fmt_int(1_234_567) == "1 234 567"
    assert bench.fmt_int(None) == "—"
    assert bench.fmt_float(2075.25) == "2 075,2"
    assert bench.fmt_float(0.06, 2) == "0,06"


@pytest.mark.parametrize("name", ["crm", "postgres", "crm_prod", "", None, "crm_bench_x"])
def test_unsafe_database_rejected(name: str | None) -> None:
    with pytest.raises(bench.UnsafeDatabaseError):
        bench.assert_safe_database(name)


@pytest.mark.parametrize("name", ["crm_bench", "crm_test", "crm_test_f3", "alma_test", "x_bench"])
def test_safe_database_accepted(name: str) -> None:
    bench.assert_safe_database(name)


def test_cli_arguments() -> None:
    args = bench.build_parser().parse_args(
        ["--sizes", "1000,50_000", "--modes", "batch", "--kams", "5", "--legacy-max", "50000"]
    )
    assert args.sizes == [1000, 50_000]
    assert args.modes == ["batch"]
    assert args.kams == 5 and args.legacy_max == 50_000 and args.force is False
    with pytest.raises(SystemExit):
        bench.build_parser().parse_args(["--modes", "fast"])
