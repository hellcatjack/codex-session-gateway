import asyncio
import hashlib
import json
import logging
import os
import pty
import shutil
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
        self._auto_resume_cache: tuple[float, str | None] = (0.0, None)

    @staticmethod
    def _is_context_compacted(text: str) -> bool:
        return "context compacted" in text.lower()

    @staticmethod
    def _is_auto_resume_id(resume_id: str | None) -> bool:
        return bool(resume_id) and resume_id.strip().lower() == "auto"

    def resolve_resume_id(self, resume_id: str | None) -> str | None:
        if self._is_auto_resume_id(resume_id):
            return self._resolve_latest_session_id_for_cwd(self._config.codex_workdir)
        return resume_id

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
        self,
        prompt: str,
        resume_id: str | None,
        output_last_message_path: str | None = None,
        ignore_default_resume_id: bool = False,
    ) -> tuple[list[str], bool]:
        args = [self._config.codex_cli_cmd, "exec"]
        active_resume_id = resume_id if ignore_default_resume_id else (resume_id or self._config.codex_cli_resume_id)
        if self._is_auto_resume_id(active_resume_id):
            active_resume_id = self._resolve_latest_session_id_for_cwd(
                self._config.codex_workdir
            )
        if self._config.codex_cli_skip_git_check:
            args.append("--skip-git-repo-check")
        if output_last_message_path:
            args.extend(["--output-last-message", output_last_message_path])
        args.extend(self._config.codex_cli_args)
        if active_resume_id:
            args.extend(["resume", active_resume_id])
        if self._config.codex_cli_input_mode == "arg":
            if self._config.codex_cli_approvals_mode:
                self._logger.warning("arg 模式无法注入 /approvals 指令，已跳过")
            args.append(prompt)
        else:
            args.append("-")
        return args, True

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

    def _history_path(self) -> str:
        return os.path.join(self._codex_home(), "history.jsonl")

    def _append_history_entry(self, session_id: str, text: str) -> None:
        cleaned = (text or "").strip()
        if not session_id or not cleaned:
            return
        path = self._history_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {"session_id": session_id, "ts": int(time.time()), "text": cleaned}
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._logger.warning("写入 Codex history.jsonl 失败: %s", exc)

    def _find_latest_session_id_for_cwd_since(
        self,
        cwd: str,
        since_epoch_seconds: float,
        require_source: str | None = None,
        require_originator: str | None = None,
    ) -> str | None:
        sessions_dir = os.path.join(self._codex_home(), "sessions")
        if not os.path.isdir(sessions_dir):
            return None
        cwd_target = os.path.realpath(os.path.normpath(cwd))
        cwd_prefix = cwd_target if cwd_target.endswith(os.sep) else f"{cwd_target}{os.sep}"
        best: tuple[float, str] | None = None  # (mtime, session_id)
        # Slight tolerance in case of coarse mtime resolution.
        since = max(0.0, since_epoch_seconds - 1.0)
        for root, _, files in os.walk(sessions_dir):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime < since:
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        line = handle.readline().strip()
                except OSError:
                    continue
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "session_meta":
                    continue
                payload = data.get("payload") or {}
                session_id = payload.get("id")
                session_cwd = payload.get("cwd")
                if not session_id or not session_cwd:
                    continue
                session_cwd = os.path.realpath(os.path.normpath(str(session_cwd)))
                if session_cwd != cwd_target and not session_cwd.startswith(cwd_prefix):
                    continue
                if self._is_subagent_session(payload.get("source")):
                    continue
                if require_source is not None and payload.get("source") != require_source:
                    continue
                if (
                    require_originator is not None
                    and payload.get("originator") != require_originator
                ):
                    continue
                if best is None or mtime > best[0]:
                    best = (mtime, str(session_id))
        return best[1] if best else None

    def _promote_session_for_cli_resume(self, session_id: str) -> None:
        path = self._find_session_file(session_id)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as inp:
                first = inp.readline()
                if not first:
                    return
                try:
                    meta = json.loads(first)
                except json.JSONDecodeError:
                    return
                if meta.get("type") != "session_meta":
                    return
                payload = meta.get("payload") or {}
                # Only rewrite exec-created sessions; don't mutate real interactive ones.
                if payload.get("source") != "exec" and payload.get("originator") != "codex_exec":
                    return
                if payload.get("source") == "cli" and payload.get("originator") == "codex_cli_rs":
                    return
                payload["originator"] = "codex_cli_rs"
                payload["source"] = "cli"
                meta["payload"] = payload
                rewritten = json.dumps(meta, ensure_ascii=False) + "\n"

                tmp_dir = os.path.dirname(path) or "."
                fd, tmp_path = tempfile.mkstemp(prefix=".tmp-codex-session-", dir=tmp_dir)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as out:
                        out.write(rewritten)
                        shutil.copyfileobj(inp, out)
                    os.replace(tmp_path, path)
                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except OSError:
                        pass
        except OSError as exc:
            self._logger.warning("提升 session_meta 失败 session_id=%s err=%s", session_id, exc)

    @classmethod
    def _is_subagent_session(cls, session_source: object) -> bool:
        if isinstance(session_source, dict):
            return "subagent" in session_source
        return False

    def _resolve_latest_session_id_for_cwd(self, cwd: str) -> str | None:
        # Cache for a short time; JSONL sync tick is already periodic, and this
        # avoids re-scanning on every call.
        now = time.monotonic()
        last_checked, cached = self._auto_resume_cache
        if now - last_checked < 0.5:
            return cached

        sessions_dir = os.path.join(self._codex_home(), "sessions")
        # Prefer the most recently *active* main session under codex_workdir.
        # Session meta timestamps reflect session creation time, but long-running
        # sessions can still be actively written after newer sessions were created.
        best: tuple[float, str] | None = None  # (mtime, session_id)
        cwd_target = os.path.realpath(os.path.normpath(cwd))
        cwd_prefix = cwd_target if cwd_target.endswith(os.sep) else f"{cwd_target}{os.sep}"
        if os.path.isdir(sessions_dir):
            for root, _, files in os.walk(sessions_dir):
                for name in files:
                    if not name.endswith(".jsonl"):
                        continue
                    path = os.path.join(root, name)
                    try:
                        with open(
                            path, "r", encoding="utf-8", errors="replace"
                        ) as handle:
                            line = handle.readline().strip()
                    except OSError:
                        continue
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") != "session_meta":
                        continue
                    payload = data.get("payload") or {}
                    session_cwd = payload.get("cwd")
                    if not session_cwd:
                        continue
                    session_cwd = os.path.realpath(os.path.normpath(str(session_cwd)))
                    if session_cwd != cwd_target and not session_cwd.startswith(cwd_prefix):
                        continue
                    if self._is_subagent_session(payload.get("source")):
                        continue
                    session_id = payload.get("id")
                    if not session_id:
                        continue
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        continue
                    if best is None or mtime > best[0]:
                        best = (mtime, str(session_id))

        resolved = best[1] if best else None
        self._auto_resume_cache = (now, resolved)
        return resolved

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
        follow_latest: bool = False,
        on_session_change: Callable[[str], None] | None = None,
    ) -> None:
        if not self._config.jsonl_stream_events:
            return
        current_resume_id: str | None = resume_id
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
                    if follow_latest:
                        desired = self._resolve_latest_session_id_for_cwd(self._config.codex_workdir)
                        if desired:
                            if desired != current_resume_id:
                                current_resume_id = desired
                                last_message = None
                                if on_session_change is not None:
                                    on_session_change(desired)
                        else:
                            await asyncio.sleep(0.5)
                            continue
                    if not current_resume_id:
                        await asyncio.sleep(0.5)
                        continue
                    session_file = self._find_session_file(current_resume_id)
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
                        if follow_latest:
                            desired = self._resolve_latest_session_id_for_cwd(self._config.codex_workdir)
                            if desired and desired != current_resume_id:
                                try:
                                    handle.close()
                                except OSError:
                                    pass
                                handle = None
                                current_inode = None
                                session_file = None
                                current_offset = 0
                                current_resume_id = desired
                                last_message = None
                                if on_session_change is not None:
                                    on_session_change(desired)
                                await asyncio.sleep(0.2)
                                continue
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
                        self._config.jsonl_reasoning_throttle_seconds > 0
                        and now - last_reasoning_at
                        < self._config.jsonl_reasoning_throttle_seconds
                    ):
                        continue
                    last_reasoning_at = now
                    mode = self._config.jsonl_reasoning_mode.strip().lower()
                    if mode == "summary":
                        await emit(self._summarize_reasoning(text))
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
        emit_output: Callable[[str], Awaitable[None]] | None = None,
        already_emitted: Callable[[str], bool] | None = None,
    ) -> None:
        if not on_final and emit_output is None:
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
            should_emit = True
            if already_emitted is not None and already_emitted(last_message):
                should_emit = False
            if emit_output is not None and should_emit:
                await emit_output(last_message)
            if on_final is not None:
                await on_final(last_message)

    async def run(
        self,
        prompt: str,
        on_output: OutputHandler,
        on_status: StatusHandler,
        resume_id: str | None = None,
        on_final: FinalHandler | None = None,
    ) -> int:
        # Support a subset of Codex interactive slash commands in Telegram:
        # `/new <prompt>` should start a brand-new session (i.e. do NOT resume).
        raw_prompt = prompt.strip()
        force_new_session = False
        if raw_prompt.startswith("/"):
            parts = raw_prompt.split(maxsplit=1)
            if parts and parts[0].lower() == "/new":
                force_new_session = True
                prompt = parts[1] if len(parts) > 1 else ""

        last_message_path = self._prepare_last_message_file()
        run_started_at = time.time()
        raw_resume_id = None if force_new_session else (resume_id or self._config.codex_cli_resume_id)
        follow_latest = force_new_session or self._is_auto_resume_id(raw_resume_id)
        resolved_resume_id = self.resolve_resume_id(
            raw_resume_id
        )
        args, use_exec = self._build_args_for_prompt(
            prompt,
            resolved_resume_id,
            last_message_path,
            ignore_default_resume_id=force_new_session,
        )
        active_resume_id = resolved_resume_id

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
        emitted_buffer = ""
        emitted_buffer_compact = ""
        emitted_buffer_max_chars = 200_000

        def hash_text(text: str) -> str:
            normalized = self._normalize_text_for_dedupe(text)
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        def append_emitted(text: str) -> None:
            nonlocal emitted_buffer
            nonlocal emitted_buffer_compact

            normalized = self._normalize_text_for_dedupe(text)
            if not normalized:
                return
            if emitted_buffer:
                emitted_buffer = f"{emitted_buffer}\n{normalized}"
            else:
                emitted_buffer = normalized
            emitted_buffer_compact = f"{emitted_buffer_compact}{normalized}"
            if len(emitted_buffer) > emitted_buffer_max_chars:
                emitted_buffer = emitted_buffer[-emitted_buffer_max_chars:]
            if len(emitted_buffer_compact) > emitted_buffer_max_chars:
                emitted_buffer_compact = emitted_buffer_compact[-emitted_buffer_max_chars:]

        def already_emitted_final(message: str) -> bool:
            # When Codex streams output line-by-line and also writes the final message
            # to `--output-last-message`, emitting the combined block again duplicates
            # what the user already saw. Use a rolling buffer to detect this.
            if not message:
                return False
            normalized = self._normalize_text_for_dedupe(message)
            if not normalized:
                return False
            if normalized in emitted_buffer:
                return True
            compact = normalized.replace("\n", "")
            if compact and compact in emitted_buffer_compact:
                return True
            # Some streamers may strip newlines; try a newline-insensitive match.
            flattened = normalized.replace("\n", "")
            if flattened and flattened in emitted_buffer.replace("\n", ""):
                return True
            return False

        async def emit_output(text: str, is_error: bool) -> None:
            if not is_error:
                if text:
                    digest = hash_text(text)
                    if digest in sent_hashes:
                        return
                    sent_hashes.add(digest)
                    append_emitted(text)
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

        async def jsonl_tailer() -> None:
            if not active_resume_id and not follow_latest:
                return

            async def emit(text: str) -> None:
                nonlocal last_output_at
                last_output_at = time.monotonic()
                await emit_output(text, False)

            def on_session_change(new_id: str) -> None:
                nonlocal active_resume_id
                active_resume_id = new_id

            await self._tail_jsonl_events(
                active_resume_id or "",
                finished,
                emit,
                follow_latest=follow_latest,
                on_session_change=on_session_change if follow_latest else None,
            )

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
                on_final,
                last_message_path,
                active_resume_id,
                run_started_at,
                emit_output=lambda message: emit_output(message, False),
                already_emitted=already_emitted_final,
            )
            # Make `/new` sessions discoverable by Codex CLI `/resume`.
            if force_new_session:
                new_session_id = active_resume_id or self._find_latest_session_id_for_cwd_since(
                    self._config.codex_workdir,
                    run_started_at,
                    require_source="exec",
                )
                if new_session_id:
                    self._promote_session_for_cli_resume(new_session_id)
                    self._append_history_entry(new_session_id, prompt)
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
        raw_resume_id = resume_id or self._config.codex_cli_resume_id
        follow_latest = self._is_auto_resume_id(raw_resume_id)
        resolved_resume_id = self.resolve_resume_id(raw_resume_id)
        args, _ = self._build_args_for_prompt(
            prompt, resolved_resume_id, last_message_path
        )
        master_fd, slave_fd = pty.openpty()
        run_started_at = time.time()
        active_resume_id = resolved_resume_id

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
        emitted_buffer = ""
        emitted_buffer_compact = ""
        emitted_buffer_max_chars = 200_000

        def hash_text(text: str) -> str:
            normalized = self._normalize_text_for_dedupe(text)
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        def append_emitted(text: str) -> None:
            nonlocal emitted_buffer
            nonlocal emitted_buffer_compact

            normalized = self._normalize_text_for_dedupe(text)
            if not normalized:
                return
            if emitted_buffer:
                emitted_buffer = f"{emitted_buffer}\n{normalized}"
            else:
                emitted_buffer = normalized
            emitted_buffer_compact = f"{emitted_buffer_compact}{normalized}"
            if len(emitted_buffer) > emitted_buffer_max_chars:
                emitted_buffer = emitted_buffer[-emitted_buffer_max_chars:]
            if len(emitted_buffer_compact) > emitted_buffer_max_chars:
                emitted_buffer_compact = emitted_buffer_compact[-emitted_buffer_max_chars:]

        def already_emitted_final(message: str) -> bool:
            if not message:
                return False
            normalized = self._normalize_text_for_dedupe(message)
            if not normalized:
                return False
            if normalized in emitted_buffer:
                return True
            compact = normalized.replace("\n", "")
            if compact and compact in emitted_buffer_compact:
                return True
            flattened = normalized.replace("\n", "")
            if flattened and flattened in emitted_buffer.replace("\n", ""):
                return True
            return False

        async def emit_output(text: str, is_error: bool) -> None:
            if not is_error:
                if text:
                    digest = hash_text(text)
                    if digest in sent_hashes:
                        return
                    sent_hashes.add(digest)
                    append_emitted(text)
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

        async def jsonl_tailer() -> None:
            if not active_resume_id and not follow_latest:
                return

            async def emit(text: str) -> None:
                nonlocal last_output_at
                last_output_at = time.monotonic()
                await emit_output(text, False)

            def on_session_change(new_id: str) -> None:
                nonlocal active_resume_id
                active_resume_id = new_id

            await self._tail_jsonl_events(
                active_resume_id or "",
                finished,
                emit,
                follow_latest=follow_latest,
                on_session_change=on_session_change if follow_latest else None,
            )

        try:
            tasks = [
                asyncio.create_task(read_output()),
                asyncio.create_task(idle_watchdog()),
                asyncio.create_task(jsonl_tailer()),
            ]

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
                on_final,
                last_message_path,
                active_resume_id,
                run_started_at,
                emit_output=lambda message: emit_output(message, False),
                already_emitted=already_emitted_final,
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
