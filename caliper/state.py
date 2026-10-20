import datetime
import sqlite3
import threading

VALID_KINDS = ("feature", "bugfix")

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
        self._conn.execute(SCHEMA)
        self._conn.commit()

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
        return self.get(req_id)

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
        return self.get(req_id)
