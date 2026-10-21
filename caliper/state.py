import datetime
import json
import sqlite3
import threading

VALID_KINDS = ("feature", "bugfix")
ACTIVE_STATUSES = ("submitted", "planning", "executing", "reviewing", "awaiting_approval", "merged", "deployed_dev")
IN_FLIGHT_STATUSES = ("planning", "executing", "reviewing")

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    extra TEXT,
    status TEXT NOT NULL DEFAULT 'submitted',
    loop_count INTEGER NOT NULL DEFAULT 0,
    review_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


class RequestStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            import pathlib
            pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self.on_change = None

    def _notify(self, change: str, req: dict) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(change, req)
        except Exception:
            pass

    def create(self, project_id: str, kind: str, text: str, extra: str | None = None) -> dict:
        if kind not in VALID_KINDS:
            raise ValueError("Request type must be 'feature' or 'bugfix'.")
        if not text or not text.strip():
            raise ValueError("The request text is required.")
        with self._lock:
            row = self._conn.execute("SELECT MAX(CAST(substr(id, 5) AS INTEGER)) AS peak FROM requests WHERE id LIKE 'req-%'").fetchone()
            seq = (row["peak"] or 0) + 1
            req_id = f"req-{seq}"
            now = _now()
            self._conn.execute(
                "INSERT INTO requests (id, project_id, kind, text, extra, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (req_id, project_id, kind, text.strip(), extra, "submitted", now, now),
            )
            self._conn.commit()
        req = self.get(req_id)
        self._notify("created", req)
        return req

    def get(self, req_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
        return dict(row) if row else None

    def list_for_project(self, project_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE project_id = ? ORDER BY CAST(substr(id, 5) AS INTEGER) DESC", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM requests ORDER BY CAST(substr(id, 5) AS INTEGER) DESC").fetchall()
        return [dict(r) for r in rows]

    def update(self, req_id: str, **fields) -> dict:
        allowed = {"status", "loop_count", "review_note"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Cannot update fields: {sorted(unknown)}")
        with self._lock:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            params = list(fields.values()) + [_now(), req_id]
            cur = self._conn.execute(f"UPDATE requests SET {assignments}, updated_at = ? WHERE id = ?", params)
            if cur.rowcount == 0:
                raise ValueError(f"Request '{req_id}' does not exist.")
            self._conn.commit()
        req = self.get(req_id)
        self._notify("updated", req)
        return req

    def cancel_active_for_project(self, project_id: str) -> list[dict]:
        with self._lock:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            rows = self._conn.execute(
                f"SELECT * FROM requests WHERE project_id = ? AND status IN ({placeholders})",
                (project_id, *ACTIVE_STATUSES),
            ).fetchall()
            previous = [dict(row) for row in rows]
            if previous:
                now = _now()
                self._conn.executemany(
                    "UPDATE requests SET status = 'cancelled', updated_at = ? WHERE id = ?",
                    [(now, row["id"]) for row in previous],
                )
                self._conn.commit()
        for row in previous:
            req = self.get(row["id"])
            if req is not None:
                self._notify("updated", req)
        return previous

    def restore_requests(self, previous: list[dict]) -> None:
        if not previous:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE requests SET status = ?, updated_at = ? WHERE id = ?",
                [(row["status"], row["updated_at"], row["id"]) for row in previous],
            )
            self._conn.commit()
        for row in previous:
            req = self.get(row["id"])
            if req is not None:
                self._notify("updated", req)

    def delete(self, req_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM requests WHERE id = ?", (req_id,))
            self._conn.commit()
        req = dict(row)
        self._notify("deleted", req)
        return req

    def delete_project_with_audit(self, project_id: str, project_name: str, details: dict) -> int:
        now = _now()
        with self._lock:
            try:
                self._conn.execute("DELETE FROM requests WHERE project_id = ?", (project_id,))
                cursor = self._conn.execute(
                    "INSERT INTO audit_events (project_id, project_name, occurred_at, details) VALUES (?,?,?,?)",
                    (project_id, project_name, now, json.dumps(details, sort_keys=True)),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return int(cursor.lastrowid)
