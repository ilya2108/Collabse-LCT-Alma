"""Интеграционные тесты с PostgreSQL: схема, изоляция транзакций, API-клиенты, сброс состояния.

Схема создаётся один раз на сессию pytest настоящими миграциями (`alembic upgrade head`),
а не `Base.metadata.create_all`: модели не описывают индексы и CHECK из 0001 (частичный
`uq_notification_dedup` под ON CONFLICT, `ck_deal_client`, trigram-индексы pg_trgm), а данных
миграций (каталог ролей и прав 0004) в metadata нет вовсе. Перед прогоном схема `public`
пересоздаётся целиком — результат не зависит от прошлых запусков и правок миграций.

Изоляция:
- `db_session` — тест целиком во внешней транзакции одного соединения; `commit()` внутри
  сервисов и обработчиков фиксирует SAVEPOINT (join_transaction_mode="create_savepoint"),
  в конце теста внешняя транзакция откатывается. Advisory xact-локи живут до конца внешней
  транзакции — конкурентность так не проверить;
- `db_committed_sessionmaker` (маркер `db_commit` ставится автоматически) — настоящие COMMIT
  на отдельных соединениях; после теста все таблицы очищаются (TRUNCATE … RESTART IDENTITY
  CASCADE), строки, засеянные миграциями, восстанавливаются из снимка `alma_test_seed`.

База — DATABASE_URL из tests/conftest.py (по умолчанию crm_test; параллельные потоки работ
задают свою: `ALMA_TEST_DB=crm_test_f<N>`). Имя обязано начинаться с `crm_test` или
оканчиваться на `_test`: фикстура удаляет схему. PostgreSQL недоступен — тесты пропускаются;
`ALMA_DB_TESTS=required` (CI) превращает пропуск в ошибку.

Все async-фикстуры function-scope: pytest-asyncio 1.x даёт каждому тесту свой event loop,
поэтому движки (NullPool) и синглтоны приложения (engine, KeyDB-клиент, SSE-хаб, хранилище
файлов, кэш каталога ролей, хранилище ключей идемпотентности) живут в пределах теста.
Сессионная подготовка схемы выполняется в отдельном потоке: alembic env.py вызывает
`asyncio.run`, и он не должен трогать event loop основного потока.
"""

from __future__ import annotations

import asyncio
import graphlib
import importlib
import os
import re
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app
from alembic import command
from app.core.cache import close_cache
from app.core.config import get_settings
from app.core.db import dispose_engine, get_session
from app.core.errors import unauthorized
from app.core.security import TokenClaims, get_token_claims, verify_internal_token
from app.core.sse import reset_sse_hub
from app.models import AppUser, Workflow, WorkflowStage
from app.modules.files import storage as storage_module
from app.modules.files.storage import storage_unavailable

from . import factories

BACKEND_ROOT = Path(app.__file__).resolve().parent.parent
SEED_SCHEMA = "alma_test_seed"
# crm_test, crm_test_f2 (потоки работ), crm_f2_test — схема этих баз пересоздаётся
_DB_NAME_RE = re.compile(r"crm_test(_[a-z0-9]+)*|crm(_[a-z0-9]+)*_test")
_TABLE_RE = re.compile(r"[a-z_][a-z0-9_]*")


# ------------------------------------------------------------------------------------------
# Маркеры
# ------------------------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: интеграционный тест с PostgreSQL (tests/db)")
    config.addinivalue_line(
        "markers", "db_commit: настоящие COMMIT на отдельных соединениях, очистка TRUNCATE"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    db_dir = Path(__file__).resolve().parent
    for item in items:
        if db_dir not in Path(item.path).resolve().parents:
            continue
        item.add_marker(pytest.mark.db)
        if "db_committed_sessionmaker" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db_commit)


# ------------------------------------------------------------------------------------------
# Подготовка базы (один раз на сессию)
# ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MigratedDatabase:
    url: str
    name: str
    head: str
    seed_tables: tuple[str, ...]  # таблицы с данными миграций, родители раньше детей
    seed_rows: dict[str, list[dict[str, Any]]]  # снимок сразу после `upgrade head`


