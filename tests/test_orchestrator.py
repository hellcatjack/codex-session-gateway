import asyncio
import json
import pytest

from src.codex_runner import CodexRunner
from src.config import Config
from src.orchestrator import Orchestrator
from src.session_manager import SessionManager
from src.store import Store


class ControlledRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls: list[str] = []
        self.final_message: str | None = None
        self.session_file: str | None = None

    async def run(self, prompt, on_output, on_status, resume_id=None, on_final=None):
        self.calls.append(prompt)
        self.started.set()
        await on_output("ok", False)
        if on_final and self.final_message:
            await on_final(self.final_message)
        await self.finish.wait()
        return 0

    def read_last_assistant_message(self, resume_id: str) -> str | None:
        return None

    def normalize_text_for_dedupe(self, text: str) -> str:
        return text

    def find_session_file(self, resume_id: str) -> str | None:
        return self.session_file

    def parse_timestamp(self, value: str | None) -> float | None:
        return CodexRunner.parse_timestamp(value)


@pytest.mark.asyncio
async def test_orchestrator_queue_processes_next_prompt(tmp_path):
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.5,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    store = Store(str(tmp_path / "test.db"))
    store.init()
    session_manager = SessionManager(store)
    runner = ControlledRunner()
    orchestrator = Orchestrator(config, session_manager, store, runner)

    status_messages = []
    stream_messages = []

    async def send_status(msg: str) -> None:
        status_messages.append(msg)

    async def send_stream(text: str, final: bool) -> None:
        stream_messages.append(text)

    await orchestrator.submit_prompt(1, "first", send_status, send_stream)
    await runner.started.wait()
    await orchestrator.submit_prompt(1, "second", send_status, send_stream)

    queued = await session_manager.peek_queue(1)
    assert queued == 1

    runner.finish.set()
    await asyncio.sleep(0.05)

    assert runner.calls == ["first", "second"]
    assert any("任务结束" in msg or "运行完成" in msg for msg in status_messages)


@pytest.mark.asyncio
async def test_orchestrator_set_resume_id_disabled(tmp_path):
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.5,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    store = Store(str(tmp_path / "test.db"))
    store.init()
    session_manager = SessionManager(store)
    runner = ControlledRunner()
    orchestrator = Orchestrator(config, session_manager, store, runner)

    status_messages = []

    async def send_status(msg: str) -> None:
        status_messages.append(msg)

    await orchestrator.set_resume_id(1, "resume-abc", send_status)
    session = await session_manager.get_or_create(1)

    assert session.resume_id is None
    assert any("禁用" in msg for msg in status_messages)


@pytest.mark.asyncio
async def test_orchestrator_last_result_returns_final_message(tmp_path):
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.5,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    store = Store(str(tmp_path / "test.db"))
    store.init()
    session_manager = SessionManager(store)
    runner = ControlledRunner()
    runner.final_message = "final answer"
    orchestrator = Orchestrator(config, session_manager, store, runner)

    status_messages = []
    stream_messages = []

    async def send_status(msg: str) -> None:
        status_messages.append(msg)

    async def send_stream(text: str, final: bool) -> None:
        stream_messages.append(text)

    await orchestrator.submit_prompt(1, "first", send_status, send_stream)
    await runner.started.wait()
    runner.finish.set()
    await asyncio.sleep(0.05)

    stream_messages.clear()
    await orchestrator.last_result(1, send_status, send_stream)

    assert "final answer" in stream_messages


@pytest.mark.asyncio
async def test_orchestrator_last_result_falls_back_to_store(tmp_path):
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.5,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    store = Store(str(tmp_path / "test.db"))
    store.init()
    session_manager = SessionManager(store)
    runner = ControlledRunner()
    orchestrator = Orchestrator(config, session_manager, store, runner)

    from src.models import Session

    previous_session = Session(user_id=1)
    previous_session.last_result = "stored result"
    store.record_session(previous_session)

    status_messages = []
    stream_messages = []

    async def send_status(msg: str) -> None:
        status_messages.append(msg)

    async def send_stream(text: str, final: bool) -> None:
        stream_messages.append(text)

    await orchestrator.last_result(1, send_status, send_stream)

    assert "stored result" in stream_messages


@pytest.mark.asyncio
async def test_orchestrator_jsonl_sync_dedupes_last_result(tmp_path, monkeypatch):
    resume_id = "resume-sync"
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions"
    sessions_dir.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    session_file = sessions_dir / f"rollout-1-{resume_id}.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same result"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=resume_id,
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.5,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    store = Store(str(tmp_path / "test.db"))
    store.init()
    session_manager = SessionManager(store)
    runner = ControlledRunner()
    runner.session_file = str(session_file)
    orchestrator = Orchestrator(config, session_manager, store, runner)

    await session_manager.set_last_result(1, "same result")
    await session_manager.set_jsonl_state(1, 0.0, None)

    messages = await orchestrator.poll_external_results(1, allow_send=True)
    assert messages == []
    last_ts, last_hash = store.get_jsonl_state_by_user_id(1)
    assert last_ts is not None
    assert last_hash is not None
