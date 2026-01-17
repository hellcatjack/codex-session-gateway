import atexit
import os
import sys
import threading

from .config import build_runtime_config
from .config_loader import load_app_config
from .logging_setup import setup_logging
from .store import Store
from .session_manager import SessionManager
from .codex_runner import CodexRunner
from .orchestrator import Orchestrator
from .adapters.telegram_adapter import TelegramAdapter
from .process_lock import ProcessLock


def main() -> None:
    setup_logging()
    app_config = load_app_config()

    db_path = app_config.base.db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    lock_path = app_config.base.lock_path
    process_lock = ProcessLock(lock_path)
    process_lock.acquire()
    atexit.register(process_lock.release)

    store = Store(db_path)
    store.init()

    threads: list[threading.Thread] = []
    for bot in app_config.bots:
        runtime_config = build_runtime_config(app_config.base, bot)
        session_manager = SessionManager(store)
        runner = CodexRunner(runtime_config)
        orchestrator = Orchestrator(
            runtime_config, session_manager, store, runner, bot_id=bot.name
        )
        adapter = TelegramAdapter(runtime_config, orchestrator, bot_id=bot.name)
        thread = threading.Thread(
            target=adapter.run, name=f"bot-{bot.name}", daemon=True
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise
