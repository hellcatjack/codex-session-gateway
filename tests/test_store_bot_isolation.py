from src.models import Session
from src.store import Store


def test_bot_isolation(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    store.init()
    s1 = Session(user_id=1, bot_id="bot-a")
    s2 = Session(user_id=1, bot_id="bot-b")
    store.record_session(s1)
    store.record_session(s2)
    store.update_session_last_result(s1.session_id, "a")
    store.update_session_last_result(s2.session_id, "b")
    assert store.get_last_result_by_user_id(1, "bot-a") == "a"
    assert store.get_last_result_by_user_id(1, "bot-b") == "b"


def test_jsonl_state_selects_latest_non_null(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    store.init()

    old = Session(user_id=1, bot_id="bot-a")
    old.jsonl_last_ts = 1.0
    old.jsonl_last_hash = "hash-old"
    old.last_activity = 100.0
    store.record_session(old)

    newer = Session(user_id=1, bot_id="bot-a")
    newer.last_activity = 200.0
    store.record_session(newer)

    assert store.get_jsonl_state_by_user_id(1, "bot-a") == (1.0, "hash-old")
