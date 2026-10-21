"""`/api/requests*` 端点测试。用最小 app(只挂 requests 路由 + 内存注册表)。"""

import json

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.gateway.api.requests import build_requests_response, register_requests_routes
from llm_manager.gateway.live_requests import RequestRegistry, RequestStatus
from llm_manager.realtime import ModelFeed


def _registry() -> RequestRegistry:
    return RequestRegistry(retention=lambda: 20)


def _app(reg: RequestRegistry) -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_requests_routes(api)
    app.include_router(api)
    app.state.live_requests = reg
    app.state.request_feed = ModelFeed(lambda: build_requests_response(reg))
    return app


def _start(reg, *, model="m1", alias="a1", preview="hi"):
    return reg.start(
        model=model,
        alias=alias,
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview=preview,
    )


def test_list_filters_by_alias_and_status():
    reg = _registry()
    a = _start(reg, model="m1", alias="a1")
    _start(reg, model="m2", alias="a2")
    a.finish(RequestStatus.COMPLETED)

    with TestClient(_app(reg)) as c:
        assert len(c.get("/api/requests").json()["data"]) == 2
        assert len(c.get("/api/requests", params={"model": "a1"}).json()["data"]) == 1
        running = c.get("/api/requests", params={"status": "running"}).json()["data"]
        assert len(running) == 1 and running[0]["alias"] == "a2"


def test_list_frame_excludes_input():
    reg = _registry()
    _start(reg)
    with TestClient(_app(reg)) as c:
        item = c.get("/api/requests").json()["data"][0]
    assert "input" not in item and "request_body" not in item


def test_detail_404_for_unknown_id():
    with TestClient(_app(_registry())) as c:
        assert c.get("/api/requests/r-999999").status_code == 404


def test_detail_tail_cuts_suffix():
    reg = _registry()
    live = _start(reg)
    live.append_text("abcdef")
    with TestClient(_app(reg)) as c:
        j = c.get(f"/api/requests/{live.info.id}", params={"tail": 4}).json()
    assert j["text"] == "cdef"
    assert j["truncated"] is True
    assert j["request_preview"] == "hi"


def test_detail_stream_sends_snapshot_then_state_and_closes():
    reg = _registry()
    live = _start(reg)
    live.append_text("hello")
    live.finish(RequestStatus.COMPLETED)

    with (
        TestClient(_app(reg)) as c,
        c.stream("GET", f"/api/requests/{live.info.id}/stream") as r,
    ):
        assert r.status_code == 200
        frames = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [f["type"] for f in frames] == ["snapshot", "state"]
    assert frames[0]["text"] == "hello"
    assert frames[1]["request"]["status"] == "completed"


def test_cancel_unknown_404():
    with TestClient(_app(_registry())) as c:
        assert c.post("/api/requests/r-000404/cancel").status_code == 404


def test_cancel_finished_409_and_cancelling_202():
    reg = _registry()
    done = _start(reg)
    done.finish(RequestStatus.COMPLETED)
    running = _start(reg)

    with TestClient(_app(reg)) as c:
        assert c.post(f"/api/requests/{done.info.id}/cancel").status_code == 409
        assert c.post(f"/api/requests/{running.info.id}/cancel").status_code == 202
        # CANCELLING 期间重复取消:幂等 202(不是 409)
        assert c.post(f"/api/requests/{running.info.id}/cancel").status_code == 202


def test_clear_cancelled_scoped_by_model():
    reg = _registry()
    a = _start(reg, model="m1", alias="a1")
    a.finish(RequestStatus.CANCELLED)
    b = _start(reg, model="m2", alias="a2")
    b.finish(RequestStatus.CANCELLED)

    with TestClient(_app(reg)) as c:
        assert c.post("/api/requests/cancelled/clear", params={"model": "m1"}).json() == {
            "cleared": 1
        }
    remaining = {i.alias for i in reg.snapshot()}
    assert remaining == {"a2"}


def test_clear_cancelled_accepts_alias_as_well_as_primary():
    reg = _registry()
    live = _start(reg, model="primary-name", alias="alias-name")
    live.finish(RequestStatus.CANCELLED)

    with TestClient(_app(reg)) as c:
        assert c.post("/api/requests/cancelled/clear", params={"model": "alias-name"}).json() == {
            "cleared": 1
        }
    assert reg.snapshot() == []


def test_clear_all_when_no_model():
    reg = _registry()
    for model, alias in (("m1", "a1"), ("m2", "a2")):
        live = _start(reg, model=model, alias=alias)
        live.finish(RequestStatus.CANCELLED)

    with TestClient(_app(reg)) as c:
        assert c.post("/api/requests/cancelled/clear").json() == {"cleared": 2}
    assert reg.snapshot() == []
