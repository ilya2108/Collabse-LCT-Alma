"""SQLite-хранилище lms-stub (файл в /data, путь — из env `DB_PATH`).

Содержит журнал принятых событий (идемпотентность по `event_id`), зачисления,
синхронизированные программы и outbox исходящих вебхуков в CRM.
Подключение ленивое: импорт модуля не требует доступного каталога /data,
база открывается при первом обращении (старт приложения, /readyz).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .common import now_iso

#: База нумерации lms_enrollment_id — в духе примеров контракта («lms-enr-7781»).
_ENROLLMENT_SEQ_BASE = 7000
#: База нумерации lms_course_id, назначаемого заглушкой («lms-course-244»).
_COURSE_SEQ_BASE = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbound_event (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    occurred_at TEXT,
    source      TEXT,
    received_at TEXT NOT NULL,
    envelope    TEXT NOT NULL,
    response    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollment (
    lms_enrollment_id TEXT PRIMARY KEY,
    crm_request_id    TEXT NOT NULL UNIQUE,
    lifecycle         TEXT NOT NULL DEFAULT 'received',
    envelope          TEXT NOT NULL,
    received_at       TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS program (
    crm_program_id TEXT PRIMARY KEY,
    lms_course_id  TEXT NOT NULL,
    name           TEXT,
    envelope       TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_event (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    local_ref     TEXT,
    envelope      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_retry_at REAL NOT NULL DEFAULT 0,
    last_error    TEXT,
    last_response TEXT,
    created_at    TEXT NOT NULL,
    delivered_at  TEXT
);
"""


