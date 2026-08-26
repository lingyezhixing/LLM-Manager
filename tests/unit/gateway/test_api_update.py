"""update_api 路由接线测试:GET 读启动检测缓存(checking 占位)、POST /check 手动刷新
并写缓存、POST /apply 传目标成功后 trigger_restart(置 restart_requested)、
UpdateError → 409。git 本体逻辑由 runtime/test_update.py 覆盖,此处 patch 编排函数,
只验证 API 层契约。"""

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
    app.state.update_status = None  # 启动检测未完成(与 app.py 初始态一致)
    app.state.restart_requested = False
    app.state.uvicorn_server = None  # dev 等价:apply 触发 os._exit(81) 前先置 flag
    return app


def _status() -> UpdateStatus:
    return UpdateStatus(
        ok=True,
        supported=True,
        current_version="v3.0.0a2",
        current_sha="abc1234",
        tag="v3.0.0a2",
        tag_available=True,
        tag_behind=1,
        commit_sha="def5678",
        commit_available=True,
        commit_behind=3,
    )


def test_status_returns_checking_placeholder_when_not_ready(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/update/status")
    assert r.status_code == 200
    assert r.json()["checking"] is True


def test_status_returns_cached_snapshot(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.app.state.update_status = _status()
        r = c.get("/api/update/status")
    j = r.json()
    assert j["checking"] is False and j["ok"] is True
    assert j["current_version"] == "v3.0.0a2"
    assert j["commit_behind"] == 3 and j["tag_behind"] == 1


def test_update_check_refreshes_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr("llm_manager.gateway.api.update_api.check_update", _status)
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/update/check")
        assert r.status_code == 200
        assert r.json()["ok"] is True and r.json()["checking"] is False
        assert c.app.state.update_status == _status()  # 已写回缓存


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


def test_startup_check_generation_guard(monkeypatch, tmp_path) -> None:
    """手动 check 把 gen 递增后,迟到的启动检测结果不得覆盖手动结果;gen 仍为
    0(无人手动 check)时启动结果正常落缓存。"""
    import asyncio
    import types

    from llm_manager.app import _startup_update_check

    startup = UpdateStatus(ok=True, current_version="startup")
    manual = UpdateStatus(ok=True, current_version="manual")
    monkeypatch.setattr("llm_manager.app.check_update", lambda: startup)

    def _fake_app(status, gen):
        return types.SimpleNamespace(
            state=types.SimpleNamespace(update_status=status, update_check_generation=gen)
        )

    superseded = _fake_app(manual, 1)
    asyncio.run(_startup_update_check(superseded))
    assert superseded.state.update_status == manual  # 启动结果被 gen 守卫拒绝

    fresh = _fake_app(None, 0)
    asyncio.run(_startup_update_check(fresh))
    assert fresh.state.update_status == startup  # 无手动介入 → 正常写入
