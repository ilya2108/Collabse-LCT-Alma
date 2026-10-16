"""Ограниченное ожидание локов воронки (workflow-engine.md §6.5): 55P03 → 409 workflow_busy."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.errors import ApiError
from app.modules.workflow import locks


class _DriverError(Exception):
    """Заглушка ошибки драйвера: у адаптера asyncpg SQLAlchemy есть `sqlstate`."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def _dbapi_error(sqlstate: str) -> DBAPIError:
    return OperationalError("SELECT pg_advisory_xact_lock(...)", {}, _DriverError(sqlstate))


def test_is_lock_timeout_by_sqlstate() -> None:
    assert locks.is_lock_timeout(_dbapi_error("55P03")) is True
    assert locks.is_lock_timeout(_dbapi_error("40P01")) is False  # deadlock — не таймаут
    assert locks.is_lock_timeout(RuntimeError("без sqlstate")) is False


def test_is_lock_timeout_reads_driver_cause() -> None:
    # адаптер без sqlstate, исходная ошибка asyncpg — в __cause__
    adapted = Exception("адаптер")
    adapted.__cause__ = _DriverError("55P03")
    assert locks.is_lock_timeout(OperationalError("SELECT 1", {}, adapted)) is True


def test_workflow_busy_error_shape() -> None:
    error = locks.workflow_busy_error()
    assert isinstance(error, ApiError)
    assert error.status_code == 409
    assert error.code == "workflow_busy"
    assert error.headers == {"Retry-After": "2"}
    assert "повторите" in error.message
    assert locks.workflow_busy_error("Свой текст").message == "Свой текст"


async def test_lock_timeout_as_busy_converts_55p03() -> None:
    with pytest.raises(ApiError) as caught:
        async with locks.lock_timeout_as_busy():
            raise _dbapi_error("55P03")
    assert caught.value.code == "workflow_busy"
    assert caught.value.headers == {"Retry-After": "2"}
    assert isinstance(caught.value.__cause__, DBAPIError)


async def test_lock_timeout_as_busy_passes_other_errors() -> None:
    with pytest.raises(DBAPIError):
        async with locks.lock_timeout_as_busy():
            raise _dbapi_error("40P01")
    with pytest.raises(ValueError):
        async with locks.lock_timeout_as_busy():
            raise ValueError("не ошибка БД")
    async with locks.lock_timeout_as_busy():  # без ошибки — ничего не происходит
        pass


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, statement, params):  # noqa: ANN001 — заглушка AsyncSession
        self.calls.append((str(statement), params))


async def test_acquire_sets_local_lock_timeout_first(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _RecordingSession()
    workflow_id = uuid.uuid4()
    monkeypatch.setattr(locks, "TRANSITION_LOCK_TIMEOUT_MS", 250)
    await locks.acquire_shared_workflow_lock(session, workflow_id)  # type: ignore[arg-type]
    monkeypatch.setattr(locks, "APPLY_LOCK_TIMEOUT_MS", 750)
    await locks.acquire_exclusive_workflow_lock(session, workflow_id)  # type: ignore[arg-type]
    await locks.acquire_exclusive_workflow_lock(session, workflow_id, timeout_ms=5)  # type: ignore[arg-type]

    sql = [statement for statement, _ in session.calls]
    params = [p for _, p in session.calls]
    assert "set_config('lock_timeout'" in sql[0] and params[0] == {"timeout": "250ms"}
    assert "pg_advisory_xact_lock_shared" in sql[1] and params[1] == {"wid": str(workflow_id)}
    assert params[2] == {"timeout": "750ms"}  # константа читается в момент вызова
    assert "pg_advisory_xact_lock(" in sql[3]
    assert params[4] == {"timeout": "5ms"}
