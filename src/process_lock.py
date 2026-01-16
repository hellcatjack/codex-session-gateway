import fcntl
import os
from typing import Optional, TextIO


class ProcessLock:
    def __init__(self, path: str) -> None:
        self._path = path
        self._handle: Optional[TextIO] = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        lock_dir = os.path.dirname(self._path) or "."
        os.makedirs(lock_dir, exist_ok=True)
        handle = open(self._path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("检测到已有实例正在运行。") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
