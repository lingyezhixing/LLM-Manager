from unittest import mock

from llm_manager.config.schema import AppConfig, ProgramConfig, WakeOnLanConfig
from llm_manager.ops.service import SystemOpsService


def _cfg(wol=True):
    return AppConfig(
        program=ProgramConfig(),
        wake_on_lan=(
            WakeOnLanConfig(
                broadcast_address="192.168.50.255",
                mac_address="a8:b8:e0:08:12:ff",
            )
            if wol
            else None
        ),
    )


def test_wake_on_lan_calls_send(monkeypatch):
    from llm_manager.ops import wol
    calls: list = []
    monkeypatch.setattr(wol, "send_wol_packet", lambda b, m: calls.append((b, m)))
    svc = SystemOpsService(_cfg(), runtime=mock.MagicMock(), devices=mock.MagicMock())
    result = svc.wake_on_lan()
    assert result.ok and calls == [("192.168.50.255", "a8:b8:e0:08:12:ff")]


def test_wake_on_lan_without_config_returns_not_ok():
    svc = SystemOpsService(_cfg(wol=False), runtime=mock.MagicMock(), devices=mock.MagicMock())
    result = svc.wake_on_lan()
    assert not result.ok


def test_unload_all_delegates_to_runtime():
    runtime = mock.MagicMock()
    svc = SystemOpsService(_cfg(), runtime=runtime, devices=mock.MagicMock())
    svc.unload_all()
    runtime.stop.assert_not_called()  # no models configured -> nothing to stop
