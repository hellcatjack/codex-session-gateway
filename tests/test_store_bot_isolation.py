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
