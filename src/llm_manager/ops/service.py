"""SystemOpsService: in-process system operations (extracted from tray.py).

Called directly by hosts (e.g. the future desktop tray), NOT over HTTP. WOL and
Claude preset switching are real; restart_autostart/unload_all delegate to the
ModelRuntimePort (stub in this plan, real once runtime is filled)."""

from __future__ import annotations

from llm_manager.config.loader import catalog_domain_models
from llm_manager.config.schema import AppConfig
from llm_manager.domain.result import OperationResult
from llm_manager.ops import wol
from llm_manager.ports.devices import DeviceRegistry
from llm_manager.ports.runtime import ModelRuntimePort


class SystemOpsService:
    def __init__(
        self,
        config: AppConfig,
        runtime: ModelRuntimePort,
        devices: DeviceRegistry,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._devices = devices

    def wake_on_lan(self) -> OperationResult:
        wol_cfg = self._config.wake_on_lan
        if wol_cfg is None:
            return OperationResult(ok=False, message="wake_on_lan not configured")
        try:
            wol.send_wol_packet(wol_cfg.broadcast_address, wol_cfg.mac_address)
            return OperationResult(ok=True, message=f"WOL sent to {wol_cfg.mac_address}")
        except Exception as e:  # noqa: BLE001
            return OperationResult(ok=False, message=f"WOL failed: {e}")

    def switch_claude_config(self, preset: str) -> OperationResult:
        from llm_manager.ops import claude_settings

        claude = self._config.claude
        if claude is None or preset not in claude.presets or claude.settings_path is None:
            return OperationResult(ok=False, message=f"preset '{preset}' not configured")
        try:
            claude_settings.apply_preset(claude.settings_path, claude.presets[preset])
            return OperationResult(ok=True, message=f"switched to {preset}")
        except Exception as e:  # noqa: BLE001
            return OperationResult(ok=False, message=f"switch failed: {e}")

    def restart_autostart(self) -> OperationResult:
        stopped = self.unload_all()
        import time

        time.sleep(2)  # load-bearing teardown pause (preserved from tray.py)
        started = 0
        for model in catalog_domain_models(self._config):
            if model.auto_start:
                self._runtime.start(model.primary_name)
                started += 1
        return OperationResult(ok=stopped.ok, message=f"restarted {started} auto-start model(s)")

    def unload_all(self) -> OperationResult:
        n = 0
        for model in catalog_domain_models(self._config):
            self._runtime.stop(model.primary_name)
            n += 1
        return OperationResult(ok=True, message=f"stopped {n} model(s)")
