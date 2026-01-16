import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .config import Config
from .models import Run, RunStatus, SessionState, new_id
from .session_manager import SessionManager
from .store import Store
from .codex_runner import CodexRunner
from .stream_broker import StreamBroker


@dataclass
class _JsonlSyncState:
    path: Optional[str] = None
    inode: Optional[int] = None
    offset: int = 0
    handle: Optional[object] = None


SendTextFunc = Callable[[str], Awaitable[None]]
StreamSendFunc = Callable[[str, bool], Awaitable[None]]


class Orchestrator:
    def __init__(
        self,
        config: Config,
        session_manager: SessionManager,
        store: Store,
        runner: CodexRunner,
        bot_id: str = "default",
    ) -> None:
        self._config = config
        self._session_manager = session_manager
        self._store = store
        self._runner = runner
        self._bot_id = bot_id
        self._active_tasks: dict[int, asyncio.Task] = {}
        self._active_lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)
        self._jsonl_states: dict[str, _JsonlSyncState] = {}

    async def submit_prompt(
        self,
        user_id: int,
        prompt: str,
        send_status: SendTextFunc,
        send_stream: StreamSendFunc,
    ) -> None:
        session = await self._session_manager.get_or_create(user_id, self._bot_id)
        self._store.record_message(session.session_id, "user", prompt)

        async with self._active_lock:
            active_task = self._active_tasks.get(user_id)
            if active_task and not active_task.done():
                await self._session_manager.enqueue_prompt(user_id, prompt, self._bot_id)
                queued = await self._session_manager.peek_queue(user_id, self._bot_id)
                await send_status(
                    f"已收到新指令，当前任务结束后执行。排队中：{queued}"
                )
                return

            resume_id = session.resume_id
            task = asyncio.create_task(
                self._run_once(user_id, prompt, send_status, send_stream, resume_id)
            )
            self._active_tasks[user_id] = task
            self._logger.info("启动任务 user_id=%s bot_id=%s", user_id, self._bot_id)

    async def cancel_run(self, user_id: int, send_status: SendTextFunc) -> None:
        async with self._active_lock:
            task = self._active_tasks.get(user_id)
            if not task or task.done():
                await send_status("当前没有运行中的任务。")
                return
            task.cancel()
            await send_status("已请求停止当前任务。")
            self._logger.info("取消任务 user_id=%s", user_id)

    async def status(self, user_id: int, send_status: SendTextFunc) -> None:
        session = await self._session_manager.get_or_create(user_id, self._bot_id)
        queued = await self._session_manager.peek_queue(user_id, self._bot_id)
        resume_text = session.resume_id or "未设置"
        await send_status(
            f"会话状态：{session.state.value}，排队指令：{queued}，resume_id：{resume_text}"
        )

    async def is_running(self, user_id: int) -> bool:
        session = await self._session_manager.get_or_create(user_id, self._bot_id)
        return session.state == SessionState.RUNNING

    async def get_resume_id(self, user_id: int) -> Optional[str]:
        session = await self._session_manager.get_or_create(user_id, self._bot_id)
        return session.resume_id or self._config.codex_cli_resume_id

    async def set_chat_id(self, user_id: int, chat_id: int) -> None:
        await self._session_manager.set_chat_id(user_id, chat_id, self._bot_id)

    def get_last_chat_id(self, user_id: int) -> Optional[int]:
        return self._store.get_last_chat_id_by_user_id(user_id, self._bot_id)

    def _extract_jsonl_message(
        self, data: dict
    ) -> tuple[Optional[float], Optional[str]]:
        timestamp = self._runner.parse_timestamp(data.get("timestamp"))
        if data.get("type") != "response_item":
            return timestamp, None
        payload = data.get("payload") or {}
        if payload.get("type") != "message":
            return timestamp, None
        if payload.get("role") != "assistant":
            return timestamp, None
        content = payload.get("content") or []
        parts = []
        for item in content:
            if item.get("type") == "output_text":
                text = item.get("text")
                if text:
                    parts.append(text)
        if not parts:
            return timestamp, None
        return timestamp, "\n".join(parts).strip()

    async def poll_external_results(self, user_id: int, allow_send: bool) -> list[str]:
        resume_id = await self.get_resume_id(user_id)
        if not resume_id:
            return []
        session = await self._session_manager.get_or_create(user_id, self._bot_id)
        last_result_hash = None
        if session.last_result:
            normalized = self._runner.normalize_text_for_dedupe(session.last_result)
            if normalized:
                last_result_hash = hashlib.sha256(
                    normalized.encode("utf-8")
                ).hexdigest()
        last_ts, last_hash = self._store.get_jsonl_state_by_user_id(user_id, self._bot_id)
        state_key = f"{self._bot_id}:{resume_id}"
        state = self._jsonl_states.setdefault(state_key, _JsonlSyncState())
        path = self._runner.find_session_file(resume_id)
        if not path:
            return []

        def reset_handle() -> None:
            if state.handle is not None:
                try:
                    state.handle.close()
                except OSError:
                    pass
            state.handle = None
            state.path = None
            state.inode = None
            state.offset = 0

        if state.path != path:
            reset_handle()

        if state.handle is None:
            try:
                state.handle = open(path, "r", encoding="utf-8", errors="replace")
                stat = os.fstat(state.handle.fileno())
                state.inode = stat.st_ino
                state.path = path
            except OSError:
                reset_handle()
                return []

        try:
            stat = os.stat(path)
        except OSError:
            reset_handle()
            return []
        if state.inode is not None and stat.st_ino != state.inode:
            reset_handle()
            return []
        if stat.st_size < state.offset:
            reset_handle()
            return []

        if last_ts is None and last_hash is None:
            baseline = time.time()
            self._store.update_session_jsonl_state(session.session_id, baseline, None)
            return []

        state.handle.seek(state.offset)
        messages: list[str] = []
        updated = False
        for raw_line in state.handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp, text = self._extract_jsonl_message(data)
            if not text or timestamp is None:
                continue
            if last_ts is not None and timestamp < last_ts:
                continue
            normalized = self._runner.normalize_text_for_dedupe(text)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if last_result_hash and digest == last_result_hash:
                last_ts = max(last_ts or timestamp, timestamp)
                last_hash = digest
                updated = True
                continue
            if last_hash and digest == last_hash:
                last_ts = max(last_ts or timestamp, timestamp)
                updated = True
                continue
            if allow_send:
                messages.append(text)
                await self._session_manager.set_last_result(user_id, text, self._bot_id)
            last_ts = max(last_ts or timestamp, timestamp)
            last_hash = digest
            updated = True
        state.offset = state.handle.tell()

        if updated:
            self._store.update_session_jsonl_state(
                session.session_id, last_ts, last_hash
            )
        return messages

    async def last_result(
        self,
        user_id: int,
        send_status: SendTextFunc,
        send_stream: StreamSendFunc,
    ) -> None:
        session = await self._session_manager.get_or_create(user_id, self._bot_id)
        result = session.last_result
        if not result:
            result = self._store.get_last_result_by_user_id(user_id, self._bot_id)
            if result:
                await self._session_manager.set_last_result(user_id, result, self._bot_id)
        if not result:
            resume_id = session.resume_id or self._config.codex_cli_resume_id
            if resume_id:
                result = self._runner.read_last_assistant_message(resume_id)
                if result:
                    await self._session_manager.set_last_result(user_id, result, self._bot_id)
        if not result:
            await send_status("暂无可用结果。")
            return
        await send_stream(result, True)

    async def set_resume_id(
        self, user_id: int, resume_id: Optional[str], send_status: SendTextFunc
    ) -> None:
        self._logger.info("会话绑定已禁用 user_id=%s", user_id)
        await send_status("会话绑定已禁用，当前仅支持查看状态。")

    async def retry_last(
        self,
        user_id: int,
        last_prompt: Optional[str],
        send_status: SendTextFunc,
        send_stream: StreamSendFunc,
    ) -> None:
        if not last_prompt:
            await send_status("没有可重试的指令。")
            return
        await self.submit_prompt(user_id, last_prompt, send_status, send_stream)

    async def _run_once(
        self,
        user_id: int,
        prompt: str,
        send_status: SendTextFunc,
        send_stream: StreamSendFunc,
        resume_id: Optional[str],
    ) -> None:
        session = await self._session_manager.set_state(user_id, SessionState.RUNNING, self._bot_id)
        run = Run(run_id=new_id("run"), session_id=session.session_id, prompt=prompt)
        self._store.record_run(run)
        await self._session_manager.set_current_run(user_id, run.run_id, self._bot_id)
        self._logger.info("任务开始 run_id=%s user_id=%s bot_id=%s", run.run_id, user_id, self._bot_id)

        await send_status("已开始执行。")
        broker = StreamBroker(
            send_func=send_stream,
            flush_interval=self._config.stream_flush_interval,
            chunk_limit=self._config.message_chunk_limit,
        )
        await broker.start()

        status_override: Optional[str] = None
        final_message: Optional[str] = None

        async def on_output(text: str, is_error: bool) -> None:
            if is_error and not self._config.stream_include_stderr:
                self._logger.debug("stderr 已隐藏：%s", text)
                return
            await broker.push(text, is_error)

        async def on_status(status: str) -> None:
            nonlocal status_override
            status_override = status

        async def on_final(message: str) -> None:
            nonlocal final_message
            final_message = message

        try:
            return_code = await self._runner.run(
                prompt, on_output, on_status, resume_id=resume_id, on_final=on_final
            )
            if status_override == "timeout":
                run.status = RunStatus.TIMEOUT
                run.error = "运行超时"
            elif status_override == "canceled":
                run.status = RunStatus.CANCELED
                run.error = "任务被取消"
            elif return_code != 0:
                run.status = RunStatus.ERROR
                run.error = f"退出码 {return_code}"
            else:
                run.status = RunStatus.DONE
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELED
            run.error = "任务被取消"
            raise
        finally:
            run.finished_at = time.time()
            await broker.stop()
            self._store.update_run(run.run_id, run.status, run.finished_at, run.error)
            if final_message:
                await self._session_manager.set_last_result(user_id, final_message, self._bot_id)
            await self._session_manager.set_current_run(user_id, None, self._bot_id)
            await self._session_manager.set_state(user_id, SessionState.IDLE, self._bot_id)
            self._logger.info(
                "任务结束 run_id=%s status=%s bot_id=%s", run.run_id, run.status.value, self._bot_id
            )
            await send_status(self._format_run_summary(run))
            await self._post_run_cleanup(user_id, send_status, send_stream)

    async def _post_run_cleanup(
        self,
        user_id: int,
        send_status: SendTextFunc,
        send_stream: StreamSendFunc,
    ) -> None:
        async with self._active_lock:
            self._active_tasks.pop(user_id, None)

        queued_prompt = await self._session_manager.dequeue_prompt(user_id, self._bot_id)
        if queued_prompt:
            await self.submit_prompt(user_id, queued_prompt, send_status, send_stream)
        else:
            await send_status("等待新指令。")

    def _format_run_summary(self, run: Run) -> str:
        if run.status == RunStatus.DONE:
            return "运行完成。"
        if run.status == RunStatus.CANCELED:
            return "运行已取消。"
        if run.status == RunStatus.TIMEOUT:
            return "运行超时。"
        if run.status == RunStatus.ERROR:
            detail = run.error or "未知错误"
            return f"运行失败：{detail}"
        return "运行结束。"
