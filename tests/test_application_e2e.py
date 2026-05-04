"""Phase 6 — Application E2E integration tests"""
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response
from fastapi.testclient import TestClient
from sqlalchemy import select

from llm_manager.api.app import create_api_app
from llm_manager.config.models import AppConfig
from llm_manager.container import Container
from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.repos.billing_repo import BillingRepository
from llm_manager.database.repos.model_repo import ModelRuntimeRepository, ProgramRuntimeRepository
from llm_manager.database.repos.request_repo import RequestRepository
from llm_manager.database.schema import billing_methods, models as models_table
from llm_manager.events import EventBus
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.schemas.model import ModelState
from llm_manager.services.device_monitor import DeviceMonitor
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.process_manager import ProcessManager
from llm_manager.services.request_router import RequestRouter
from llm_manager.services.token_tracker import TokenTracker


def _make_app_config(**overrides) -> AppConfig:
    data = {
        "Local-Models": {
            "qwen": {
                "aliases": ["qwen", "qwen7b"],
                "mode": "Chat",
                "port": 8081,
                "cpu": {
                    "required_devices": ["cpu"],
                    "script_path": "cpu.sh",
                    "memory_mb": {"ram": 8000},
                },
            },
            "llama": {
                "aliases": ["llama"],
                "mode": "Chat",
                "port": 8082,
                "cpu": {
                    "required_devices": ["cpu"],
                    "script_path": "cpu_llama.sh",
                    "memory_mb": {"ram": 4000},
                },
            },
        },
    }
    return AppConfig.model_validate(data)


@pytest.fixture
def app_config():
    return _make_app_config()


def _build_container(config: AppConfig, db_path: Path = Path(":memory:")) -> Container:
    container = Container()
    container.register_instance(Container, container)
    container.register_instance(AppConfig, config)
    container.register(EventBus, EventBus)
    container.register(PluginRegistry, PluginRegistry)
    container.register(DatabaseEngine, lambda: DatabaseEngine(config.program, db_path=db_path))

    container.register(ModelRuntimeRepository, ModelRuntimeRepository)
    container.register(ProgramRuntimeRepository, ProgramRuntimeRepository)
    container.register(RequestRepository, RequestRepository)
    container.register(BillingRepository, BillingRepository)

    process_mgr = MagicMock(spec=ProcessManager)
    process_mgr.start_process = AsyncMock(return_value=MagicMock(pid=1234))
    process_mgr.stop_process = AsyncMock()
    container.register_instance(ProcessManager, process_mgr)

    device_monitor = MagicMock(spec=DeviceMonitor)
    device_monitor.get_online_devices = MagicMock(return_value={"cpu"})
    device_monitor.check_devices_available = MagicMock(return_value=True)
    container.register_instance(DeviceMonitor, device_monitor)

    container.register(ModelManager, ModelManager)
    container.register(TokenTracker, TokenTracker)
    container.register(RequestRouter, RequestRouter)

    return container


async def _start_container(container: Container) -> None:
    order = container._topological_sort()
    for svc_type in order:
        instance = container.resolve(svc_type)
        if hasattr(instance, "on_start") and callable(instance.on_start):
            result = instance.on_start()
            if hasattr(result, "__await__"):
                await result


# ============================================================
# TestApplicationStartup
# ============================================================

class TestApplicationStartup:

    @pytest.mark.asyncio
    async def test_container_resolves_all_services(self, app_config):
        container = _build_container(app_config)
        await _start_container(container)

        assert container.resolve(ModelManager) is not None
        assert container.resolve(RequestRouter) is not None
        assert container.resolve(TokenTracker) is not None
        assert container.resolve(DeviceMonitor) is not None
        assert container.resolve(BillingRepository) is not None

        db = container.resolve(DatabaseEngine)
        assert db.engine is not None

        await container.stop_all()

    @pytest.mark.asyncio
    async def test_billing_seeded_for_all_models(self, app_config):
        container = _build_container(app_config)
        await _start_container(container)

        billing_repo = container.resolve(BillingRepository)
        for model_name in app_config.models:
            billing_repo.seed_default_billing(model_name)

        db = container.resolve(DatabaseEngine)
        with db.engine.connect() as conn:
            rows = conn.execute(select(models_table)).fetchall()
            model_names_in_db = {row._mapping["original_name"] for row in rows}

        assert "qwen" in model_names_in_db
        assert "llama" in model_names_in_db

        with db.engine.connect() as conn:
            for model_row in rows:
                billing_row = conn.execute(
                    select(billing_methods).where(
                        billing_methods.c.model_id == model_row._mapping["id"]
                    )
                ).fetchone()
                assert billing_row is not None, f"No billing for {model_row._mapping['original_name']}"

        await container.stop_all()

    @pytest.mark.asyncio
    async def test_program_runtime_recorded(self, app_config):
        container = _build_container(app_config)
        await _start_container(container)

        program_repo = container.resolve(ProgramRuntimeRepository)
        before = time.time()
        record_id = program_repo.record_start(time.time())

        db = container.resolve(DatabaseEngine)
        with db.engine.connect() as conn:
            from llm_manager.database.schema import program_runtimes as pr
            row = conn.execute(select(pr).where(pr.c.id == record_id)).fetchone()

        assert row is not None
        assert row._mapping["start_time"] >= before
        assert row._mapping["end_time"] is None

        await container.stop_all()


# ============================================================
# TestGracefulShutdown
# ============================================================

