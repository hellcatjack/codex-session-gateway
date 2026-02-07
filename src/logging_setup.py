import logging
import os


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx INFO logs include full URLs (Telegram Bot API tokens are embedded in URLs).
    # Force these libraries to WARNING+ to avoid leaking secrets into journal/log files.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
