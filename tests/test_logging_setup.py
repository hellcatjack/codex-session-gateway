import logging


def test_setup_logging_mutes_httpx_request_logs(monkeypatch):
    # httpx at INFO logs full Telegram Bot API URLs (including bot tokens).
    # We always force it to WARNING+ to avoid leaking secrets to journal/log files.
    from src.logging_setup import setup_logging

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging.getLogger("httpx").setLevel(logging.NOTSET)

    setup_logging()

    # We want an explicit override (not inherited), because systemd/journal will
    # happily persist INFO logs if root level is INFO.
    assert logging.getLogger("httpx").level >= logging.WARNING