def _run_in_thread(fn: Callable[[], Any]) -> Any:
    """Выполняет fn в отдельном потоке и возвращает результат (исключение — пробрасывает)."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — переносим в основной поток
            box["error"] = exc

    thread = threading.Thread(target=target, name="alma-test-db")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def run_async(factory: Callable[[], Awaitable[Any]]) -> Any:
    return _run_in_thread(lambda: asyncio.run(factory()))  # type: ignore[arg-type]


def _unavailable(reason: str) -> None:
    if os.environ.get("ALMA_DB_TESTS") == "required":
        pytest.fail(f"PostgreSQL обязателен (ALMA_DB_TESTS=required): {reason}", pytrace=False)
    pytest.skip(f"PostgreSQL недоступен — тесты tests/db пропущены: {reason}")


def _quote_table(name: str) -> str:
    if not _TABLE_RE.fullmatch(name):
        raise ValueError(f"неожиданное имя таблицы: {name!r}")
    return f'"{name}"'


async def _pg_connect(url: str, database: str | None = None) -> asyncpg.Connection:
    parsed = make_url(url)
    return await asyncpg.connect(
        host=parsed.host or "localhost",
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=database or parsed.database,
        timeout=5,
    )


async def ensure_database(url: str) -> None:
    """Создаёт базу, если её нет (через служебную БД postgres)."""
    name = make_url(url).database
    try:
        conn = await _pg_connect(url, "postgres")
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError):
        # нет доступа к служебной БД (CI с готовой базой) — проверим целевую напрямую
        conn = await _pg_connect(url)
        await conn.close()
        return
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            try:
                await conn.execute(f'CREATE DATABASE "{name}"')
            except asyncpg.DuplicateDatabaseError:
                pass
    finally:
        await conn.close()


async def drop_database(url: str) -> None:
    name = make_url(url).database
    conn = await _pg_connect(url, "postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _reset_schema(url: str) -> None:
    conn = await _pg_connect(url)
    try:
        busy = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
        )
        if busy:
            raise RuntimeError(
                f"база {make_url(url).database} занята другим подключением ({busy}) — "
                "параллельный прогон? Задайте свою: ALMA_TEST_DB=crm_test_f<N>"
            )
        await conn.execute(
            "SET lock_timeout = '10s';"
            f"DROP SCHEMA IF EXISTS {SEED_SCHEMA} CASCADE;"
            "DROP SCHEMA IF EXISTS public CASCADE;"
            "CREATE SCHEMA public;"
        )
    finally:
        await conn.close()


def alembic_config(url: str) -> Config:
    """Конфиг без ini-файла: env.py не вызывает fileConfig и не отключает логгеры
    приложения (caplog продолжает видеть app.*). URL env.py всё равно берёт из DATABASE_URL."""
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def script_head() -> str:
    head = ScriptDirectory.from_config(
        alembic_config("postgresql+asyncpg://x/x")
    ).get_current_head()
    assert head is not None, "в alembic/versions нет ревизий"
    return head


def run_alembic(url: str, *args: str) -> None:
    """`alembic <upgrade|downgrade> <rev>` против url (в отдельном потоке, env DATABASE_URL
    подменяется на время вызова — env.py читает именно его)."""

    def call() -> None:
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        try:
            getattr(command, args[0])(alembic_config(url), *args[1:])
        finally:
            if previous is not None:
                os.environ["DATABASE_URL"] = previous

    _run_in_thread(call)


async def _public_tables(conn: AsyncConnection) -> list[str]:
    rows = await conn.execute(
        sa.text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version' ORDER BY tablename"
        )
    )
    return [row[0] for row in rows]


async def _fk_order(conn: AsyncConnection, tables: Iterable[str]) -> tuple[str, ...]:
    wanted = set(tables)
    edges = await conn.execute(
        sa.text(
            "SELECT ch.relname, pa.relname FROM pg_constraint c "
            "JOIN pg_class ch ON ch.oid = c.conrelid "
            "JOIN pg_class pa ON pa.oid = c.confrelid "
            "JOIN pg_namespace n ON n.oid = ch.relnamespace "
            "WHERE c.contype = 'f' AND n.nspname = 'public'"
        )
    )
    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter(
        {t: set() for t in sorted(wanted)}
    )
    for child, parent in edges:
        if child in wanted and parent in wanted and child != parent:
            sorter.add(child, parent)
    return tuple(sorter.static_order())


async def _snapshot_seed(url: str) -> tuple[str, tuple[str, ...], dict[str, list[dict[str, Any]]]]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            head = (
                await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            seeded = []
            for table in await _public_tables(conn):
                has_rows = await conn.execute(
                    sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{_quote_table(table)})")
                )
                if has_rows.scalar():
                    seeded.append(table)
            ordered = await _fk_order(conn, seeded)
            await conn.execute(sa.text(f"CREATE SCHEMA {SEED_SCHEMA}"))
            rows: dict[str, list[dict[str, Any]]] = {}
            for table in ordered:
                quoted = _quote_table(table)
                await conn.execute(
                    sa.text(f"CREATE TABLE {SEED_SCHEMA}.{quoted} AS TABLE public.{quoted}")
                )
                result = await conn.execute(sa.text(f"SELECT * FROM public.{quoted}"))
                rows[table] = [dict(row._mapping) for row in result]
        return head, ordered, rows
    finally:
        await engine.dispose()


async def truncate_and_restore(conn: AsyncConnection, db: MigratedDatabase) -> None:
    """Очищает все таблицы и возвращает строки, засеянные миграциями (снимок сессии)."""
    await conn.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    tables = await _public_tables(conn)
    if tables:
        listed = ", ".join(f"public.{_quote_table(t)}" for t in tables)
        await conn.execute(sa.text(f"TRUNCATE TABLE {listed} RESTART IDENTITY CASCADE"))
    for table in db.seed_tables:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND is_generated = 'NEVER' "
                "ORDER BY ordinal_position"
            ),
            {"t": table},
        )
        columns = ", ".join(f'"{name}"' for (name,) in result)
        quoted = _quote_table(table)
        await conn.execute(
            sa.text(
                f"INSERT INTO public.{quoted} ({columns}) OVERRIDING SYSTEM VALUE "
                f"SELECT {columns} FROM {SEED_SCHEMA}.{quoted}"
            )
        )


@pytest.fixture(scope="session")
def migrated_db() -> MigratedDatabase:
    url = os.environ["DATABASE_URL"]
    name = make_url(url).database or ""
    if not _DB_NAME_RE.fullmatch(name):
        pytest.fail(
            f"tests/db пересоздаёт схему базы — нужно имя crm_test* или *_test, а не {name!r}",
            pytrace=False,
        )
    try:
        run_async(lambda: ensure_database(url))
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        _unavailable(f"{type(exc).__name__}: {exc}")
    run_async(lambda: _reset_schema(url))
    run_alembic(url, "upgrade", "head")
    head, seed_tables, seed_rows = run_async(lambda: _snapshot_seed(url))
    return MigratedDatabase(
        url=url, name=name, head=head, seed_tables=seed_tables, seed_rows=seed_rows
    )


# ------------------------------------------------------------------------------------------
# Соединения и сессии (на тест)
# ------------------------------------------------------------------------------------------


@pytest.fixture
async def db_engine(migrated_db: MigratedDatabase) -> AsyncIterator[AsyncEngine]:
    """Отдельные соединения (NullPool): гонки, `pg_locks`, второй читатель."""
    engine = create_async_engine(migrated_db.url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_connection(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield conn
        finally:
            if outer.is_active:
                await outer.rollback()


@pytest.fixture
def db_session_factory(db_connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """Сессии во внешней транзакции теста: commit — SAVEPOINT, всё откатится после теста.
    Одно соединение: сессии фабрики нельзя использовать конкурентно (asyncio.gather)."""
    return async_sessionmaker(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )


@pytest.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session


@pytest.fixture
async def db_committed_sessionmaker(
    migrated_db: MigratedDatabase,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Настоящие COMMIT на отдельных соединениях (конкурентность, advisory-локи, гонки).
    После теста — TRUNCATE всех таблиц и восстановление данных миграций."""
    engine = create_async_engine(migrated_db.url, poolclass=NullPool)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()
        await dispose_engine()  # глобальный движок приложения тоже мог открыть соединения
        cleanup = create_async_engine(migrated_db.url, poolclass=NullPool)
        try:
            async with cleanup.begin() as conn:
                await truncate_and_restore(conn, migrated_db)
        finally:
            await cleanup.dispose()


