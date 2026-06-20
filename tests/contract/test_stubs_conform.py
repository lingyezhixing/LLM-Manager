"""Stubs must conform to their ports; the probes registry must cover all ModelMode values."""

import pathlib

from llm_manager.domain.model import ModelMode
from llm_manager.ports.devices import DeviceRegistry, probes
from llm_manager.ports.events import EventBus
from llm_manager.ports.process import ProcessBackend
from llm_manager.ports.runtime import ModelRuntimePort
from llm_manager.ports.service import Service


def test_devices_probes_cover_all_modes():
    import llm_manager.devices.probes  # noqa: F401  (registration side-effect)
    for mode in ModelMode:
        assert mode in probes, f"missing probe for {mode}"


def test_stubs_satisfy_ports(tmp_path):
    from llm_manager.devices.registry import DeviceRegistryImpl
    from llm_manager.events.bus import EventBusImpl
    from llm_manager.persistence.repository import Repository
    from llm_manager.persistence.store import SqliteStore
    from llm_manager.process.backend import SubprocessBackend
    from llm_manager.process.reaper_service import ReaperService
    from llm_manager.runtime.controller import ModelController
    from llm_manager.runtime.idle_service import IdleCheckService

    store = SqliteStore(pathlib.Path(tmp_path) / "throwaway.db")
    store.connect()
    meter = Repository(store)
    runtime = ModelController(
        config=None,
        process=SubprocessBackend(),
        devices=DeviceRegistryImpl(),
        probes=probes,
        meter=meter,
        events=EventBusImpl(),
    )
    assert isinstance(DeviceRegistryImpl(), DeviceRegistry)
    assert isinstance(EventBusImpl(), EventBus)
    assert isinstance(SubprocessBackend(), ProcessBackend)
    assert isinstance(ReaperService(SubprocessBackend()), Service)
    assert isinstance(runtime, ModelRuntimePort)
    assert isinstance(IdleCheckService(runtime), Service)
    store.close()
