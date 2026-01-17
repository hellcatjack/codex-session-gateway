from __future__ import annotations

import pytest

from src.adapters import telegram_adapter
from src.config import Config


class DummyApp:
    def __init__(self) -> None:
        self.handlers = []
        self.post_init = None
        self.run_polling_kwargs: dict[str, object] | None = None

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    def run_polling(self, *args, **kwargs) -> None:
        self.run_polling_kwargs = kwargs


class DummyBuilder:
    def __init__(self) -> None:
        self._token = None
        self._app = DummyApp()

    def token(self, token: str) -> "DummyBuilder":
        self._token = token
        return self

    def build(self) -> DummyApp:
        return self._app


class DummyOrchestrator:
    pass


def test_run_polling_disables_signal_handlers(monkeypatch) -> None:
    dummy_builder = DummyBuilder()
    monkeypatch.setattr(telegram_adapter, "ApplicationBuilder", lambda: dummy_builder)

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id="resume",
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=1.0,
        stream_include_stderr=False,
        progress_tick_interval=1.0,
        run_timeout_seconds=1.0,
        context_compaction_idle_timeout_seconds=1.0,
        no_output_idle_timeout_seconds=1.0,
        final_result_idle_timeout_seconds=1.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=1.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )

    adapter = telegram_adapter.TelegramAdapter(config, DummyOrchestrator())
    adapter.run()

    assert dummy_builder._app.run_polling_kwargs is not None
    assert dummy_builder._app.run_polling_kwargs.get("stop_signals", "missing") is None


class DummyMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class DummyBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []
        self._message_id = 100

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((chat_id, text))
        self._message_id += 1
        return DummyMessage(self._message_id)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str):
        self.edited.append((chat_id, message_id, text))


class DummyContext:
    def __init__(self, bot: DummyBot) -> None:
        self.bot = bot


@pytest.mark.asyncio
async def test_jsonl_messages_cached_until_run_finishes() -> None:
    class CacheOrchestrator:
        def __init__(self) -> None:
            self.running_calls = 0
            self.poll_calls = 0

        async def is_running(self, user_id: int) -> bool:
            self.running_calls += 1
            return self.running_calls == 1

        async def poll_external_results(self, user_id: int, allow_send: bool):
            self.poll_calls += 1
            if self.poll_calls == 1:
                return ["final result"]
            return []

        def get_last_chat_id(self, user_id: int):
            return 123

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id="resume",
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=1.0,
        stream_include_stderr=False,
        progress_tick_interval=1.0,
        run_timeout_seconds=1.0,
        context_compaction_idle_timeout_seconds=1.0,
        no_output_idle_timeout_seconds=1.0,
        final_result_idle_timeout_seconds=1.0,
        jsonl_sync_interval_seconds=1.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=1.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )

    orchestrator = CacheOrchestrator()
    adapter = telegram_adapter.TelegramAdapter(config, orchestrator)
    bot = DummyBot()
    context = DummyContext(bot)

    await adapter._sync_jsonl_tick(context)
    assert bot.sent == []

    await adapter._sync_jsonl_tick(context)
    assert bot.sent == [(123, "final result")]
