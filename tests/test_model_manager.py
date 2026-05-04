"""Phase 4 — Model Manager tests"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_manager.config.models import AppConfig, ModelConfigEntry, ProgramConfig
from llm_manager.container import Container
from llm_manager.events import EventBus
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.schemas.device import DeviceState, DeviceStatus
from llm_manager.schemas.model import ModelInstance, ModelState
from llm_manager.services.device_monitor import DeviceMonitor
from llm_manager.services.idle_monitor import IdleMonitor
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.process_manager import ProcessManager


def _make_app_config() -> AppConfig:
    return AppConfig.model_validate({
        "Local-Models": {
            "qwen": {
                "aliases": ["qwen", "qwen7b"],
                "mode": "Chat",
                "port": 8081,
                "rtx_4060": {
                    "required_devices": ["rtx_4060"],
                    "script_path": "gpu.sh",
                    "memory_mb": {"vram": 6000},
                },
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
        }
    })


def _mock_process_info(pid=1234):
    info = MagicMock()
    info.pid = pid
    return info


@pytest.fixture
def manager_env():
    """Create a ModelManager with mocked dependencies."""
    config = _make_app_config()
    container = Container()
    container.register_instance(Container, container)
    container.register_instance(AppConfig, config)
    container.register_instance(EventBus, EventBus())
    container.register_instance(PluginRegistry, PluginRegistry())

    process_mgr = MagicMock(spec=ProcessManager)
    process_mgr.start_process = AsyncMock(return_value=_mock_process_info())
    process_mgr.stop_process = AsyncMock()
    container.register_instance(ProcessManager, process_mgr)

    device_monitor = MagicMock(spec=DeviceMonitor)
    device_monitor.get_online_devices = MagicMock(return_value={"rtx_4060", "cpu"})
    device_monitor.check_devices_available = MagicMock(return_value=True)
    container.register_instance(DeviceMonitor, device_monitor)

    runtime_repo = MagicMock()
    runtime_repo.record_start = MagicMock(return_value=42)
    runtime_repo.record_end_by_id = MagicMock()
    container.register_instance(MagicMock, runtime_repo)

    container.register(ModelManager, ModelManager)
    manager = container.resolve(ModelManager)

    container.start_all_sync = lambda: None

    manager._app_config = config
    manager._event_bus = container.resolve(EventBus)
    manager._device_monitor = device_monitor
    manager._process_manager = process_mgr
    manager._plugin_registry = container.resolve(PluginRegistry)
    manager._runtime_repo = runtime_repo
    manager._build_instances()

    return {
        "manager": manager,
        "container": container,
        "config": config,
        "process_mgr": process_mgr,
        "device_monitor": device_monitor,
        "runtime_repo": runtime_repo,
    }


class TestAdaptiveDeployment:

    @pytest.mark.asyncio
    async def test_selects_gpu_when_available(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["device_monitor"].get_online_devices.return_value = {"rtx_4060", "cpu"}

        instance = await mgr.start_model("qwen")

        assert instance.active_deployment == "rtx_4060"
        manager_env["process_mgr"].start_process.assert_called_once_with(
            name="qwen", script_path="gpu.sh",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_cpu_when_gpu_offline(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["device_monitor"].get_online_devices.return_value = {"cpu"}

        instance = await mgr.start_model("qwen")

        assert instance.active_deployment == "cpu"
        call_args = manager_env["process_mgr"].start_process.call_args
        assert "cpu.sh" in str(call_args)

    @pytest.mark.asyncio
    async def test_raises_when_no_deployment_matches(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["device_monitor"].get_online_devices.return_value = {"amd_780m"}

        with pytest.raises(RuntimeError, match="No compatible deployment"):
            await mgr.start_model("qwen")

    @pytest.mark.asyncio
    async def test_manual_override_uses_specified_deployment(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["device_monitor"].check_devices_available.return_value = True

        instance = await mgr.start_model("qwen", deployment_name="cpu")

        assert instance.active_deployment == "cpu"

    @pytest.mark.asyncio
    async def test_manual_override_unknown_deployment_raises(self, manager_env):
        mgr = manager_env["manager"]

        with pytest.raises(ValueError, match="Deployment 'nonexistent' not found"):
            await mgr.start_model("qwen", deployment_name="nonexistent")


class TestRuntimeRecording:

    @pytest.mark.asyncio
    async def test_start_records_runtime(self, manager_env):
        mgr = manager_env["manager"]
        runtime_repo = manager_env["runtime_repo"]

        instance = await mgr.start_model("qwen")

        assert instance.runtime_record_id == 42
        runtime_repo.record_start.assert_called_once()
        args = runtime_repo.record_start.call_args[0]
        assert args[0] == "qwen"

    @pytest.mark.asyncio
    async def test_stop_updates_runtime(self, manager_env):
        mgr = manager_env["manager"]
        runtime_repo = manager_env["runtime_repo"]

        await mgr.start_model("qwen")
        await mgr.stop_model("qwen")

        runtime_repo.record_end_by_id.assert_called_once()
        args = runtime_repo.record_end_by_id.call_args[0]
        assert args[0] == 42

    @pytest.mark.asyncio
    async def test_start_recording_failure_does_not_prevent_startup(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["runtime_repo"].record_start.side_effect = Exception("DB down")

        instance = await mgr.start_model("qwen")

        assert instance.state == ModelState.RUNNING
        assert instance.runtime_record_id is None

    @pytest.mark.asyncio
    async def test_stop_recording_failure_does_not_prevent_shutdown(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["runtime_repo"].record_end_by_id.side_effect = Exception("DB down")

        await mgr.start_model("qwen")
        instance = await mgr.stop_model("qwen")

        assert instance.state == ModelState.STOPPED
        assert instance.runtime_record_id is None


class TestStateTransitions:

    @pytest.mark.asyncio
    async def test_double_start_returns_existing_instance(self, manager_env):
        mgr = manager_env["manager"]

        first = await mgr.start_model("qwen")
        second = await mgr.start_model("qwen")

        assert first is second
        assert second.state == ModelState.RUNNING

    @pytest.mark.asyncio
    async def test_start_while_starting_raises(self, manager_env):
        mgr = manager_env["manager"]
        mgr._instances["qwen"].state = ModelState.STARTING

        with pytest.raises(RuntimeError, match="already starting"):
            await mgr.start_model("qwen")

    @pytest.mark.asyncio
    async def test_start_failure_sets_failed(self, manager_env):
        mgr = manager_env["manager"]
        manager_env["process_mgr"].start_process.side_effect = Exception("process error")

        with pytest.raises(Exception, match="process error"):
            await mgr.start_model("qwen")

        assert mgr._instances["qwen"].state == ModelState.FAILED

    @pytest.mark.asyncio
    async def test_stop_non_running_is_idempotent(self, manager_env):
        mgr = manager_env["manager"]
        instance = mgr._instances["qwen"]
        assert instance.state == ModelState.STOPPED

        result = await mgr.stop_model("qwen")
        assert result.state == ModelState.STOPPED
        manager_env["process_mgr"].stop_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_clears_fields(self, manager_env):
        mgr = manager_env["manager"]

        await mgr.start_model("qwen")
        assert mgr._instances["qwen"].pid is not None
        assert mgr._instances["qwen"].started_at is not None

        instance = await mgr.stop_model("qwen")
        assert instance.pid is None
        assert instance.active_deployment is None
        assert instance.started_at is None
        assert instance.last_request_at is None
        assert instance.runtime_record_id is None


class TestIdleMonitor:

    @pytest.mark.asyncio
    async def test_auto_stops_idle_model(self, manager_env):
        mgr = manager_env["manager"]
        config = manager_env["config"]
        config.program.alive_time = 1

        await mgr.start_model("qwen")
        mgr._instances["qwen"].last_request_at = time.time() - 120

        monitor = IdleMonitor(manager_env["container"])
        monitor._running = True
        await monitor._check_idle_models()

        assert mgr._instances["qwen"].state == ModelState.STOPPED

    @pytest.mark.asyncio
    async def test_skips_recently_active_model(self, manager_env):
        mgr = manager_env["manager"]
        config = manager_env["config"]
        config.program.alive_time = 60

        await mgr.start_model("qwen")
        mgr._instances["qwen"].last_request_at = time.time() - 30

        monitor = IdleMonitor(manager_env["container"])
        monitor._running = True
        await monitor._check_idle_models()

        assert mgr._instances["qwen"].state == ModelState.RUNNING

    @pytest.mark.asyncio
    async def test_skips_model_with_no_requests(self, manager_env):
        mgr = manager_env["manager"]
        config = manager_env["config"]
        config.program.alive_time = 1

        await mgr.start_model("qwen")
        assert mgr._instances["qwen"].last_request_at is None

        monitor = IdleMonitor(manager_env["container"])
        monitor._running = True
        await monitor._check_idle_models()

        assert mgr._instances["qwen"].state == ModelState.RUNNING

    @pytest.mark.asyncio
    async def test_disabled_when_alive_time_zero(self):
        container = Container()
        config = _make_app_config()
        config.program.alive_time = 0
        container.register_instance(Container, container)
        container.register_instance(AppConfig, config)
        container.register(ModelManager, ModelManager)

        monitor = IdleMonitor(container)
        await monitor.on_start()

        assert monitor._task is None
        assert monitor._running is False

    @pytest.mark.asyncio
    async def test_skips_non_running_models(self, manager_env):
        mgr = manager_env["manager"]
        config = manager_env["config"]
        config.program.alive_time = 1

        mgr._instances["qwen"].state = ModelState.FAILED
        mgr._instances["qwen"].last_request_at = time.time() - 9999

        monitor = IdleMonitor(manager_env["container"])
        monitor._running = True
        await monitor._check_idle_models()

        manager_env["process_mgr"].stop_process.assert_not_called()