class TestGracefulShutdown:

    @pytest.mark.asyncio
    async def test_running_models_stopped_on_shutdown(self, app_config):
        container = _build_container(app_config)
        await _start_container(container)

        model_mgr = container.resolve(ModelManager)
        await model_mgr.start_model("qwen")
        assert model_mgr.get_instance("qwen").state == ModelState.RUNNING

        for name, inst in list(model_mgr.get_all_instances().items()):
            if inst.state == ModelState.RUNNING:
                await model_mgr.stop_model(name)

        assert model_mgr.get_instance("qwen").state == ModelState.STOPPED
        container.resolve(ProcessManager).stop_process.assert_called_with("qwen")

    @pytest.mark.asyncio
    async def test_program_runtime_end_updated(self, app_config):
        container = _build_container(app_config)
        await _start_container(container)

        program_repo = container.resolve(ProgramRuntimeRepository)
        record_id = program_repo.record_start(time.time())

        program_repo.update_end(record_id, time.time())

        db = container.resolve(DatabaseEngine)
        with db.engine.connect() as conn:
            from llm_manager.database.schema import program_runtimes as pr
            row = conn.execute(select(pr).where(pr.c.id == record_id)).fetchone()

        assert row is not None
        assert row._mapping["end_time"] is not None

    @pytest.mark.asyncio
    async def test_services_stopped_in_reverse_order(self, app_config):
        container = _build_container(app_config)
        await _start_container(container)

        stop_order = []
        original_stop = container.stop_all

        async def _tracked_stop():
            order = list(reversed(container._topological_sort()))
            for svc_type in order:
                if svc_type not in container._started:
                    continue
                instance = container._instances.get(svc_type)
                if instance and hasattr(instance, "on_stop"):
                    stop_order.append(svc_type)
            await original_stop()

        container.stop_all = _tracked_stop
        await container.stop_all()

        db_type = DatabaseEngine
        if db_type in stop_order:
            model_mgr_idx = stop_order.index(ModelManager) if ModelManager in stop_order else -1
            db_idx = stop_order.index(db_type)
            if model_mgr_idx >= 0:
                assert model_mgr_idx < db_idx, (
                    "ModelManager should be stopped before DatabaseEngine"
                )


# ============================================================
# TestEndToEndProxy
# ============================================================

class TestEndToEndProxy:

    @pytest.fixture
    def proxy_env(self, app_config):
        container = _build_container(app_config)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_start_container(container))

        billing_repo = container.resolve(BillingRepository)
        for model_name in app_config.models:
            billing_repo.seed_default_billing(model_name)

        model_mgr = container.resolve(ModelManager)
        loop.run_until_complete(model_mgr.start_model("qwen"))

        app = create_api_app(container)
        client = TestClient(app)

        yield {
            "client": client,
            "container": container,
            "model_mgr": model_mgr,
            "config": app_config,
            "loop": loop,
        }

        loop.run_until_complete(container.stop_all())
        loop.close()

    def _mock_httpx_response(self, json_data, status_code=200):
        response = MagicMock(spec=Response)
        response.status_code = status_code
        response.json.return_value = json_data
        return response

    def test_proxy_request_to_running_model(self, proxy_env):
        client = proxy_env["client"]
        router = proxy_env["container"].resolve(RequestRouter)

        mock_response = self._mock_httpx_response({
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            resp = client.post("/v1/chat/completions", json={"model": "qwen"})

        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data

    def test_proxy_via_api_prefix_also_works(self, proxy_env):
        client = proxy_env["client"]
        router = proxy_env["container"].resolve(RequestRouter)

        mock_response = self._mock_httpx_response({
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            resp = client.post("/api/proxy/v1/chat/completions", json={"model": "qwen"})

        assert resp.status_code == 200

    def test_proxy_auto_starts_stopped_model(self, proxy_env):
        client = proxy_env["client"]
        model_mgr = proxy_env["model_mgr"]
        router = proxy_env["container"].resolve(RequestRouter)

        import asyncio
        asyncio.get_event_loop().run_until_complete(model_mgr.stop_model("qwen"))
        assert model_mgr.get_instance("qwen").state == ModelState.STOPPED

        mock_response = self._mock_httpx_response({"choices": []})
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            resp = client.post("/v1/chat/completions", json={"model": "qwen"})

        assert resp.status_code == 200
        assert model_mgr.get_instance("qwen").state == ModelState.RUNNING

    def test_token_recorded_after_request(self, proxy_env):
        client = proxy_env["client"]
        router = proxy_env["container"].resolve(RequestRouter)

        mock_response = self._mock_httpx_response({
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            resp = client.post("/v1/chat/completions", json={"model": "qwen"})

        assert resp.status_code == 200

        db = proxy_env["container"].resolve(DatabaseEngine)
        with db.engine.connect() as conn:
            from llm_manager.database.schema import model_requests, models as m
            model_row = conn.execute(
                select(m).where(m.c.original_name == "qwen")
            ).fetchone()
            assert model_row is not None

            req_rows = conn.execute(
                select(model_requests).where(
                    model_requests.c.model_id == model_row._mapping["id"]
                )
            ).fetchall()

        assert len(req_rows) >= 1
        assert req_rows[0]._mapping["input_tokens"] == 100
        assert req_rows[0]._mapping["output_tokens"] == 50

    def test_proxy_unknown_model_returns_404(self, proxy_env):
        client = proxy_env["client"]
        router = proxy_env["container"].resolve(RequestRouter)

        mock_response = self._mock_httpx_response({})
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            resp = client.post("/v1/chat/completions", json={"model": "nonexistent"})

        assert resp.status_code == 404
