import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager import config
from llm_manager.data import logs
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


def _app(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    app = FastAPI()
    register_routes(app, _NoLife(), config.load(p), open_db(Path(":memory:")), {})
    return app


def test_logs_stream_backfills_then_streams():
    logs.reset()
    logs.capture("m1", "old1", "out")
    logs.capture("m1", "old2", "out")
    logs.capture("m1", "error: boom", "err")

    async def go():
        out = []
        gen = _logs_stream("m1", limit=10)
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


def test_logs_stream_respects_level_filter_on_backfill():
    logs.reset()
    logs.capture("m1", "info line", "out")
    logs.capture("m1", "error: x", "err")
    logs.capture("m1", "info line2", "out")

    async def go():
        out = []
        gen = _logs_stream("m1", limit=10, level="error")
        async for frame in gen:
            out.append(json.loads(frame.removeprefix("data: ").strip()))
            if len(out) == 1:
                break
        await gen.aclose()
        return out

    res = asyncio.run(go())
    assert len(res) == 1 and res[0]["text"] == "error: x" and res[0]["level"] == "error"


def test_logs_search_endpoint_returns_matches(tmp_path):
    logs.reset()
    logs.capture("m1", "ctx a", "out")
    logs.capture("m1", "error: x", "err")
    logs.capture("m1", "ctx b", "out")
    logs.capture("m1", "error: y", "err")
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/models/m1/logs/search?q=error")
    assert r.status_code == 200
    j = r.json()
    assert j["matches"] == [2, 4] and j["total"] == 2


def test_logs_search_endpoint_level_filter(tmp_path):
    logs.reset()
    logs.capture("m1", "ERROR boom", "err")     # id1 level=error
    logs.capture("m1", "error out", "out")      # id2 level=info(stream=out)
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/models/m1/logs/search?q=error&level=error")
    assert r.json()["matches"] == [1]


def test_logs_before_endpoint_pages_older(tmp_path):
    logs.reset()
    for i in range(10):
        logs.capture("m1", f"line{i}", "out")   # ids 1..10
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/models/m1/logs?before=6&limit=3")
    assert r.status_code == 200
    assert [ll["id"] for ll in r.json()] == [3, 4, 5]


def test_logs_unknown_alias_404(tmp_path):
    logs.reset()
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/models/nope/logs/search?q=x")
    assert r.status_code == 404
