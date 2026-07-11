from llm_manager.data.config_store import get_setting, set_setting, get_all_settings
from llm_manager.data.persistence import open_db


def test_set_get_setting_round_trip_and_upsert(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert get_setting(db, "host") is None
    set_setting(db, "host", "0.0.0.0")
    assert get_setting(db, "host") == "0.0.0.0"
    set_setting(db, "host", "127.0.0.1")          # upsert 覆盖
    assert get_setting(db, "host") == "127.0.0.1"
    assert get_all_settings(db) == {"host": "127.0.0.1"}
