"""update_api 路由接线测试:status 直出 check_update 结果;apply 传目标成功后
trigger_restart(置 restart_requested)、UpdateError → 409。git 本体逻辑由
runtime/test_update.py 覆盖,此处 patch 编排函数,只验证 API 层契约。"""

from __future__ import annotations

import time

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.gateway.api.update_api import register_update_routes
from llm_manager.runtime.update import UpdateError, UpdateStatus


def _app(tmp_path):
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_update_routes(api)
    app.include_router(api)
    app.state.restart_requested = False
    app.state.uvicorn_server = None  # dev 等价:apply 触发 os._exit(81) 前先置 flag
    return app


def _status() -> UpdateStatus:
    return UpdateStatus(
        ok=True,
        error=None,
        current_version="v3.0.0a2",
        current_sha="abc1234",
        dirty=False,
        conflicted=False,
        tag="v3.0.0a2",
        tag_sha="abc1234",
        tag_available=True,
        commit_sha="def5678",
        commit_available=True,
    )


def test_update_status_returns_asdict(monkeypatch, tmp_path):
    monkeypatch.setattr("llm_manager.gateway.api.update_api.check_update", _status)
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/update/status")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["current_version"] == "v3.0.0a2"
    assert j["tag_available"] is True and j["commit_available"] is True


def test_update_apply_success_triggers_restart(monkeypatch, tmp_path):
    calls: list[int] = []
    seen: dict = {}
    monkeypatch.setattr("llm_manager.gateway.api.common.os._exit", lambda code: calls.append(code))
    monkeypatch.setattr(
        "llm_manager.gateway.api.update_api.apply_update",
        lambda target="commit": seen.__setitem__("target", target) or "newsha123",
    )
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/update/apply", json={"target": "tag"})
        assert r.status_code == 202
        assert r.json() == {"updated": True, "target": "tag", "sha": "newsha123"}
        assert seen["target"] == "tag"  # 目标透传
        assert c.app.state.restart_requested is True
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.05)
    assert calls == [81]


def test_update_apply_refuses_with_409(monkeypatch, tmp_path):
    def _boom(target="commit"):
        raise UpdateError("本地有未提交改动与更新冲突")

    monkeypatch.setattr("llm_manager.gateway.api.update_api.apply_update", _boom)
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/update/apply", json={"target": "commit"})
    assert r.status_code == 409
    assert r.json()["detail"] == "本地有未提交改动与更新冲突"
    assert c.app.state.restart_requested is False


def test_update_apply_rejects_bad_target(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/update/apply", json={"target": "release"})
    assert r.status_code == 422  # Pydantic Literal 校验
