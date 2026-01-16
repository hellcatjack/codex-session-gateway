import sqlite3
import threading
import time
from typing import Optional

from .models import Run, Session, SessionState, RunStatus


class Store:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._lock = threading.Lock()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        cur = self._conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        if column not in columns:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def init(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    resume_id TEXT,
                    last_result TEXT,
                    jsonl_last_ts REAL,
                    jsonl_last_hash TEXT,
                    last_chat_id INTEGER,
                    created_at REAL NOT NULL,
                    last_activity REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    error TEXT
                )
                """
            )
            self._ensure_column("sessions", "resume_id", "TEXT")
            self._ensure_column("sessions", "last_result", "TEXT")
            self._ensure_column("sessions", "jsonl_last_ts", "REAL")
            self._ensure_column("sessions", "jsonl_last_hash", "TEXT")
            self._ensure_column("sessions", "last_chat_id", "INTEGER")
            self._conn.commit()

    def record_session(self, session: Session) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO sessions
                (session_id, user_id, state, resume_id, last_result, jsonl_last_ts, jsonl_last_hash, last_chat_id, created_at, last_activity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.state.value,
                    session.resume_id,
                    session.last_result,
                    session.jsonl_last_ts,
                    session.jsonl_last_hash,
                    session.last_chat_id,
                    time.time(),
                    session.last_activity,
                ),
            )
            self._conn.commit()

    def update_session_state(self, session_id: str, state: SessionState) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE sessions SET state = ?, last_activity = ? WHERE session_id = ?",
                (state.value, time.time(), session_id),
            )
            self._conn.commit()

    def update_session_resume_id(self, session_id: str, resume_id: str | None) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE sessions SET resume_id = ?, last_activity = ? WHERE session_id = ?",
                (resume_id, time.time(), session_id),
            )
            self._conn.commit()

    def update_session_last_result(
        self, session_id: str, last_result: str | None
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE sessions SET last_result = ?, last_activity = ? WHERE session_id = ?",
                (last_result, time.time(), session_id),
            )
            self._conn.commit()

    def get_last_result_by_user_id(self, user_id: int) -> Optional[str]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT last_result
                FROM sessions
                WHERE user_id = ? AND last_result IS NOT NULL
                ORDER BY last_activity DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def update_session_jsonl_state(
        self, session_id: str, last_ts: float | None, last_hash: str | None
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE sessions
                SET jsonl_last_ts = ?, jsonl_last_hash = ?, last_activity = ?
                WHERE session_id = ?
                """,
                (last_ts, last_hash, time.time(), session_id),
            )
            self._conn.commit()

    def get_jsonl_state_by_user_id(
        self, user_id: int
    ) -> tuple[Optional[float], Optional[str]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT jsonl_last_ts, jsonl_last_hash
                FROM sessions
                WHERE user_id = ?
                ORDER BY last_activity DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    def update_session_chat_id(self, session_id: str, chat_id: int) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE sessions
                SET last_chat_id = ?, last_activity = ?
                WHERE session_id = ?
                """,
                (chat_id, time.time(), session_id),
            )
            self._conn.commit()

    def get_last_chat_id_by_user_id(self, user_id: int) -> Optional[int]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT last_chat_id
                FROM sessions
                WHERE user_id = ? AND last_chat_id IS NOT NULL
                ORDER BY last_activity DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def record_message(self, session_id: str, sender: str, content: str) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO messages (session_id, sender, content, ts) VALUES (?, ?, ?, ?)",
                (session_id, sender, content, time.time()),
            )
            self._conn.commit()

    def record_run(self, run: Run) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO runs (run_id, session_id, status, prompt, started_at, finished_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.session_id,
                    run.status.value,
                    run.prompt,
                    run.started_at,
                    run.finished_at,
                    run.error,
                ),
            )
            self._conn.commit()

    def update_run(
        self, run_id: str, status: RunStatus, finished_at: Optional[float], error: Optional[str]
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE runs SET status = ?, finished_at = ?, error = ? WHERE run_id = ?",
                (status.value, finished_at, error, run_id),
            )
            self._conn.commit()
