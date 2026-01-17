import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Optional
from collections import deque


class SessionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    ERROR = "error"
    CANCELED = "canceled"


class RunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELED = "canceled"
    TIMEOUT = "timeout"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass
class Session:
    user_id: int
    bot_id: str = "default"
    session_id: str = field(default_factory=lambda: new_id("sess"))
    state: SessionState = SessionState.IDLE
    current_run_id: Optional[str] = None
    resume_id: Optional[str] = None
    last_result: Optional[str] = None
    jsonl_last_ts: Optional[float] = None
    jsonl_last_hash: Optional[str] = None
    last_chat_id: Optional[int] = None
    queue: Deque[str] = field(default_factory=deque)
    last_activity: float = field(default_factory=time.time)


@dataclass
class Run:
    run_id: str
    session_id: str
    prompt: str
    status: RunStatus = RunStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
