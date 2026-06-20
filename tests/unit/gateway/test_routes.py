from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager.gateway.routes import register_routes


def test_health_returns_200():
    app = FastAPI()
    register_routes(app)
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
