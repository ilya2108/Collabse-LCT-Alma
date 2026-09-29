"""SQLite-хранилище cms-stub (файл в /data, путь — из env `DB_PATH`).

Содержит журнал принятых событий (идемпотентность по `event_id`), каталог
опубликованных программ, лиды демо-формы и outbox исходящих вебхуков в CRM.
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

#: База нумерации cms_lead_id — в духе примеров контракта («lead-5031»).
_LEAD_SEQ_BASE = 5000

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

CREATE TABLE IF NOT EXISTS catalog_item (
    cms_external_id TEXT PRIMARY KEY,
    crm_program_id  TEXT NOT NULL UNIQUE,
    name            TEXT,
    product         TEXT,
    published       INTEGER NOT NULL DEFAULT 1,
    envelope        TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lead (
    cms_lead_id    TEXT PRIMARY KEY,
    full_name      TEXT NOT NULL,
    email          TEXT NOT NULL,
    phone          TEXT,
    client_kind    TEXT NOT NULL DEFAULT 'person',
    company_name   TEXT,
    inn            TEXT,
    comment        TEXT,
    crm_program_id TEXT,
    program_name   TEXT,
    event_id       TEXT NOT NULL,
    crm_request_id TEXT,
    created_at     TEXT NOT NULL
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

    # ---------------------------------------------------------------- каталог

    def upsert_catalog_item(
        self,
        *,
        crm_program_id: str,
        cms_external_id_hint: str | None,
        name: str | None,
        product: dict[str, Any] | None,
        envelope_raw: str,
    ) -> tuple[str, bool]:
        """Публикация программы на «сайте»; cms_external_id назначается заглушкой,
        если CRM его ещё не знает. Повторный upsert снова публикует позицию.
        """
        with self.lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT cms_external_id FROM catalog_item WHERE crm_program_id = ?",
                (crm_program_id,),
            ).fetchone()
            now = now_iso()
            product_raw = json.dumps(product, ensure_ascii=False) if product else None
            if row is not None:
                conn.execute(
                    "UPDATE catalog_item SET name = ?, product = ?, published = 1,"
                    " envelope = ?, updated_at = ? WHERE crm_program_id = ?",
                    (name, product_raw, envelope_raw, now, crm_program_id),
                )
                conn.commit()
                return row["cms_external_id"], False
            cms_external_id = cms_external_id_hint
            if not cms_external_id:
                count = conn.execute("SELECT COUNT(*) AS n FROM catalog_item").fetchone()["n"]
                cms_external_id = f"cms-item-{count + 1}"
            conn.execute(
                "INSERT INTO catalog_item"
                " (cms_external_id, crm_program_id, name, product, published, envelope, updated_at)"
                " VALUES (?, ?, ?, ?, 1, ?, ?)",
                (cms_external_id, crm_program_id, name, product_raw, envelope_raw, now),
            )
            conn.commit()
            return cms_external_id, True

    def unpublish_catalog_item(self, *, crm_program_id: str, envelope_raw: str) -> str | None:
        """Снятие с публикации; возвращает cms_external_id или None, если позиции нет."""
        with self.lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT cms_external_id FROM catalog_item WHERE crm_program_id = ?",
                (crm_program_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE catalog_item SET published = 0, envelope = ?, updated_at = ?"
                " WHERE crm_program_id = ?",
                (envelope_raw, now_iso(), crm_program_id),
            )
            conn.commit()
            return row["cms_external_id"]

    def get_catalog_item(self, crm_program_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM catalog_item WHERE crm_program_id = ?", (crm_program_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_catalog(self, *, published_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM catalog_item"
        if published_only:
            query += " WHERE published = 1"
        query += " ORDER BY updated_at DESC"
        with self.lock:
            rows = self._connection().execute(query).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------- лиды

    def create_lead(
        self,
        *,
        full_name: str,
        email: str,
        phone: str | None,
        client_kind: str,
        company_name: str | None,
        inn: str | None,
        comment: str | None,
        crm_program_id: str | None,
        program_name: str | None,
        event_id: str,
    ) -> str:
        with self.lock:
            conn = self._connection()
            count = conn.execute("SELECT COUNT(*) AS n FROM lead").fetchone()["n"]
            cms_lead_id = f"lead-{_LEAD_SEQ_BASE + count + 1}"
            conn.execute(
                "INSERT INTO lead (cms_lead_id, full_name, email, phone, client_kind,"
                " company_name, inn, comment, crm_program_id, program_name, event_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cms_lead_id,
                    full_name,
                    email,
                    phone,
                    client_kind,
                    company_name,
                    inn,
                    comment,
                    crm_program_id,
                    program_name,
                    event_id,
                    now_iso(),
                ),
            )
            conn.commit()
            return cms_lead_id

    def reserve_lead_id(self) -> str:
        """Идентификатор следующего лида (нужен в конверте до записи строки)."""
        with self.lock:
            count = self._connection().execute("SELECT COUNT(*) AS n FROM lead").fetchone()["n"]
        return f"lead-{_LEAD_SEQ_BASE + count + 1}"

    def set_lead_crm_request(self, cms_lead_id: str, crm_request_id: str) -> None:
        """Обратная связь от CRM: id созданной B2C-заявки из ответа на вебхук."""
        with self.lock:
            conn = self._connection()
            conn.execute(
                "UPDATE lead SET crm_request_id = ? WHERE cms_lead_id = ?",
                (crm_request_id, cms_lead_id),
            )
            conn.commit()

    def list_leads(self) -> list[dict[str, Any]]:
        """Лиды со статусом доставки их события в CRM (join с outbox)."""
        with self.lock:
            rows = self._connection().execute(
                "SELECT lead.*, outbound_event.status AS delivery_status,"
                " outbound_event.attempts AS delivery_attempts,"
                " outbound_event.last_error AS delivery_error"
                " FROM lead LEFT JOIN outbound_event ON outbound_event.event_id = lead.event_id"
                " ORDER BY lead.created_at DESC, lead.cms_lead_id DESC"
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
