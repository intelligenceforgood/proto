"""SQLite-backed queue for dossier bundle plans."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from i4g.settings import get_settings

if TYPE_CHECKING:  # pragma: no cover - import used only for type hints
    from i4g.reports.bundle_builder import DossierPlan

SETTINGS = get_settings()


class DossierQueueStore:
    """Persists DossierPlan payloads for downstream agent execution."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        resolved = Path(db_path) if db_path else Path(SETTINGS.storage.sqlite_path)
        if not resolved.is_absolute():
            resolved = (Path(SETTINGS.project_root) / resolved).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = resolved
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dossier_queue (
                    plan_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    warnings TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dossier_queue_status ON dossier_queue(status)")
            try:
                conn.execute("ALTER TABLE dossier_queue ADD COLUMN warnings TEXT")
            except sqlite3.OperationalError:
                pass

    def enqueue_plan(self, plan: "DossierPlan", *, priority: str = "normal") -> str:
        """Insert or replace a dossier plan in the queue."""

        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(plan.to_dict(), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dossier_queue (plan_id, status, priority, payload, queued_at, updated_at, warnings)
                VALUES (?, 'pending', ?, ?, ?, ?, NULL)
                ON CONFLICT(plan_id) DO UPDATE SET
                    status='pending',
                    priority=excluded.priority,
                    payload=excluded.payload,
                    queued_at=excluded.queued_at,
                    updated_at=excluded.updated_at,
                    error=NULL,
                    warnings=NULL
                """,
                (plan.plan_id, priority, payload, now, now),
            )
        return plan.plan_id

    def list_pending(self, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Return pending queue entries along with their serialized plans."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT plan_id, priority, payload, queued_at, updated_at, warnings
                FROM dossier_queue
                WHERE status='pending'
                ORDER BY queued_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_plans(self, *, status: str | None = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Return queue entries filtered by ``status`` (or all entries when omitted)."""

        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT plan_id, status, priority, payload, queued_at, updated_at, error, warnings
            FROM dossier_queue
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Return a single queue entry regardless of status."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT plan_id, status, priority, payload, queued_at, updated_at, error, warnings
                FROM dossier_queue
                WHERE plan_id=?
                """,
                (plan_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def mark_complete(self, plan_id: str, *, warnings: Optional[Sequence[str]] = None) -> None:
        """Mark a queued plan as completed and persist optional warnings."""

        self._update_status(plan_id, status="completed", warnings=warnings)

    def mark_failed(self, plan_id: str, error: str) -> None:
        """Mark a queued plan as failed with an error message."""

        self._update_status(plan_id, status="failed", error=error)

    def reset(self, plan_id: str) -> None:
        """Return a leased plan to the pending state (used for dry runs)."""

        self._update_status(plan_id, status="pending", warnings=None)

    def lease_next(self) -> Optional[Dict[str, Any]]:
        """Atomically lease the next pending entry for processing."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT plan_id, priority, payload, queued_at, updated_at, warnings
                FROM dossier_queue
                WHERE status='pending'
                ORDER BY queued_at ASC
                LIMIT 1
                """,
            ).fetchone()
            if not row:
                conn.commit()
                return None
            plan_id = row["plan_id"] if isinstance(row, sqlite3.Row) else row[0]
            conn.execute(
                """
                UPDATE dossier_queue
                SET status='leased', updated_at=?
                WHERE plan_id=?
                """,
                (datetime.now(timezone.utc).isoformat(), plan_id),
            )
            conn.commit()
        return self._row_to_dict(row)

    def _update_status(
        self,
        plan_id: str,
        *,
        status: str,
        error: Optional[str] = None,
        warnings: Optional[Sequence[str]] = None,
    ) -> None:
        warnings_payload = json.dumps(list(warnings)) if warnings is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE dossier_queue
                SET status=?, updated_at=?, error=?, warnings=?
                WHERE plan_id=?
                """,
                (status, datetime.now(timezone.utc).isoformat(), error, warnings_payload, plan_id),
            )

    def _row_to_dict(self, row: sqlite3.Row | Tuple[Any, ...]) -> Dict[str, Any]:
        if isinstance(row, sqlite3.Row):
            record = dict(row)
        else:
            # Fallback for callers passing tuples in older tests (plan_id, priority, payload, queued_at, updated_at, warnings)
            record = {}
            if len(row) >= 6:
                record["plan_id"] = row[0]
                # Some queries include status as the second column
                if len(row) >= 8:
                    record["status"] = row[1]
                    record["priority"] = row[2]
                    record["payload"] = row[3]
                    record["queued_at"] = row[4]
                    record["updated_at"] = row[5]
                    record["error"] = row[6]
                    record["warnings"] = row[7]
                else:
                    record["priority"] = row[1]
                    record["payload"] = row[2]
                    record["queued_at"] = row[3]
                    record["updated_at"] = row[4]
                    record["warnings"] = row[5]
        payload_raw = record.get("payload")
        payload = json.loads(payload_raw) if payload_raw else {}
        warnings_raw = record.get("warnings")
        result = {
            "plan_id": record.get("plan_id"),
            "priority": record.get("priority"),
            "payload": payload,
            "queued_at": record.get("queued_at"),
            "updated_at": record.get("updated_at"),
            "warnings": json.loads(warnings_raw) if warnings_raw else [],
        }
        if "status" in record:
            result["status"] = record.get("status")
        if "error" in record:
            result["error"] = record.get("error")
        return result
