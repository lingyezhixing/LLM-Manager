"""update_api 路由接线测试:status 直出 check_update 结果;apply 成功后 trigger_restart
(置 restart_requested)、UpdateError → 409。git 本体逻辑由 runtime/test_update.py 覆盖,
此处 patch 编排函数,只验证 API 层契约。"""

from __future__ import annotations

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


def test_update_status_returns_asdict(monkeypatch, tmp_path):
    st = UpdateStatus(
        ok=True,
        error=None,
        current_version="v3.0.0a2",
        current_sha="abc1234",
        latest_version="v3.0.0a2",
        latest_sha="abc1234",
        up_to_date=True,
        available=False,
        dirty=False,
        conflicted=False,
        commits_behind=0,
    )
    monkeypatch.setattr("llm_manager.gateway.api.update_api.check_update", lambda: st)
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/update/status")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["current_version"] == "v3.0.0a2"
    assert j["up_to_date"] is True and j["available"] is False


def test_update_apply_success_triggers_restart(monkeypatch, tmp_path):
    calls: list[int] = []
    monkeypatch.setattr("llm_manager.gateway.api.common.os._exit", lambda code: calls.append(code))
    monkeypatch.setattr("llm_manager.gateway.api.update_api.apply_update", lambda: "newsha123")
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/update/apply")
        assert r.status_code == 202
        assert r.json() == {"updated": True, "sha": "newsha123"}
        assert c.app.state.restart_requested is True
        deadline = __import__("time").monotonic() + 2
        while not calls and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.05)
    assert calls == [81]


def test_update_apply_refuses_with_409(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "llm_manager.gateway.api.update_api.apply_update",
        lambda: (_ for _ in ()).throw(UpdateError("工作树有未提交改动")),
    )
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/update/apply")
    assert r.status_code == 409
    assert r.json()["detail"] == "工作树有未提交改动"
    assert c.app.state.restart_requested is False
