import pytest

from src.config import AppConfig, BaseConfig, BotConfig


def test_main_creates_session_manager_per_bot(monkeypatch, tmp_path):
    from src import main as main_mod

    created_orchestrators = []

    class FakeSessionManager:
        def __init__(self, store):
            self.store = store

    class FakeStore:
        def __init__(self, db_path):
            self.db_path = db_path

        def init(self):
            return None

    class FakeProcessLock:
        def __init__(self, path):
            self.path = path

        def acquire(self):
            return None

        def release(self):
            return None

    class FakeRunner:
        def __init__(self, config):
            self.config = config

    class FakeOrchestrator:
        def __init__(self, runtime_config, session_manager, store, runner, bot_id="default"):
            self.runtime_config = runtime_config
            self.session_manager = session_manager
            self.store = store
            self.runner = runner
            self.bot_id = bot_id
            created_orchestrators.append(self)

    class FakeAdapter:
        def __init__(self, runtime_config, orchestrator, bot_id="default"):
            self.runtime_config = runtime_config
            self.orchestrator = orchestrator
            self.bot_id = bot_id

        def run(self):
            return None

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            return None

        def join(self):
            return None

    base = BaseConfig(
        db_path=str(tmp_path / "db.sqlite3"),
        lock_path=str(tmp_path / "lock"),
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        stream_flush_interval=0.1,
        stream_include_stderr=False,
        progress_tick_interval=0.5,
        run_timeout_seconds=60.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    app_config = AppConfig(
        base=base,
        bots=[
            BotConfig(
                name="gateway",
                token="token1",
                allowed_user_ids={1},
                resume_id=None,
                codex_workdir="/tmp",
            ),
            BotConfig(
                name="stock",
                token="token2",
                allowed_user_ids={2},
                resume_id=None,
                codex_workdir="/tmp",
            ),
        ],
    )

    monkeypatch.setattr(main_mod, "load_app_config", lambda: app_config)
    monkeypatch.setattr(main_mod, "setup_logging", lambda: None)
    monkeypatch.setattr(main_mod, "ProcessLock", FakeProcessLock)
    monkeypatch.setattr(main_mod, "Store", FakeStore)
    monkeypatch.setattr(main_mod, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(main_mod, "CodexRunner", FakeRunner)
    monkeypatch.setattr(main_mod, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(main_mod, "TelegramAdapter", FakeAdapter)
    monkeypatch.setattr(main_mod.threading, "Thread", FakeThread)
    monkeypatch.setattr(main_mod.atexit, "register", lambda *_args, **_kwargs: None)

    main_mod.main()

    assert len(created_orchestrators) == len(app_config.bots)
    assert len({id(item.session_manager) for item in created_orchestrators}) == len(
        app_config.bots
    )
