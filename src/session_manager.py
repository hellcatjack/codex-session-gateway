import asyncio
import time
from typing import Optional

from .models import Session, SessionState
from .store import Store


class SessionManager:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._sessions: dict[int, Session] = {}
        self._lock = asyncio.Lock()

    def _get_or_create_locked(self, user_id: int) -> Session:
        session = self._sessions.get(user_id)
        if session is None:
            session = Session(user_id=user_id)
            self._sessions[user_id] = session
            self._store.record_session(session)
        return session

    async def get_or_create(self, user_id: int) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.last_activity = time.time()
            return session

    async def set_state(self, user_id: int, state: SessionState) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.state = state
            session.last_activity = time.time()
            self._store.update_session_state(session.session_id, state)
            return session

    async def set_current_run(self, user_id: int, run_id: Optional[str]) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.current_run_id = run_id
            session.last_activity = time.time()
            return session

    async def set_resume_id(self, user_id: int, resume_id: Optional[str]) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.resume_id = resume_id
            session.last_activity = time.time()
            self._store.update_session_resume_id(session.session_id, resume_id)
            return session

    async def set_last_result(self, user_id: int, last_result: Optional[str]) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.last_result = last_result
            session.last_activity = time.time()
            self._store.update_session_last_result(session.session_id, last_result)
            return session

    async def set_jsonl_state(
        self, user_id: int, last_ts: Optional[float], last_hash: Optional[str]
    ) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.jsonl_last_ts = last_ts
            session.jsonl_last_hash = last_hash
            session.last_activity = time.time()
            self._store.update_session_jsonl_state(
                session.session_id, last_ts, last_hash
            )
            return session

    async def set_chat_id(self, user_id: int, chat_id: int) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.last_chat_id = chat_id
            session.last_activity = time.time()
            self._store.update_session_chat_id(session.session_id, chat_id)
            if session.jsonl_last_ts is None and session.jsonl_last_hash is None:
                session.jsonl_last_ts = time.time()
                self._store.update_session_jsonl_state(
                    session.session_id, session.jsonl_last_ts, session.jsonl_last_hash
                )
            return session

    async def enqueue_prompt(self, user_id: int, prompt: str) -> Session:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            session.queue.append(prompt)
            session.last_activity = time.time()
            return session

    async def dequeue_prompt(self, user_id: int) -> Optional[str]:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            if not session.queue:
                return None
            session.last_activity = time.time()
            return session.queue.popleft()

    async def peek_queue(self, user_id: int) -> int:
        async with self._lock:
            session = self._get_or_create_locked(user_id)
            return len(session.queue)
