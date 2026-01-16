import asyncio
import hashlib
import json
import logging
import os
import pty
import tempfile
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .config import Config


OutputHandler = Callable[[str, bool], Awaitable[None]]
StatusHandler = Callable[[str], Awaitable[None]]
FinalHandler = Callable[[str], Awaitable[None]]


class CodexRunner:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = logging.getLogger(__name__)
        self._cpr_request = b"\x1b[6n"
        self._cpr_response = b"\x1b[1;1R"

    @staticmethod
    def _is_context_compacted(text: str) -> bool:
        return "context compacted" in text.lower()

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
        env.setdefault("TERM", "xterm-256color")
        runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        bus_path = os.path.join(runtime_dir, "bus")
        env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
        if os.path.exists(bus_path):
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_path}")
        return env

    def _build_args_for_prompt(
        self, prompt: str, resume_id: str | None, output_last_message_path: str | None = None
    ) -> tuple[list[str], bool]:
        args = [self._config.codex_cli_cmd]
        active_resume_id = resume_id or self._config.codex_cli_resume_id
        if active_resume_id:
            args.append("exec")
            if self._config.codex_cli_skip_git_check:
                args.append("--skip-git-repo-check")
            if output_last_message_path:
                args.extend(["--output-last-message", output_last_message_path])
            args.extend(self._config.codex_cli_args)
            args.extend(["resume", active_resume_id])
            if self._config.codex_cli_input_mode == "arg":
                if self._config.codex_cli_approvals_mode:
                    self._logger.warning(
                        "arg 模式无法注入 /approvals 指令，已跳过"
                    )
                args.append(prompt)
            else:
                args.append("-")
            return args, True

        if output_last_message_path:
            args.extend(["--output-last-message", output_last_message_path])
        args.extend(self._config.codex_cli_args)
        if self._config.codex_cli_input_mode == "arg":
            if self._config.codex_cli_approvals_mode:
                self._logger.warning(
                    "arg 模式无法注入 /approvals 指令，已跳过"
                )
            args.append(prompt)
        return args, False

    def _build_input(self, prompt: str) -> str:
        approvals_mode = self._config.codex_cli_approvals_mode
        if approvals_mode:
            return f"/approvals {approvals_mode}\n{prompt}\n"
        return f"{prompt}\n"

    @staticmethod
    def _prepare_last_message_file() -> str | None:
        try:
            handle = tempfile.NamedTemporaryFile(
                prefix="codex-last-message-", suffix=".txt", delete=False
            )
            path = handle.name
            handle.close()
            return path
        except OSError:
            return None

    @staticmethod
    def _read_last_message(path: str | None) -> str | None:
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
        except OSError:
            return None
        return content or None

    @staticmethod
    def _codex_home() -> str:
        return os.getenv("CODEX_HOME", os.path.expanduser("~/.codex"))

    def _find_session_file(self, resume_id: str) -> str | None:
        sessions_dir = os.path.join(self._codex_home(), "sessions")
        if not os.path.isdir(sessions_dir):
            return None
        candidates: list[tuple[float, str]] = []
        for root, _, files in os.walk(sessions_dir):
            for name in files:
                if resume_id in name and name.endswith(".jsonl"):
                    path = os.path.join(root, name)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    candidates.append((mtime, path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def find_session_file(self, resume_id: str) -> str | None:
        return self._find_session_file(resume_id)

    @staticmethod
    def _parse_timestamp(value: str | None) -> float | None:
        if not value:
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @staticmethod
    def parse_timestamp(value: str | None) -> float | None:
        return CodexRunner._parse_timestamp(value)

    @classmethod
    def _extract_last_assistant_message_with_ts(
        cls, path: str
    ) -> tuple[str | None, float | None]:
        last_message = None
        last_timestamp = None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entry_timestamp = cls._parse_timestamp(data.get("timestamp"))
                    payload = data.get("payload") or {}
                    if data.get("type") == "event_msg":
                        if payload.get("type") == "agent_message":
                            message = payload.get("message")
                            if message:
                                last_message = message.strip()
                                last_timestamp = entry_timestamp
                        continue
                    if data.get("type") != "response_item":
                        continue
                    if payload.get("type") != "message":
                        continue
                    if payload.get("role") != "assistant":
                        continue
                    content = payload.get("content") or []
                    parts = []
                    for item in content:
                        if item.get("type") == "output_text":
                            text = item.get("text")
                            if text:
                                parts.append(text)
                    if parts:
                        last_message = "\n".join(parts).strip()
                        last_timestamp = entry_timestamp
        except OSError:
            return None, None
        return last_message or None, last_timestamp

    @classmethod
    def _extract_last_assistant_message(cls, path: str) -> str | None:
        message, _ = cls._extract_last_assistant_message_with_ts(path)
        return message

    def _read_last_assistant_message(self, resume_id: str) -> str | None:
        session_file = self._find_session_file(resume_id)
        if not session_file:
            return None
        return self._extract_last_assistant_message(session_file)

    def _read_last_assistant_message_after(
        self, resume_id: str, min_timestamp: float
    ) -> str | None:
        session_file = self._find_session_file(resume_id)
        if not session_file:
            return None
        message, timestamp = self._extract_last_assistant_message_with_ts(session_file)
        if not message or timestamp is None:
            return None
        if timestamp < min_timestamp:
            return None
        return message

    def read_last_assistant_message(self, resume_id: str) -> str | None:
        return self._read_last_assistant_message(resume_id)

    @staticmethod
    def _event_msg_text(data: dict) -> tuple[str | None, bool]:
        if data.get("type") != "event_msg":
            return None, False
        payload = data.get("payload") or {}
        event_type = payload.get("type")
        if event_type == "agent_message":
            message = payload.get("message")
            return (message.strip(), False) if message else (None, False)
        if event_type == "agent_reasoning":
            text = payload.get("text")
            return (text.strip(), True) if text else (None, True)
        return None, False

    @staticmethod
    def _summarize_reasoning(text: str) -> str:
        lowered = text.lower()
        tags: list[str] = []
        if any(word in lowered for word in ("plan", "规划", "计划")):
            tags.append("制定计划")
        if any(word in lowered for word in ("analyze", "analysis", "评估", "分析")):
            tags.append("分析需求")
        if any(word in lowered for word in ("config", "配置", "env", "环境")):
            tags.append("检查配置")
        if any(word in lowered for word in ("error", "fail", "失败", "问题")):
            tags.append("排查问题")
        if any(word in lowered for word in ("test", "pytest", "playwright", "测试")):
            tags.append("执行测试")
        if any(word in lowered for word in ("deploy", "systemctl", "service", "服务")):
            tags.append("部署/服务操作")
        if any(word in lowered for word in ("refactor", "重构")):
            tags.append("重构整理")
        if any(word in lowered for word in ("readme", "doc", "文档")):
            tags.append("更新文档")
        if any(word in lowered for word in ("verify", "验证")):
            tags.append("验证结果")
        if any(word in lowered for word in ("final", "summary", "最终", "总结")):
            tags.append("整理最终回复")
        if any(word in lowered for word in ("sqlite", "db", "数据库", "jsonl")):
            tags.append("检查数据与日志")
        if not tags:
            tags.append("整理任务与输出")
        summary = "；".join(tags[:4])
        trimmed = text.strip()
        return f"内部推理摘要：{summary}（已隐藏原文，长度{len(trimmed)}字）"

    @staticmethod
    def _normalize_text_for_dedupe(text: str) -> str:
        if not text:
            return ""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in normalized.split("\n")]
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def normalize_text_for_dedupe(text: str) -> str:
        return CodexRunner._normalize_text_for_dedupe(text)

    async def _tail_jsonl_events(
        self,
        resume_id: str,
        finished: asyncio.Event,
        emit: Callable[[str], Awaitable[None]],
    ) -> None:
        if not self._config.jsonl_stream_events:
            return
        session_file = None
        handle = None
        current_inode: int | None = None
        current_offset = 0
        last_stat_check = 0.0
        stat_interval = 0.5
        last_reasoning_at = 0.0
        last_message = None
        try:
            while not finished.is_set():
                if handle is None:
                    session_file = self._find_session_file(resume_id)
                    if not session_file:
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        handle = open(
                            session_file, "r", encoding="utf-8", errors="replace"
                        )
                        stat = os.fstat(handle.fileno())
                        current_inode = stat.st_ino
                        handle.seek(0, os.SEEK_END)
                        current_offset = handle.tell()
                    except OSError:
                        handle = None
                        await asyncio.sleep(0.5)
                        continue
                line = handle.readline()
                if not line:
                    now = time.monotonic()
                    if now - last_stat_check >= stat_interval:
                        last_stat_check = now
                        try:
                            stat = os.stat(session_file)
                        except OSError:
                            try:
                                handle.close()
                            except OSError:
                                pass
                            handle = None
                            current_inode = None
                            session_file = None
                            await asyncio.sleep(0.2)
                            continue
                        if current_inode is not None and stat.st_ino != current_inode:
                            try:
                                handle.close()
                            except OSError:
                                pass
                            handle = None
                            current_inode = None
                            await asyncio.sleep(0.2)
                            continue
                        if stat.st_size < current_offset:
                            try:
                                handle.close()
                            except OSError:
                                pass
                            handle = None
                            current_inode = None
                            await asyncio.sleep(0.2)
                            continue
                    await asyncio.sleep(0.2)
                    continue
                current_offset = handle.tell()
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text, is_reasoning = self._event_msg_text(data)
                if not text:
                    continue
                if is_reasoning:
                    now = time.monotonic()
                    if (
                        now - last_reasoning_at
                        < self._config.jsonl_reasoning_throttle_seconds
                    ):
                        continue
                    last_reasoning_at = now
                    mode = self._config.jsonl_reasoning_mode.strip().lower()
                    if mode == "summary":
                        await emit(self._summarize_reasoning(text))
                    else:
                        await emit("进度：内部推理进行中（内容已隐藏）。")
                    continue
                if text == last_message:
                    continue
                last_message = text
                await emit(text)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass

    async def _emit_final_message(
        self,
        on_final: FinalHandler | None,
        last_message_path: str | None,
        resume_id: str | None,
        min_timestamp: float | None,
    ) -> None:
        if not on_final:
            return
        last_message = self._read_last_message(last_message_path)
        if not last_message and resume_id:
            if min_timestamp is None:
                last_message = self._read_last_assistant_message(resume_id)
            else:
                last_message = self._read_last_assistant_message_after(
                    resume_id, min_timestamp
                )
        if last_message:
            await on_final(last_message)

    async def run(
        self,
        prompt: str,
        on_output: OutputHandler,
        on_status: StatusHandler,
        resume_id: str | None = None,
        on_final: FinalHandler | None = None,
    ) -> int:
        last_message_path = self._prepare_last_message_file()
        run_started_at = time.time()
        args, use_exec = self._build_args_for_prompt(
            prompt, resume_id, last_message_path
        )
        active_resume_id = resume_id or self._config.codex_cli_resume_id

        if self._config.codex_cli_use_pty and not use_exec:
            return await self._run_with_pty(
                prompt,
                on_output,
                on_status,
                resume_id,
                last_message_path,
                on_final,
            )

        stdin_setting = (
            asyncio.subprocess.PIPE
            if self._config.codex_cli_input_mode == "stdin"
            else asyncio.subprocess.DEVNULL
        )

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=stdin_setting,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._config.codex_workdir,
            env=self._build_env(),
        )
        self._logger.info("启动 Codex CLI 进程 pid=%s", proc.pid)

        last_output_at = time.monotonic()
        finished = asyncio.Event()
        context_compacted = False
        forced_done = False
        last_message_sent: str | None = None
        fallback_attempted = False
        sent_hashes: set[str] = set()

        def hash_text(text: str) -> str:
            normalized = self._normalize_text_for_dedupe(text)
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        async def emit_output(text: str, is_error: bool) -> None:
            if not is_error:
                if text:
                    digest = hash_text(text)
                    if digest in sent_hashes:
                        return
                    sent_hashes.add(digest)
            await on_output(text, is_error)

        async def read_stream(stream: asyncio.StreamReader, is_error: bool) -> None:
            nonlocal last_output_at
            nonlocal context_compacted
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                last_output_at = time.monotonic()
                if self._is_context_compacted(text):
                    context_compacted = True
                await emit_output(text, is_error)

        async def idle_watchdog() -> None:
            nonlocal forced_done
            nonlocal last_message_sent
            nonlocal fallback_attempted
            check_interval = min(
                1.0,
                max(0.1, self._config.context_compaction_idle_timeout_seconds / 2),
            )
            while not finished.is_set():
                await asyncio.sleep(check_interval)
                if finished.is_set():
                    break
                idle_for = time.monotonic() - last_output_at
                if (
                    self._config.final_result_idle_timeout_seconds > 0
                    and idle_for >= self._config.final_result_idle_timeout_seconds
                ):
                    final_message = self._read_last_message(last_message_path)
                    if not final_message and active_resume_id and not fallback_attempted:
                        fallback_attempted = True
                        final_message = self._read_last_assistant_message_after(
                            active_resume_id, run_started_at
                        )
                    if final_message:
                        if final_message != last_message_sent:
                            last_message_sent = final_message
                            await emit_output(final_message, False)
                        await emit_output("检测到最终结果已输出，自动结束任务。", False)
                        forced_done = True
                        self._logger.warning(
                            "检测到最终结果空闲，尝试结束进程 pid=%s",
                            proc.pid,
                        )
                        proc.terminate()
                        break
                if (
                    self._config.no_output_idle_timeout_seconds > 0
                    and idle_for >= self._config.no_output_idle_timeout_seconds
                ):
                    await emit_output("检测到长时间无输出，已自动结束。", False)
                    await on_status("timeout")
                    forced_done = True
                    self._logger.warning(
                        "检测到长时间无输出，尝试结束进程 pid=%s",
                        proc.pid,
                    )
                    proc.terminate()
                    break
                if not context_compacted:
                    continue
                if self._config.jsonl_stream_events:
                    continue
                if idle_for < self._config.context_compaction_idle_timeout_seconds:
                    continue
                last_message = self._read_last_message(last_message_path)
                if not last_message and active_resume_id and not fallback_attempted:
                    fallback_attempted = True
                    last_message = self._read_last_assistant_message_after(
                        active_resume_id, run_started_at
                    )
                if last_message and last_message != last_message_sent:
                    last_message_sent = last_message
                    await emit_output(last_message, False)
                await emit_output("检测到上下文压缩后无输出，已自动结束。", False)
                await on_status("timeout")
                forced_done = True
                self._logger.warning(
                    "检测到上下文压缩后无输出，尝试结束进程 pid=%s",
                    proc.pid,
                )
                proc.terminate()
                break

        async def progress_loop() -> None:
            while not finished.is_set():
                await asyncio.sleep(self._config.progress_tick_interval)
                if finished.is_set():
                    break
                idle_for = time.monotonic() - last_output_at
                if idle_for >= self._config.progress_tick_interval:
                    await on_output(
                        f"进度：运行中，已等待 {int(idle_for)} 秒", False
                    )

        async def jsonl_tailer() -> None:
            if not active_resume_id:
                return

            async def emit(text: str) -> None:
                nonlocal last_output_at
                last_output_at = time.monotonic()
                await emit_output(text, False)

            await self._tail_jsonl_events(active_resume_id, finished, emit)

        try:
            if proc.stdin is not None and self._config.codex_cli_input_mode == "stdin":
                proc.stdin.write(self._build_input(prompt).encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            tasks = [
                asyncio.create_task(read_stream(proc.stdout, False)),
                asyncio.create_task(read_stream(proc.stderr, True)),
                asyncio.create_task(idle_watchdog()),
                asyncio.create_task(jsonl_tailer()),
            ]
            if (
                self._config.progress_tick_interval > 0
                and not self._config.jsonl_stream_events
            ):
                tasks.append(asyncio.create_task(progress_loop()))

            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=self._config.run_timeout_seconds
                )
            except asyncio.TimeoutError:
                await on_status("timeout")
                proc.terminate()
                await proc.wait()

            finished.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._emit_final_message(
                on_final, last_message_path, active_resume_id, run_started_at
            )
            if forced_done:
                return 0
            if proc.returncode is None:
                return 0
            return proc.returncode
        except asyncio.CancelledError:
            await on_status("canceled")
            proc.terminate()
            await proc.wait()
            finished.set()
            self._logger.info("Codex CLI 已取消 pid=%s", proc.pid)
            raise
        finally:
            if last_message_path:
                try:
                    os.remove(last_message_path)
                except OSError:
                    pass

    async def _run_with_pty(
        self,
        prompt: str,
        on_output: OutputHandler,
        on_status: StatusHandler,
        resume_id: str | None,
        last_message_path: str | None,
        on_final: FinalHandler | None,
    ) -> int:
        args, _ = self._build_args_for_prompt(
            prompt, resume_id, last_message_path
        )
        master_fd, slave_fd = pty.openpty()
        run_started_at = time.time()
        active_resume_id = resume_id or self._config.codex_cli_resume_id

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self._config.codex_workdir,
            env=self._build_env(),
        )
        os.close(slave_fd)
        self._logger.info("启动 Codex CLI 伪终端 pid=%s", proc.pid)

        last_output_at = time.monotonic()
        finished = asyncio.Event()
        context_compacted = False
        forced_done = False
        last_message_sent: str | None = None
        fallback_attempted = False
        sent_hashes: set[str] = set()

        def hash_text(text: str) -> str:
            normalized = self._normalize_text_for_dedupe(text)
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        async def emit_output(text: str, is_error: bool) -> None:
            if not is_error:
                if text:
                    digest = hash_text(text)
                    if digest in sent_hashes:
                        return
                    sent_hashes.add(digest)
            await on_output(text, is_error)

        async def read_output() -> None:
            nonlocal last_output_at
            nonlocal context_compacted
            text_buffer = ""
            raw_buffer = b""
            while True:
                data = await asyncio.to_thread(os.read, master_fd, 1024)
                if not data:
                    break
                last_output_at = time.monotonic()
                raw_buffer += data
                while True:
                    idx = raw_buffer.find(self._cpr_request)
                    if idx == -1:
                        break
                    if idx > 0:
                        text_buffer += raw_buffer[:idx].decode(
                            "utf-8", errors="replace"
                        )
                    raw_buffer = raw_buffer[idx + len(self._cpr_request) :]
                    os.write(master_fd, self._cpr_response)

                if len(raw_buffer) > 3:
                    emit, raw_buffer = raw_buffer[:-3], raw_buffer[-3:]
                    text_buffer += emit.decode("utf-8", errors="replace")

                while "\n" in text_buffer:
                    line, text_buffer = text_buffer.split("\n", 1)
                    line = line.rstrip("\r")
                    if line:
                        if self._is_context_compacted(line):
                            context_compacted = True
                        await emit_output(line, False)

            if raw_buffer:
                text_buffer += raw_buffer.decode("utf-8", errors="replace")
            if text_buffer.strip():
                if self._is_context_compacted(text_buffer.strip()):
                    context_compacted = True
                await emit_output(text_buffer.strip(), False)

        async def idle_watchdog() -> None:
            nonlocal forced_done
            nonlocal last_message_sent
            nonlocal fallback_attempted
            check_interval = min(
                1.0,
                max(0.1, self._config.context_compaction_idle_timeout_seconds / 2),
            )
            while not finished.is_set():
                await asyncio.sleep(check_interval)
                if finished.is_set():
                    break
                idle_for = time.monotonic() - last_output_at
                if (
                    self._config.final_result_idle_timeout_seconds > 0
                    and idle_for >= self._config.final_result_idle_timeout_seconds
                ):
                    final_message = self._read_last_message(last_message_path)
                    if not final_message and active_resume_id and not fallback_attempted:
                        fallback_attempted = True
                        final_message = self._read_last_assistant_message_after(
                            active_resume_id, run_started_at
                        )
                    if final_message:
                        if final_message != last_message_sent:
                            last_message_sent = final_message
                            await emit_output(final_message, False)
                        await emit_output("检测到最终结果已输出，自动结束任务。", False)
                        forced_done = True
                        self._logger.warning(
                            "检测到最终结果空闲，尝试结束进程 pid=%s",
                            proc.pid,
                        )
                        proc.terminate()
                        break
                if (
                    self._config.no_output_idle_timeout_seconds > 0
                    and idle_for >= self._config.no_output_idle_timeout_seconds
                ):
                    await emit_output("检测到长时间无输出，已自动结束。", False)
                    await on_status("timeout")
                    forced_done = True
                    self._logger.warning(
                        "检测到长时间无输出，尝试结束进程 pid=%s",
                        proc.pid,
                    )
                    proc.terminate()
                    break
                if not context_compacted:
                    continue
                if self._config.jsonl_stream_events:
                    continue
                if idle_for < self._config.context_compaction_idle_timeout_seconds:
                    continue
                last_message = self._read_last_message(last_message_path)
                if not last_message and active_resume_id and not fallback_attempted:
                    fallback_attempted = True
                    last_message = self._read_last_assistant_message_after(
                        active_resume_id, run_started_at
                    )
                if last_message and last_message != last_message_sent:
                    last_message_sent = last_message
                    await emit_output(last_message, False)
                await emit_output("检测到上下文压缩后无输出，已自动结束。", False)
                await on_status("timeout")
                forced_done = True
                self._logger.warning(
                    "检测到上下文压缩后无输出，尝试结束进程 pid=%s",
                    proc.pid,
                )
                proc.terminate()
                break

        async def progress_loop() -> None:
            while not finished.is_set():
                await asyncio.sleep(self._config.progress_tick_interval)
                if finished.is_set():
                    break
                idle_for = time.monotonic() - last_output_at
                if idle_for >= self._config.progress_tick_interval:
                    await on_output(
                        f"进度：运行中，已等待 {int(idle_for)} 秒", False
                    )

        async def jsonl_tailer() -> None:
            if not active_resume_id:
                return

            async def emit(text: str) -> None:
                nonlocal last_output_at
                last_output_at = time.monotonic()
                await emit_output(text, False)

            await self._tail_jsonl_events(active_resume_id, finished, emit)

        try:
            tasks = [
                asyncio.create_task(read_output()),
                asyncio.create_task(idle_watchdog()),
                asyncio.create_task(jsonl_tailer()),
            ]
            if (
                self._config.progress_tick_interval > 0
                and not self._config.jsonl_stream_events
            ):
                tasks.append(asyncio.create_task(progress_loop()))

            if self._config.codex_cli_input_mode == "stdin":
                os.write(master_fd, self._build_input(prompt).encode("utf-8"))
            elif self._config.codex_cli_approvals_mode:
                self._logger.warning("PTY arg 模式无法注入 /approvals 指令，已跳过")

            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=self._config.run_timeout_seconds
                )
            except asyncio.TimeoutError:
                await on_status("timeout")
                proc.terminate()
                await proc.wait()

            finished.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._emit_final_message(
                on_final, last_message_path, active_resume_id, run_started_at
            )
            if forced_done:
                return 0
            if proc.returncode is None:
                return 0
            return proc.returncode
        except asyncio.CancelledError:
            await on_status("canceled")
            proc.terminate()
            await proc.wait()
            finished.set()
            raise
        finally:
            os.close(master_fd)
            if last_message_path:
                try:
                    os.remove(last_message_path)
                except OSError:
                    pass
