"""GET /api/logs/* — persistent session logs (system + model) over SQLite.

Covers: sessions list (type/model filters, before pagination), per-session line
paging (level filter / before), SSE live stream (DB backfill + broadcaster tail),
cross-session text search, deleted-model alias resolution from session history,
unknown-alias 404, unknown-session 404.

SSE 测试直接驱动 _session_stream 生成器:starlette TestClient 与 httpx ASGITransport
都会 await app(...) 到 ASGI 应用跑完才返回 —— 无限 SSE 流永不结束,任何客户端
传输层都会死锁。生成器单循环测试覆盖真实逻辑(DB 回填 + 广播实时行);HTTP 层
路由/404 由 test_session_404 经同步 TestClient 覆盖。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import cfg as build_cfg
from helpers import model as build_model
from helpers import scheme as build_scheme

from llm_manager.data import logs as _logs
from llm_manager.data.config_store import ConfigStore, write_appconfig
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.logs import _session_stream
from llm_manager.gateway.routes import register_routes
from llm_manager.state import ModelStatus


class _NoLife:
    async def ensure_running(self, alias, *, inc_pending=False):
        return ModelStatus.STOPPED

    async def stop(self, alias):
        return ModelStatus.STOPPED


def _build(tmp_path):
    """App + DB + 两个会话。system 会话持久化直建(无广播器,SSE 不测系统);
    model 会话走 logs 管线建(登记广播器,SSE 实时推可测)。"""
    db = open_db(tmp_path / "t.db")
    write_appconfig(
        db,
        build_cfg(
            models={
                "m1": build_model(
                    ("m1a",),
                    8001,
                    schemes={
                        "RTX4060": build_scheme(devices=("rtx 4060",), memory_mb={"rtx 4060": 2048})
                    },
                )
            }
        ),
    )
    store = ConfigStore(db)
    app = FastAPI()
    register_routes(app, _NoLife(), db, {})
    app.state.config_store = store
    app.state.db = db
    _logs.reset()
    _logs.init(db)
    sid_sys = _logs.log_start_session(db, "system", None, None, 1000.0)
    _logs.log_insert_lines(
        db, sid_sys, [(1, 1000.1, "sys", "info", "boot"), (2, 1000.2, "sys", "error", "boom")]
    )
    _logs.log_end_session(db, sid_sys, 1500.0)
    sid_m = _logs.start_session("model", "m1", "m1a", 2000.0)
    return app, db, sid_sys, sid_m


@pytest.fixture
def client(tmp_path):
    app, db, sid_sys, sid_m = _build(tmp_path)
    with TestClient(app) as c:
        yield c, db, sid_sys, sid_m


def test_sessions_list(client):
    c, _db, sid_sys, sid_m = client
    r = c.get("/api/logs/sessions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["id"] == sid_m and data[0]["type"] == "model"
    assert data[0]["alias"] == "m1a"
    assert data[1]["line_count"] == 2 and data[1]["status"] == "ended"
    r2 = c.get("/api/logs/sessions?type=system")
    assert [d["id"] for d in r2.json()] == [sid_sys]
    r3 = c.get("/api/logs/sessions?type=model&model=m1a")
    assert [d["id"] for d in r3.json()] == [sid_m]


def test_running_session_status_survives_heartbeat(client):
    """心跳把运行中会话的 end_time 写成非 NULL(每 30s),status 仍应为 running。

    回归 7279319 解耦语义:运行中由内存 live 集合判定,end_time 只管时间——
    API 响应层的 status 必须用 SQL 算好的 status 字段,不能回退到 end_time 判断
    (否则心跳一写 end_time,日志页「运行中」就消失)。"""
    c, db, _sid_sys, sid_m = client
    _logs.log_heartbeat_live(db, 2500.0)  # 模拟一次心跳:live 会话 end_time 被推到现在
    r = c.get("/api/logs/sessions")
    by_id = {d["id"]: d for d in r.json()}
    assert by_id[sid_m]["end_time"] == 2500.0  # 心跳确实写了 end_time
    assert by_id[sid_m]["status"] == "running"  # 但在内存 live 集合 → 仍运行中
    assert by_id[sid_m]["duration_s"] is None  # 运行中不展示时长


def test_sessions_before_pagination(client):
    c, _db, sid_sys, _sid_m = client
    r = c.get("/api/logs/sessions?before=2")
    assert [d["id"] for d in r.json()] == [sid_sys]  # id < 2


def test_session_lines_and_level(client):
    c, _db, sid_sys, _sid_m = client
    r = c.get(f"/api/logs/sessions/{sid_sys}/lines")
    assert [d["text"] for d in r.json()] == ["boot", "boom"]
    r2 = c.get(f"/api/logs/sessions/{sid_sys}/lines?level=error")
    assert [d["text"] for d in r2.json()] == ["boom"]
    r3 = c.get(f"/api/logs/sessions/{sid_sys}/lines?before=2&limit=1")
    assert [d["text"] for d in r3.json()] == ["boot"]


def test_session_404(client):
    c, _db, _sid_sys, _sid_m = client
    assert c.get("/api/logs/sessions/9999/lines").status_code == 404
    assert c.get("/api/logs/sessions/9999/stream").status_code == 404


def test_logs_search(client):
    c, _db, sid_sys, _sid_m = client
    r = c.get("/api/logs/search?q=BOOM")
    data = r.json()
    assert data["total"] == 1
    assert data["matches"][0]["session_id"] == sid_sys
    assert data["matches"][0]["line"]["text"] == "boom"
    r2 = c.get("/api/logs/search?q=boom&type=model")
    assert r2.json()["total"] == 0
    r3 = c.get(f"/api/logs/search?q=boom&session_id={sid_sys}")
    assert r3.json()["total"] == 1
    r4 = c.get("/api/logs/search?q=boom&model=m1a")
    assert r4.json()["total"] == 0


def test_search_total_is_true_count_uncut_by_limit(client):
    """真 total:limit 截断行数但 total 是满足条件的全部匹配数。"""
    c, db, sid_sys, _sid_m = client
    for i in range(600):
        _logs.log_insert_lines(db, sid_sys, [(100 + i, 1000.0 + i, "sys", "info", f"x {i}")])
    r = c.get("/api/logs/search?q=x&limit=500")
    j = r.json()
    assert j["total"] == 600 and len(j["matches"]) == 500


def test_search_invalid_level_422(client):
    c, _db, sid_sys, _sid_m = client
    assert c.get("/api/logs/search?q=x&level=bogus").status_code == 422
    assert c.get("/api/logs/sessions?type=bogus").status_code == 422
    assert c.get(f"/api/logs/sessions/{sid_sys}/lines?level=bogus").status_code == 422


def test_unknown_model_alias_404(client):
    c, _db, _sid_sys, _sid_m = client
    # 真正未知的 alias(配置与会话历史都无)→ 仍 404
    assert c.get("/api/logs/sessions?model=totally_unknown").status_code == 404


def test_deleted_model_alias_resolves_from_session_history(client):
    """已删模型的残留会话:alias/model_name 命中会话历史 → 过滤出该模型会话(不再 404)。

    §8 承诺模型下拉含"已删除模型的残留会话";删模型后 config 无此 alias,
    需回退到 log_sessions 历史按 alias/原名解析。"""
    c, db, _sid_sys, _sid_m = client
    sid_del = _logs.log_start_session(db, "model", "deleted_model", "gone", 3000.0)
    _logs.log_insert_lines(db, sid_del, [(1, 3000.1, "model", "info", "residual")])
    _logs.log_end_session(db, sid_del, 3500.0)
    # 按历史 alias 过滤
    r = c.get("/api/logs/sessions?type=model&model=gone")
    assert r.status_code == 200
    assert [d["id"] for d in r.json()] == [sid_del]
    # 按历史 model_name 过滤同样命中
    r2 = c.get("/api/logs/sessions?type=model&model=deleted_model")
    assert [d["id"] for d in r2.json()] == [sid_del]
    # 搜索端点共享同一解析逻辑
    r3 = c.get("/api/logs/search?q=residual&model=gone")
    assert r3.json()["total"] == 1


def test_session_stream_backfill_and_live(client):
    _c, db, _sid_sys, sid_m = client
    # 回填行经 capture+flush 落库 —— 直插 seq=1 会与 capture 的 next_seq 冲突
    # (UNIQUE(session_id, seq));此刻无订阅者,广播丢弃,仅落库。
    _logs.capture("m1", "listening", "out")
    asyncio.run(_logs.flush())

    async def go():
        out = []
        q = _logs.subscribe(sid_m)  # 端点里的存在性检查同款
        gen = _session_stream(sid_m, None, db, q)
        try:
            frame = await anext(gen)  # 回填最近行(DB)
            out.append(json.loads(frame.removeprefix("data: ").strip()))
            _logs.capture("m1", "live line", "out")
            await _logs.flush()  # 落库 + 广播(同一循环)
            frame = await anext(gen)  # 实时行经广播
            out.append(json.loads(frame.removeprefix("data: ").strip()))
        finally:
            await gen.aclose()  # finally → unsubscribe
        return out

    res = asyncio.run(go())
    assert [ll["text"] for ll in res] == ["listening", "live line"]


def test_sessions_orphan_ended_null_end_time_no_crash(client):
    """孤儿会话(status=ended 且 end_time NULL,崩溃残留)不崩,duration_s=None。"""
    c, db, _sid_sys, _sid_m = client

    # 直插孤儿行:ended(不在 live 集)+ end_time NULL
    with db.write_lock:
        cur = db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time, end_time) "
            "VALUES ('model', 'm', 'm', 1000.0, NULL)"
        )
        db.conn.commit()
        sid = cur.lastrowid
    resp = c.get("/api/logs/sessions")
    assert resp.status_code == 200
    rows = [r for r in resp.json() if r["id"] == sid]
    assert len(rows) == 1
    assert rows[0]["status"] == "ended"
    assert rows[0]["end_time"] is None
    assert rows[0]["duration_s"] is None  # 修复前:TypeError 杀进程