# ------------------------------------------------------------------------------------------
# Состояние процесса (синглтоны приложения) — сброс до и после каждого теста
# ------------------------------------------------------------------------------------------


def _call_optional(module_name: str, attr: str) -> Any:
    """Вызывает функцию модуля доработки, если модуль уже есть в рабочей копии."""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        return None
    fn = getattr(module, attr, None)
    return fn() if callable(fn) else None


@pytest.fixture(autouse=True)
async def _isolate_process_state() -> AsyncIterator[None]:
    _call_optional("app.core.access", "reset_role_catalog")  # F2: кэш каталога ролей
    reset_sse_hub()
    yield
    await dispose_engine()
    await close_cache()
    reset_sse_hub()
    storage_module.reset_storage()
    _call_optional("app.core.access", "reset_role_catalog")
    _call_optional("app.core.idempotency_store", "reset_idempotency_store")  # F4


# ------------------------------------------------------------------------------------------
# Хранилище файлов в памяти (вместо MinIO)
# ------------------------------------------------------------------------------------------


class MemoryStorage:
    """Подмена S3Storage: тот же интерфейс, объекты в словаре."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str | None]] = {}

    async def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str | None) -> None:
        self.objects[(bucket, key)] = (data, content_type)

    async def read_bytes(self, bucket: str, key: str) -> bytes:
        try:
            return self.objects[(bucket, key)][0]
        except KeyError as exc:
            raise storage_unavailable(f"нет объекта {bucket}/{key}") from exc

    async def stream(self, bucket: str, key: str) -> AsyncIterator[bytes]:
        yield await self.read_bytes(bucket, key)

    async def presigned_url(self, bucket: str, key: str, download_name: str) -> str:
        return f"http://minio.test/{bucket}/{key}"

    async def remove(self, bucket: str, key: str) -> bool:
        return self.objects.pop((bucket, key), None) is not None


@pytest.fixture
def memory_storage(monkeypatch: pytest.MonkeyPatch) -> MemoryStorage:
    storage = MemoryStorage()
    monkeypatch.setattr(storage_module, "_storage", storage)
    return storage


# ------------------------------------------------------------------------------------------
# Данные: пользователи и воронки
# ------------------------------------------------------------------------------------------


@pytest.fixture
def user_factory(db_session: AsyncSession) -> Callable[..., Awaitable[AppUser]]:
    async def make(**kwargs: Any) -> AppUser:
        return await factories.make_user(db_session, **kwargs)

    return make


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> AppUser:
    return await factories.make_user(
        db_session, roles=("admin",), username="admin@test", full_name="Админов Админ"
    )


@pytest.fixture
async def workflows(
    db_session: AsyncSession, admin_user: AppUser
) -> dict[str, tuple[Workflow, dict[str, WorkflowStage]]]:
    """Воронки b2b и b2c из app/modules/workflow/seeds (как на стенде):
    `{code: (Workflow, {stage_code: WorkflowStage})}`."""
    from app.tools.seed import seed_workflows

    return await seed_workflows(db_session, admin_user)


# ------------------------------------------------------------------------------------------
# HTTP-клиенты приложения
# ------------------------------------------------------------------------------------------


@dataclass
class ApiClients:
    """`as_user(user)` — клиент от имени пользователя (JWT не проверяется: подменён
    get_token_claims); `internal()` — с X-Internal-Token. Все клиенты делят одно приложение
    и `db_session`: запросы выполнять последовательно, не через asyncio.gather."""

    app: FastAPI
    _claims: dict[str, TokenClaims]
    _opened: list[httpx.AsyncClient]

    def _client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers=headers
        )
        self._opened.append(client)
        return client

    def as_user(
        self, user: AppUser, *, token_roles: Iterable[str] | None = None
    ) -> httpx.AsyncClient:
        token = f"test-{uuid.uuid4()}"
        self._claims[token] = TokenClaims(
            sub=str(user.keycloak_sub),
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=tuple(user.roles if token_roles is None else token_roles),
            azp="crm-frontend",
        )
        return self._client({"Authorization": f"Bearer {token}"})

    def internal(self) -> httpx.AsyncClient:
        return self._client({"X-Internal-Token": get_settings().internal_api_token})

    def anonymous(self) -> httpx.AsyncClient:
        return self._client({})


@pytest.fixture
async def api(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ApiClients]:
    from app.main import create_app

    claims: dict[str, TokenClaims] = {}
    application = create_app()

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def claims_override(request: Request) -> TokenClaims:
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token not in claims:
            raise unauthorized()
        return claims[token]

    application.dependency_overrides[get_session] = session_override
    application.dependency_overrides[get_token_claims] = claims_override

    # F4: принципал Idempotency-Key — из тех же тестовых токенов, без проверки JWT
    try:
        idempotency = importlib.import_module("app.core.idempotency")
    except ModuleNotFoundError as exc:
        if exc.name != "app.core.idempotency":
            raise
        idempotency = None
    if idempotency is not None and hasattr(idempotency, "resolve_principal"):

        async def principal(headers: Any) -> str | None:
            token = (headers.get("authorization") or "").removeprefix("Bearer ").strip()
            if token in claims:
                return f"sub:{claims[token].sub}"
            if verify_internal_token(
                headers.get("x-internal-token"), get_settings().internal_api_token
            ):
                return "internal"
            return None

        monkeypatch.setattr(idempotency, "resolve_principal", principal)

    clients = ApiClients(app=application, _claims=claims, _opened=[])
    try:
        yield clients
    finally:
        for client in clients._opened:
            await client.aclose()