class Storage:
    """Обёртка над одним соединением SQLite; сериализация записи — через RLock.

    Лок публичный: обработчики вебхуков берут его на связку
    «проверка дубля → побочный эффект → запись журнала», чтобы приём
    события был атомарным относительно конкурентных повторов.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ core

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            parent = Path(self._db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def ensure_ready(self) -> None:
        """Fail-fast проверка доступности базы (используется стартом и /readyz)."""
        with self.lock:
            self._connection().execute("SELECT 1").fetchone()

    def close(self) -> None:
        with self.lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -------------------------------------------------- принятые события CRM

    def get_inbound_response(self, event_id: str) -> dict[str, Any] | None:
        """Ранее отданный ответ по event_id — основа идемпотентности приёма."""
        with self.lock:
            row = self._connection().execute(
                "SELECT response FROM inbound_event WHERE event_id = ?", (event_id,)
            ).fetchone()
        return json.loads(row["response"]) if row else None

    def save_inbound(
        self,
        *,
        event_id: str,
        event_type: str,
        occurred_at: str,
        source: str,
        envelope_raw: str,
        response: dict[str, Any],
    ) -> None:
        with self.lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO inbound_event"
                " (event_id, event_type, occurred_at, source, received_at, envelope, response)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event_type,
                    occurred_at,
                    source,
                    now_iso(),
                    envelope_raw,
                    json.dumps(response, ensure_ascii=False),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------ зачисления

    def upsert_enrollment(self, *, crm_request_id: str, envelope_raw: str) -> tuple[str, bool]:
        """Зачисление по заявке CRM: повторная передача той же заявки
        переиспользует уже выданный lms_enrollment_id (CRM хранит его в external_refs).
        """
        with self.lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT lms_enrollment_id FROM enrollment WHERE crm_request_id = ?",
                (crm_request_id,),
            ).fetchone()
            now = now_iso()
            if row is not None:
                conn.execute(
                    "UPDATE enrollment SET envelope = ?, updated_at = ? WHERE crm_request_id = ?",
                    (envelope_raw, now, crm_request_id),
                )
                conn.commit()
                return row["lms_enrollment_id"], False
            count = conn.execute("SELECT COUNT(*) AS n FROM enrollment").fetchone()["n"]
            enrollment_id = f"lms-enr-{_ENROLLMENT_SEQ_BASE + count + 1}"
            conn.execute(
                "INSERT INTO enrollment"
                " (lms_enrollment_id, crm_request_id, lifecycle, envelope, received_at, updated_at)"
                " VALUES (?, ?, 'received', ?, ?, ?)",
                (enrollment_id, crm_request_id, envelope_raw, now, now),
            )
            conn.commit()
            return enrollment_id, True

    def get_enrollment(self, lms_enrollment_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM enrollment WHERE lms_enrollment_id = ?", (lms_enrollment_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_enrollments(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM enrollment ORDER BY received_at DESC, lms_enrollment_id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_lifecycle(self, lms_enrollment_id: str, lifecycle: str) -> bool:
        with self.lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE enrollment SET lifecycle = ?, updated_at = ? WHERE lms_enrollment_id = ?",
                (lifecycle, now_iso(), lms_enrollment_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    # -------------------------------------------------------------- программы

    def upsert_program(
        self,
        *,
        crm_program_id: str,
        lms_course_id: str | None,
        name: str | None,
        envelope_raw: str,
    ) -> str:
        """Синхронизация программы из CRM; lms_course_id назначается заглушкой,
        если CRM его ещё не знает.
        """
        with self.lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT lms_course_id FROM program WHERE crm_program_id = ?", (crm_program_id,)
            ).fetchone()
            now = now_iso()
            if row is not None:
                course_id = lms_course_id or row["lms_course_id"]
                conn.execute(
                    "UPDATE program SET lms_course_id = ?, name = ?, envelope = ?, updated_at = ?"
                    " WHERE crm_program_id = ?",
                    (course_id, name, envelope_raw, now, crm_program_id),
                )
                conn.commit()
                return course_id
            if not lms_course_id:
                count = conn.execute("SELECT COUNT(*) AS n FROM program").fetchone()["n"]
                lms_course_id = f"lms-course-{_COURSE_SEQ_BASE + count + 1}"
            conn.execute(
                "INSERT INTO program (crm_program_id, lms_course_id, name, envelope, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (crm_program_id, lms_course_id, name, envelope_raw, now),
            )
            conn.commit()
            return lms_course_id

    def list_programs(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM program ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------- outbox исходящих в CRM

    def enqueue_outbound(
        self, *, event_id: str, event_type: str, local_ref: str | None, envelope_raw: str
    ) -> None:
        with self.lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO outbound_event"
                " (event_id, event_type, local_ref, envelope, status, next_retry_at, created_at)"
                " VALUES (?, ?, ?, ?, 'pending', 0, ?)",
                (event_id, event_type, local_ref, envelope_raw, now_iso()),
            )
            conn.commit()

    def due_outbound(self, now_ts: float, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM outbound_event"
                " WHERE status IN ('pending', 'retrying') AND next_retry_at <= ?"
                " ORDER BY created_at LIMIT ?",
                (now_ts, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_outbound(self, event_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM outbound_event WHERE event_id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_delivered(self, event_id: str, attempts: int, response_body: str | None) -> None:
        with self.lock:
            conn = self._connection()
            conn.execute(
                "UPDATE outbound_event SET status = 'delivered', attempts = ?, last_error = NULL,"
                " last_response = ?, delivered_at = ? WHERE event_id = ?",
                (attempts, response_body, now_iso(), event_id),
            )
            conn.commit()

    def mark_retrying(
        self, event_id: str, attempts: int, next_retry_at: float, error: str
    ) -> None:
        with self.lock:
            conn = self._connection()
            conn.execute(
                "UPDATE outbound_event SET status = 'retrying', attempts = ?, next_retry_at = ?,"
                " last_error = ? WHERE event_id = ?",
                (attempts, next_retry_at, error, event_id),
            )
            conn.commit()

    def mark_dead(self, event_id: str, attempts: int, error: str) -> None:
        with self.lock:
            conn = self._connection()
            conn.execute(
                "UPDATE outbound_event SET status = 'dead', attempts = ?, last_error = ?"
                " WHERE event_id = ?",
                (attempts, error, event_id),
            )
            conn.commit()

    # ----------------------------------------------------------- журнал и счёт

    def journal(self, limit: int = 200) -> list[dict[str, Any]]:
        """Объединённый журнал обмена: принятые (←) и исходящие (→) события."""
        with self.lock:
            conn = self._connection()
            inbound = conn.execute(
                "SELECT event_id, event_type, received_at, envelope FROM inbound_event"
                " ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            outbound = conn.execute(
                "SELECT event_id, event_type, created_at, envelope, status, attempts,"
                " last_error, last_response, delivered_at FROM outbound_event"
                " ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        entries: list[dict[str, Any]] = [
            {
                "direction": "in",
                "at": row["received_at"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "status": "accepted",
                "attempts": None,
                "last_error": None,
                "envelope": row["envelope"],
            }
            for row in inbound
        ]
        entries.extend(
            {
                "direction": "out",
                "at": row["created_at"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "status": row["status"],
                "attempts": row["attempts"],
                "last_error": row["last_error"],
                "envelope": row["envelope"],
            }
            for row in outbound
        )
        entries.sort(key=lambda item: item["at"], reverse=True)
        return entries[:limit]

    def counters(self) -> dict[str, int]:
        with self.lock:
            conn = self._connection()
            received = conn.execute("SELECT COUNT(*) AS n FROM inbound_event").fetchone()["n"]
            delivered = conn.execute(
                "SELECT COUNT(*) AS n FROM outbound_event WHERE status = 'delivered'"
            ).fetchone()["n"]
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM outbound_event WHERE status IN ('pending', 'retrying')"
            ).fetchone()["n"]
            dead = conn.execute(
                "SELECT COUNT(*) AS n FROM outbound_event WHERE status = 'dead'"
            ).fetchone()["n"]
        return {"received": received, "delivered": delivered, "pending": pending, "dead": dead}
