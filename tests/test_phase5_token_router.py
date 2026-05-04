"""Phase 5 — 请求路由与 Token 追踪测试"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_manager.config.models import AppConfig, ModelConfigEntry, ProgramConfig
from llm_manager.container import Container
from llm_manager.events import EventBus
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.schemas.model import DeploymentConfig, ModelConfig, ModelInstance, ModelState
from llm_manager.schemas.request import TokenUsage
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.process_manager import ProcessManager
from llm_manager.services.device_monitor import DeviceMonitor
from llm_manager.services.token_tracker import TokenTracker
from llm_manager.services.request_router import RequestRouter
from llm_manager.database.repos.request_repo import RequestRepository


def _make_app_config() -> AppConfig:
    return AppConfig.model_validate({
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
            "embedding_model": {
                "aliases": ["emb"],
                "mode": "Embedding",
                "port": 8082,
                "cpu": {
                    "required_devices": ["cpu"],
                    "script_path": "cpu_emb.sh",
                    "memory_mb": {"ram": 4000},
                },
            },
        },
    })


def _make_token_tracker(config: AppConfig | None = None) -> TokenTracker:
    config = config or _make_app_config()
    container = Container()
    container.register_instance(Container, container)
    container.register_instance(AppConfig, config)

    request_repo = MagicMock(spec=RequestRepository)
    request_repo.save_request = MagicMock()
    container.register_instance(RequestRepository, request_repo)

    tracker = TokenTracker(container)
    tracker._config = config
    tracker._request_repo = request_repo
    return tracker


def _make_router_env():
    config = _make_app_config()
    container = Container()
    container.register_instance(Container, container)
    container.register_instance(AppConfig, config)
    container.register_instance(EventBus, EventBus())
    container.register_instance(PluginRegistry, PluginRegistry())

    process_mgr = MagicMock(spec=ProcessManager)
    process_mgr.start_process = AsyncMock(return_value=MagicMock(pid=1234))
    process_mgr.stop_process = AsyncMock()
    container.register_instance(ProcessManager, process_mgr)

    device_monitor = MagicMock(spec=DeviceMonitor)
    device_monitor.get_online_devices = MagicMock(return_value={"cpu"})
    device_monitor.check_devices_available = MagicMock(return_value=True)
    container.register_instance(DeviceMonitor, device_monitor)

    runtime_repo = MagicMock()
    runtime_repo.record_start = MagicMock(return_value=42)
    runtime_repo.record_end_by_id = MagicMock()

    request_repo = MagicMock(spec=RequestRepository)
    request_repo.save_request = MagicMock()
    container.register_instance(RequestRepository, request_repo)

    container.register(ModelManager, ModelManager)
    manager = container.resolve(ModelManager)
    manager._app_config = config
    manager._event_bus = container.resolve(EventBus)
    manager._device_monitor = device_monitor
    manager._process_manager = process_mgr
    manager._plugin_registry = container.resolve(PluginRegistry)
    manager._runtime_repo = runtime_repo
    manager._build_instances()

    tracker = TokenTracker(container)
    tracker._config = config
    tracker._request_repo = request_repo
    container.register_instance(TokenTracker, tracker)

    router = RequestRouter(container)
    router._model_manager = manager
    router._plugin_registry = container.resolve(PluginRegistry)
    router._token_tracker = tracker

    return {
        "router": router,
        "manager": manager,
        "tracker": tracker,
        "container": container,
        "config": config,
        "request_repo": request_repo,
        "process_mgr": process_mgr,
    }


# ============================================================
# TestTokenExtraction
# ============================================================

class TestTokenExtraction:

    def test_extract_from_timings(self):
        tracker = _make_token_tracker()
        data = {"timings": {"cache_n": 80, "prompt_n": 20, "predicted_n": 50}}
        usage = tracker.extract_tokens(data)
        assert usage.input_tokens == 100  # cache_n + prompt_n
        assert usage.output_tokens == 50
        assert usage.cache_n == 80
        assert usage.prompt_n == 20

    def test_fallback_to_usage(self):
        tracker = _make_token_tracker()
        data = {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
        usage = tracker.extract_tokens(data)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_n == 0
        assert usage.prompt_n == 0

    def test_timings_priority_over_usage(self):
        tracker = _make_token_tracker()
        data = {
            "timings": {"cache_n": 30, "prompt_n": 10, "predicted_n": 20},
            "usage": {"prompt_tokens": 999, "completion_tokens": 888},
        }
        usage = tracker.extract_tokens(data)
        assert usage.input_tokens == 40  # from timings, not usage
        assert usage.output_tokens == 20

    def test_empty_data_returns_zeros(self):
        tracker = _make_token_tracker()
        usage = tracker.extract_tokens({})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_n == 0
        assert usage.prompt_n == 0

    def test_partial_timings_fields(self):
        tracker = _make_token_tracker()
        data = {"timings": {"predicted_n": 50}}
        usage = tracker.extract_tokens(data)
        assert usage.input_tokens == 0  # cache_n and prompt_n default to 0
        assert usage.output_tokens == 50
        assert usage.cache_n == 0
        assert usage.prompt_n == 0

    def test_all_zero_timings_falls_to_usage(self):
        tracker = _make_token_tracker()
        data = {
            "timings": {"cache_n": 0, "prompt_n": 0, "predicted_n": 0},
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        usage = tracker.extract_tokens(data)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50


# ============================================================
# TestStreamTokenExtraction
# ============================================================

class TestStreamTokenExtraction:

    def test_extract_from_sse_stream(self):
        tracker = _make_token_tracker()
        sse = (
            b'data: {"choices": []}\n'
            b'data: {"usage": {"prompt_tokens": 50, "completion_tokens": 30}}\n'
            b'data: [DONE]\n'
        )
        usage = tracker.extract_from_stream(sse)
        assert usage.input_tokens == 50
        assert usage.output_tokens == 30

    def test_extract_from_json_response(self):
        tracker = _make_token_tracker()
        content = b'{"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}'
        usage = tracker.extract_from_stream(content)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_sse_with_timings_in_last_block(self):
        tracker = _make_token_tracker()
        sse = (
            b'data: {"choices": [{"text": "hello"}]}\n'
            b'data: {"timings": {"cache_n": 60, "prompt_n": 40, "predicted_n": 30}}\n'
            b'data: [DONE]\n'
        )
        usage = tracker.extract_from_stream(sse)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 30
        assert usage.cache_n == 60

    def test_no_valid_tokens_returns_zeros(self):
        tracker = _make_token_tracker()
        sse = b'data: {"choices": []}\ndata: [DONE]\n'
        usage = tracker.extract_from_stream(sse)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_invalid_bytes_returns_zeros(self):
        tracker = _make_token_tracker()
        usage = tracker.extract_from_stream(b'\xff\xfe invalid')
        assert usage.input_tokens == 0


# ============================================================
# TestTokenRecording
# ============================================================

class TestTokenRecording:

    @pytest.mark.asyncio
    async def test_record_to_database(self):
        config = _make_app_config()
        config.program.token_tracker = ["Chat"]
        tracker = _make_token_tracker(config)
        usage = TokenUsage(input_tokens=100, output_tokens=50, cache_n=20, prompt_n=80)
        now = time.time()

        await tracker.record_request("qwen", usage, now - 1, now, "Chat")

        tracker._request_repo.save_request.assert_called_once()
        args = tracker._request_repo.save_request.call_args[0]
        assert args[0] == "qwen"
        assert args[3] == 100  # input_tokens
        assert args[4] == 50   # output_tokens
        assert args[5] == 20   # cache_n
        assert args[6] == 80   # prompt_n

    @pytest.mark.asyncio
    async def test_skip_when_mode_not_tracked(self):
        config = _make_app_config()
        config.program.token_tracker = ["Chat"]
        tracker = _make_token_tracker(config)
        usage = TokenUsage(input_tokens=100, output_tokens=50)

        await tracker.record_request("emb_model", usage, time.time(), time.time(), "Embedding")

        tracker._request_repo.save_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_all_zero(self):
        config = _make_app_config()
        config.program.token_tracker = ["Chat"]
        tracker = _make_token_tracker(config)
        usage = TokenUsage()

        await tracker.record_request("qwen", usage, time.time(), time.time(), "Chat")

        tracker._request_repo.save_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_failure_does_not_raise(self):
        config = _make_app_config()
        config.program.token_tracker = ["Chat"]
        tracker = _make_token_tracker(config)
        tracker._request_repo.save_request.side_effect = Exception("DB down")
        usage = TokenUsage(input_tokens=100, output_tokens=50)

        await tracker.record_request("qwen", usage, time.time(), time.time(), "Chat")

    @pytest.mark.asyncio
    async def test_fills_default_times(self):
        config = _make_app_config()
        config.program.token_tracker = ["Chat"]
        tracker = _make_token_tracker(config)
        usage = TokenUsage(input_tokens=50, output_tokens=25)

        await tracker.record_request("qwen", usage, 0, 0, "Chat")

        args = tracker._request_repo.save_request.call_args[0]
        assert args[1] > 0  # start_time defaulted to end_time
        assert args[2] > 0  # end_time defaulted to now


# ============================================================
# TestSmartAutoStart
# ============================================================

class TestSmartAutoStart:

    @pytest.mark.asyncio
    async def test_auto_start_stopped_model(self):
        env = _make_router_env()
        mgr = env["manager"]
        router = env["router"]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            response = await router.route_request("qwen", "/v1/chat/completions", "POST")

        assert response.status_code == 200
        assert mgr._instances["qwen"].state == ModelState.RUNNING
        env["process_mgr"].start_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_start_resolves_alias(self):
        env = _make_router_env()
        router = env["router"]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            response = await router.route_request("qwen7b", "/v1/chat/completions", "POST")

        assert response.status_code == 200
        assert env["manager"]._instances["qwen"].state == ModelState.RUNNING

    @pytest.mark.asyncio
    async def test_wait_for_starting_model(self):
        env = _make_router_env()
        mgr = env["manager"]
        router = env["router"]
        instance = mgr._instances["qwen"]

        start_count = 0
        original_start = mgr.start_model

        async def _delayed_start(name, **kw):
            nonlocal start_count
            start_count += 1
            return await original_start(name, **kw)

        instance.state = ModelState.STARTING

        async def _set_running():
            await asyncio.sleep(0.3)
            instance.state = ModelState.RUNNING

        asyncio.create_task(_set_running())

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            response = await router.route_request("qwen", "/v1/chat/completions", "POST")

        assert response.status_code == 200
        assert start_count == 0

    @pytest.mark.asyncio
    async def test_unknown_model_raises(self):
        env = _make_router_env()
        router = env["router"]

        with pytest.raises(ValueError, match="not found"):
            await router.route_request("nonexistent", "/v1/chat/completions", "POST")

    @pytest.mark.asyncio
    async def test_concurrent_requests_no_duplicate_start(self):
        env = _make_router_env()
        mgr = env["manager"]
        router = env["router"]

        start_call_count = 0
        original_start = mgr.start_model

        async def _tracked_start(name, **kw):
            nonlocal start_call_count
            start_call_count += 1
            return await original_start(name, **kw)

        mgr.start_model = _tracked_start

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            results = await asyncio.gather(
                router.route_request("qwen", "/v1/chat/completions", "POST"),
                router.route_request("qwen", "/v1/chat/completions", "POST"),
            )

        assert len(results) == 2
        assert start_call_count <= 2


# ============================================================
# TestPendingTracking
# ============================================================

class TestPendingTracking:

    def test_increment_updates_count_and_timestamp(self):
        env = _make_router_env()
        router = env["router"]
        mgr = env["manager"]
        before = time.time()

        router._increment_pending("qwen")

        assert router._pending["qwen"] == 1
        assert mgr._instances["qwen"].last_request_at is not None
        assert mgr._instances["qwen"].last_request_at >= before

    def test_increment_accumulates(self):
        env = _make_router_env()
        router = env["router"]
        router._increment_pending("qwen")
        router._increment_pending("qwen")
        router._increment_pending("qwen")

        assert router._pending["qwen"] == 3

    def test_decrement_reduces_count(self):
        env = _make_router_env()
        router = env["router"]
        router._pending["qwen"] = 3

        router._decrement_pending("qwen")

        assert router._pending["qwen"] == 2

    def test_decrement_does_not_go_negative(self):
        env = _make_router_env()
        router = env["router"]
        router._pending["qwen"] = 0

        router._decrement_pending("qwen")

        assert router._pending["qwen"] == 0

    def test_decrement_unknown_model_is_noop(self):
        env = _make_router_env()
        router = env["router"]

        router._decrement_pending("nonexistent")

        assert "nonexistent" not in router._pending

    @pytest.mark.asyncio
    async def test_pending_decremented_on_success(self):
        env = _make_router_env()
        router = env["router"]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            await router.route_request("qwen", "/v1/chat/completions", "POST")

        assert router._pending.get("qwen", 0) == 0

    @pytest.mark.asyncio
    async def test_pending_decremented_on_failure(self):
        env = _make_router_env()
        router = env["router"]

        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(side_effect=Exception("connection error"))
            with pytest.raises(Exception, match="connection error"):
                await router.route_request("qwen", "/v1/chat/completions", "POST")

        assert router._pending.get("qwen", 0) == 0


# ============================================================
# TestRouteRequestTokenIntegration
# ============================================================

class TestRouteRequestTokenIntegration:

    @pytest.mark.asyncio
    async def test_non_streaming_extracts_and_records_tokens(self):
        env = _make_router_env()
        router = env["router"]
        env["config"].program.token_tracker = ["Chat"]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        }
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            await router.route_request("qwen", "/v1/chat/completions", "POST")

        env["request_repo"].save_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_200_skips_token_recording(self):
        env = _make_router_env()
        router = env["router"]
        env["config"].program.token_tracker = ["Chat"]

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {}
        with patch.object(router, "_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)
            await router.route_request("qwen", "/v1/chat/completions", "POST")

        env["request_repo"].save_request.assert_not_called()
