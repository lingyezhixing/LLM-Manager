import pytest
from fastapi.testclient import TestClient
from helpers import cfg as build_cfg
from helpers import model as build_model
from helpers import scheme as build_scheme

from llm_manager.app import create_app
from llm_manager.data.config_store import write_appconfig
from llm_manager.data.persistence import open_db


def test_app_boots_and_health_ok(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(
        db,
        build_cfg(
            models={
                "Qwen3-4B": build_model(
                    ("Qwen3-4B",),
                    10001,
                    schemes={
                        "RTX4060": build_scheme(devices=("rtx 4060",), memory_mb={"rtx 4060": 5120})
                    },
                )
            }
        ),
    )
    db.conn.close()
    app = create_app(db_path=tmp_path / "t.db")
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200


def test_create_app_validates_and_fails_fast_on_bad_config(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(
        db, build_cfg(models={"A": build_model(("x",), 1)})
    )  # 无 scheme → validate 报错
    db.conn.close()
    with pytest.raises(ValueError):
        create_app(db_path=tmp_path / "t.db")
