from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from llm_manager.gateway.routes.health import router as health_router
from llm_manager.gateway.routes.models import router as models_router


def _app_with_container(container):
    app = FastAPI()
    app.state.container = container
    app.include_router(health_router)
    app.include_router(models_router)
    return app


def test_health_ok():
    app = _app_with_container(SimpleNamespace())
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}


def test_models_returns_openai_shape():
    from llm_manager.domain.model import Model, ModelMode

    fake_models = [
        Model(primary_name="qwen", aliases=frozenset({"qwen"}), mode=ModelMode.CHAT, port=1)
    ]
    container = SimpleNamespace(config=SimpleNamespace(), models=fake_models)
    import llm_manager.gateway.routes.models as m

    orig = m.catalog_domain_models
    m.catalog_domain_models = lambda cfg: fake_models
    try:
        app = _app_with_container(container)
        client = TestClient(app)
        data = client.get("/v1/models").json()
    finally:
        m.catalog_domain_models = orig
    assert data["object"] == "list"
    assert data["data"][0]["id"] == "qwen"
    assert data["data"][0]["object"] == "model"
