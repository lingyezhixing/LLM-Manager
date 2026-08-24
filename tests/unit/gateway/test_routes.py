from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import cfg as build_cfg
from helpers import model as build_model
from helpers import scheme as build_scheme

from llm_manager import config
from llm_manager.data.config_store import ConfigStore, write_appconfig
from llm_manager.data.persistence import open_db
from llm_manager.gateway.routes import register_routes
from llm_manager.state import ModelStatus


def _cfg() -> config.AppConfig:
    return build_cfg(
        models={
            "m1": build_model(
                ("m1",),
                8000,
                schemes={
                    "RTX4060": build_scheme(devices=("rtx 4060",), memory_mb={"rtx 4060": 2048})
                },
            )
        }
    )


def _cfg_distinct() -> config.AppConfig:
    # 契约:internal-qwen-key(内部键,不应外露)→ aliases[0]=qwen2.5-32b-instruct(对外身份)
    return build_cfg(
        models={
            "internal-qwen-key": build_model(
                ("qwen2.5-32b-instruct",),
                8001,
                schemes={
                    "RTX4060": build_scheme(devices=("rtx 4060",), memory_mb={"rtx 4060": 2048})
                },
            )
        }
    )


class _FakeLife:
    async def ensure_running(self, alias, *, inc_pending=False):
        return ModelStatus.ROUTING


def _register(app, cfg, client_pool=None):
    db = open_db(Path(":memory:"))
    write_appconfig(db, cfg)
    store = ConfigStore(db)
    register_routes(app, _FakeLife(), db, client_pool or {})
    app.state.config_store = store
    app.state.db = db


def test_health_returns_200():
    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_v1_models_returns_catalog():
    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        r = c.get("/v1/models")
    assert r.status_code == 200 and "m1" in {m["id"] for m in r.json()["data"]}


def test_favicon_svg_served_with_explicit_mime(tmp_path, monkeypatch):
    """契约(fix):favicon 图标随 public/ 拷入 dist,由 SPA 兜底 serve,但必须带
    image/svg+xml——Windows 上 mimetypes 把 .svg 猜成 image/svg,标准 Chromium
    会拒绝解码(favicon 不入库,实测结论)。"""
    import llm_manager.gateway.routes as routes_mod

    fake_dist = tmp_path / "dist"
    (fake_dist / "assets").mkdir(parents=True)
    (fake_dist / "favicon.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8"
    )
    (fake_dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    monkeypatch.setattr(routes_mod, "_FRONTEND_DIST", fake_dist)

    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        r = c.get("/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in r.content


def test_v1_models_lists_primary_alias_not_internal_key():
    """契约(fix):/v1/models 的 id 必须是 aliases[0](主别名 = 下游 served name = 客户端调用名),
    而非 primary_name(仅内部区分用的配置键,不应外露)。"""
    cfg = _cfg_distinct()
    app = FastAPI()
    _register(app, cfg)
    with TestClient(app) as c:
        r = c.get("/v1/models")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert "qwen2.5-32b-instruct" in ids  # 主别名对外
    assert "internal-qwen-key" not in ids  # 内部键不外露


def test_options_preflight_returns_204_with_cors():
    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        r = c.options("/v1/chat/completions")
    assert r.status_code == 204 and r.headers.get("access-control-allow-origin") == "*"


def test_non_get_catchall_forwards_to_proxy():
    # catch_all 不再 501;MockTransport 强制 ConnectError → 502(隔离,不依赖真实端口占用)
    def fail_handler(req):
        raise httpx.ConnectError("no upstream (test)", request=req)

    app = FastAPI()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(fail_handler)
    )
    _register(app, _cfg(), client_pool={8000: client})
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={"model": "m1"})
    assert r.status_code == 502


def test_spa_served_and_api_unaffected_when_dist_exists(tmp_path, monkeypatch):
    """StaticFiles+SPA fallback:GET / → index.html;既有 /health、/api/config/models 不受影响;
    未命中 GET 路径回退 index.html(SPA 前端路由)。"""
    import llm_manager.gateway.routes as routes_mod

    fake_dist = tmp_path / "dist"
    (fake_dist / "assets").mkdir(parents=True)
    (fake_dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (fake_dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    monkeypatch.setattr(routes_mod, "_FRONTEND_DIST", fake_dist)

    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        assert c.get("/").status_code == 200  # index.html
        assert "SPA" in c.get("/").text
        assert c.get("/health").status_code == 200  # 既有路由仍在
        assert c.get("/api/config/models").status_code == 200  # 管理接口仍在
        assert c.get("/models").status_code == 200  # SPA 路由回退 index.html
        assert c.get("/assets/app.js").status_code == 200  # 静态资源


def test_spa_rejects_path_traversal(tmp_path, monkeypatch):
    """路径穿越防御:GET /%2e%2e/... 必须返回 404,不能读 dist 外的文件。"""
    import llm_manager.gateway.routes as routes_mod

    fake_dist = tmp_path / "dist"
    (fake_dist / "assets").mkdir(parents=True)
    (fake_dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    secret = tmp_path / "config.yaml"  # dist 的同级文件(dist/../config.yaml)
    secret.write_text("LEAKED-SECRET", encoding="utf-8")
    monkeypatch.setattr(routes_mod, "_FRONTEND_DIST", fake_dist)
    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        for url in ["/%2e%2e/config.yaml", "/..%2fconfig.yaml", "/%2e%2e/%2e%2e/config.yaml"]:
            r = c.get(url, follow_redirects=False)
            assert r.status_code == 404, url
            assert "LEAKED-SECRET" not in r.text


def test_spa_boots_when_dist_lacks_assets(tmp_path, monkeypatch):
    """dist 存在但无 assets/ 子目录时,网关仍能启动且 GET / 返回 index.html。"""
    import llm_manager.gateway.routes as routes_mod

    fake_dist = tmp_path / "dist"
    fake_dist.mkdir(parents=True)
    (fake_dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")  # 注意:无 assets/
    monkeypatch.setattr(routes_mod, "_FRONTEND_DIST", fake_dist)
    app = FastAPI()
    _register(app, _cfg())  # 不应抛 RuntimeError
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/health").status_code == 200


def test_v1_models_reflects_store_reload():
    """读穿:store.reload() 后 /v1/models 反映新模型,无需重启/重注册。"""
    from dataclasses import replace

    app = FastAPI()
    _register(app, _cfg())
    with TestClient(app) as c:
        m2 = config.ModelConfig(
            aliases=("m2",),
            mode="Chat",
            port=8002,
            schemes={
                "s": config.Scheme("s", frozenset({"rtx 4060"}), config.Command(exe="q.bat"), {})
            },
        )
        cur = app.state.config_store.snapshot()
        write_appconfig(app.state.db, replace(cur, models={**cur.models, "m2": m2}))
        app.state.config_store.reload()
        r = c.get("/v1/models")
    ids = {m["id"] for m in r.json()["data"]}
    assert "m1" in ids and "m2" in ids
