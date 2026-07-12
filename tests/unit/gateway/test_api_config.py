import time
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.config_store import ConfigStore, seed_defaults
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.config_api import register_config_routes


def _app(tmp_path):
    db = open_db(tmp_path / "t.db")
    seed_defaults(db)                       # host 0.0.0.0 / port 8080 / log_dir logs / db_path ...
    store = ConfigStore(db, scripts_dir=tmp_path / "scripts")
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_config_routes(api)
    app.include_router(api)
    app.state.db = db
    app.state.config_store = store
    app.state.boot_program = {"host": "0.0.0.0", "port": "8080",
                              "db_path": "data/llm_manager.db", "log_dir": "logs"}
    app.state.started_at = time.time()
    return app


def test_system_info_returns_db_logdir_version(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/system/info")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j["version"], str)
    assert isinstance(j["started_at"], (int, float))
    assert j["log_dir"] == "logs"
    assert "db_path" in j
