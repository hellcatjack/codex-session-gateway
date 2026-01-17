from __future__ import annotations

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
