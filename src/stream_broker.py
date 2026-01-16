import asyncio
import time
from typing import Awaitable, Callable, List


SendFunc = Callable[[str, bool], Awaitable[None]]


class StreamBroker:
    def __init__(
        self,
        send_func: SendFunc,
        flush_interval: float,
        chunk_limit: int,
    ) -> None:
        self._send_func = send_func
        self._flush_interval = flush_interval
        self._chunk_limit = chunk_limit
        self._buffer: List[str] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._last_flush_at = 0.0

    async def start(self) -> None:
        if self._flush_task is not None:
            return
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._flush_task is None:
            return
        self._flush_task.cancel()
        await asyncio.gather(self._flush_task, return_exceptions=True)
        self._flush_task = None
        await self.flush(final=True)

    async def push(self, text: str, is_error: bool) -> None:
        line = f"[stderr] {text}" if is_error else text
        async with self._lock:
            self._buffer.append(line)

    async def flush(self, final: bool = False) -> None:
        async with self._lock:
            if not self._buffer:
                return
            content = "\n".join(self._buffer)
            self._buffer.clear()

        for chunk in self._split(content):
            await self._send_func(chunk, final)
        self._last_flush_at = time.monotonic()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self.flush(final=False)

    def _split(self, content: str) -> list[str]:
        if len(content) <= self._chunk_limit:
            return [content]
        chunks = []
        start = 0
        while start < len(content):
            end = min(start + self._chunk_limit, len(content))
            chunks.append(content[start:end])
            start = end
        return chunks
