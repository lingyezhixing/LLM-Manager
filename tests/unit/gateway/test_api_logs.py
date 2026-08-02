"""GET /api/models/{alias}/logs* — legacy model log endpoints, now DB-backed.

Old URL + response shape preserved (frontend ModelLogPanel contract):
backfill/before paging (limit clamped 1..5000), search {matches: list[int],
total}, SSE stream (DB backfill then live tail). Data source is the model's
LATEST session (current session while running; most recent after stop) — so
logs stay readable after stop (persisted), and unknown aliases still 404.

SSE 测试直接驱动 _logs_stream 生成器(同 test_api_logs_sessions.py 的
_session_stream 模式):starlette TestClient 与 httpx ASGITransport 都会
await app(...) 到 ASGI 应用跑完才返回 —— 无限 SSE 流永不结束,任何客户端传输层
都会死锁。生成器单循环测试覆盖真实逻辑(DB 回填 + 广播实时行);HTTP 层路由/404
由其余端点测试经同步 TestClient 覆盖。
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager import config
from llm_manager.data import logs
from llm_manager.data.config_store import ConfigStore, write_appconfig
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.models import _logs_stream
from llm_manager.gateway.routes import register_routes
from llm_manager.state import ModelStatus


class _NoLife:
    """控制端点用不到 lifecycle;日志端点测试给个桩即可。"""
    async def ensure_running(self, alias, *, inc_pending=False): return ModelStatus.STOPPED
    async def stop(self, alias): return ModelStatus.STOPPED


_CFG = """
program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}
Local-Models:
  m1:
    aliases: ["m1"]
    mode: Chat
    port: 8001
    RTX4060:
      required_devices: ["rtx 4060"]
      command: {exe: "q.bat"}
      memory_mb: {"rtx 4060": 2048}
"""


@pytest.fixture
def app(tmp_path):
    """App + 独立 tmp DB + logs 接线(每测试独立,会话/行 id 从 1 起)。"""
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, config.load(p))
    store = ConfigStore(db)
    app = FastAPI()
    register_routes(app, _NoLife(), db, {})
    app.state.config_store = store
    app.state.db = db
    logs.reset()
    logs.init(db)
    yield app, db
    logs.reset()


def _seed(lines: list[tuple[str, str]]) -> int:
    """开 m1 模型日志会话、capture 各行并落库;返回 sid。"""
    sid = logs.start_session("model", "m1", "m1")
    for text, stream in lines:
        logs.capture("m1", text, stream)
    asyncio.run(logs.flush())
    return sid


def test_logs_stream_backfills_then_streams(app):
    app, db = app
    _seed([("old1", "out"), ("old2", "out"), ("error: boom", "err")])

    async def go():
        out = []
        gen = _logs_stream("m1", db, limit=10)
        async for frame in gen:
            out.append(json.loads(frame.removeprefix("data: ").strip()))
            if len(out) == 3:        # 取完回填 3 行即停(真端点无限)
                break
        await gen.aclose()           # 触发 finally → unsubscribe
        return out

    res = asyncio.run(go())
    assert [ll["text"] for ll in res] == ["old1", "old2", "error: boom"]
    assert res[0]["level"] == "info" and res[2]["level"] == "error"
    assert res[0]["id"] == 1 and res[2]["id"] == 3


def test_logs_stream_respects_level_filter_on_backfill(app):
    app, db = app
    _seed([("info line", "out"), ("error: x", "err"), ("info line2", "out")])

    async def go():
        out = []
        gen = _logs_stream("m1", db, limit=10, level="error")
        async for frame in gen:
            out.append(json.loads(frame.removeprefix("data: ").strip()))
            if len(out) == 1:
                break
        await gen.aclose()
        return out

    res = asyncio.run(go())
    assert len(res) == 1 and res[0]["text"] == "error: x" and res[0]["level"] == "error"


def test_logs_search_endpoint_returns_matches(app):
    app, db = app
    _seed([("ctx a", "out"), ("error: x", "err"), ("ctx b", "out"), ("error: y", "err")])
    with TestClient(app) as c:
        r = c.get("/api/models/m1/logs/search?q=error")
    assert r.status_code == 200
    j = r.json()
    assert j["matches"] == [2, 4] and j["total"] == 2


def test_logs_search_endpoint_level_filter(app):
    app, db = app
    _seed([("ERROR boom", "err"), ("error out", "out")])   # id1 error / id2 info(stream=out)
    with TestClient(app) as c:
        r = c.get("/api/models/m1/logs/search?q=error&level=error")
    assert r.json()["matches"] == [1]


def test_logs_before_endpoint_pages_older(app):
    app, db = app
    _seed([(f"line{i}", "out") for i in range(10)])        # ids 1..10
    with TestClient(app) as c:
        r = c.get("/api/models/m1/logs?before=6&limit=3")
    assert r.status_code == 200
    assert [ll["id"] for ll in r.json()] == [3, 4, 5]


def test_logs_still_readable_after_session_end(app):
    """停止后仍可读:end_session 收口后,列表/检索改读 DB 最新(已结束)会话。"""
    app, db = app
    sid = _seed([("persisted line", "out")])
    logs.end_session(sid)                                  # stop 收口:alias 映射移除,DB 行保留
    assert logs.resolve_session("m1") is None
    with TestClient(app) as c:
        r = c.get("/api/models/m1/logs")
        s = c.get("/api/models/m1/logs/search?q=persisted")
    assert r.status_code == 200
    assert [ll["text"] for ll in r.json()] == ["persisted line"]
    assert s.json() == {"matches": [1], "total": 1}


def test_logs_no_session_returns_empty(app):
    """从未启动:无最新会话 → 列表 []、search 空、stream 空流。"""
    app, db = app
    with TestClient(app) as c:
        r = c.get("/api/models/m1/logs")
        s = c.get("/api/models/m1/logs/search?q=x")

    async def go():
        return [f async for f in _logs_stream("m1", db, limit=10)]

    assert r.json() == []
    assert s.json() == {"matches": [], "total": 0}
    assert asyncio.run(go()) == []


def test_logs_unknown_alias_404(app):
    app, db = app
    with TestClient(app) as c:
        r = c.get("/api/models/nope/logs/search?q=x")
    assert r.status_code == 404


def test_logs_stream_ended_session_holds_open(app):
    """已收口会话:回填发完后生成器保持打开(不结束 → EventSource 不重连重放回填行)。"""
    app, db = app
    sid = _seed([("line1", "out"), ("line2", "out")])
    logs.end_session(sid)                              # 收口:无订阅者

    async def go():
        gen = _logs_stream("m1", db, limit=10)
        try:
            frames = [json.loads((await anext(gen)).removeprefix("data: ").strip())
                      for _ in range(2)]               # 回填 2 行
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(anext(gen), timeout=0.1)   # 无新数据,但生成器仍活着
        finally:
            await gen.aclose()
        return frames

    res = asyncio.run(go())
    assert [ll["text"] for ll in res] == ["line1", "line2"]


def test_logs_search_returns_all_matches(app):
    """旧契约:search 返回全部匹配(>500 不截断),total 为真实计数(面板 ‹/› 跳转可达全部)。"""
    app, db = app
    _seed([(f"x line {i}", "out") for i in range(600)])
    with TestClient(app) as c:
        r = c.get("/api/models/m1/logs/search?q=x")
    j = r.json()
    assert j["total"] == 600 and len(j["matches"]) == 600
    assert j["matches"] == list(range(1, 601))
