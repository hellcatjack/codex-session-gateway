import atexit
import os
import sys

from .config import load_config
from .logging_setup import setup_logging
from .store import Store
from .session_manager import SessionManager
from .codex_runner import CodexRunner
from .orchestrator import Orchestrator
from .adapters.telegram_adapter import TelegramAdapter
from .process_lock import ProcessLock


def main() -> None:
    setup_logging()
    config = load_config()
    if not config.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 未配置")

    db_path = os.getenv("DB_PATH", os.path.join("data", "app.db"))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    lock_path = os.getenv(
        "LOCK_PATH", os.path.join(os.path.dirname(db_path), "app.lock")
    )
    process_lock = ProcessLock(lock_path)
    process_lock.acquire()
    atexit.register(process_lock.release)

    store = Store(db_path)
    store.init()

    session_manager = SessionManager(store)
    runner = CodexRunner(config)
    orchestrator = Orchestrator(config, session_manager, store, runner)

    adapter = TelegramAdapter(config, orchestrator)
    adapter.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise
