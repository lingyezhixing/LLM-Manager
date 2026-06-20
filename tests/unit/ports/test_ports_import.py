"""Ports must import cleanly (no impl-layer imports) and expose the contracts."""
from llm_manager.domain.device import DeviceName
from llm_manager.domain.model import ModelMode
from llm_manager.registry import Registry


def test_ports_modules_import():
    import llm_manager.ports.config  # noqa: F401
    import llm_manager.ports.devices  # noqa: F401
    import llm_manager.ports.events  # noqa: F401
    import llm_manager.ports.gateway  # noqa: F401
    import llm_manager.ports.metering  # noqa: F401
    import llm_manager.ports.process  # noqa: F401
    import llm_manager.ports.runtime  # noqa: F401
    import llm_manager.ports.service  # noqa: F401
    import llm_manager.ports.system_ops  # noqa: F401


def test_all_four_registries_exist():
    """Spec §8 mandates four global registry instances.

    probes/devices stay empty until their impl layers (later plans) load.
    token_parsers/endpoint_shapes are populated by the metering impl layer
    (Plan 2) via the conftest session bootstrap.
    """
    from llm_manager.ports.devices import devices, probes
    from llm_manager.ports.gateway import endpoint_shapes
    from llm_manager.ports.metering import token_parsers

    assert isinstance(probes, Registry) and ModelMode.CHAT not in probes
    assert isinstance(devices, Registry) and DeviceName("rtx 4060") not in devices
    assert isinstance(token_parsers, Registry) and "v1/messages" in token_parsers
    assert isinstance(endpoint_shapes, Registry) and "v1/chat/completions" in endpoint_shapes


def test_protocol_symbols_exist():
    from llm_manager.ports.devices import DevicePlugin, DeviceRegistry, Probe
    from llm_manager.ports.events import EventBus
    from llm_manager.ports.gateway import GatewayPort, ProxyRequest
    from llm_manager.ports.metering import MeteringSink, TokenParser
    from llm_manager.ports.process import ProcessBackend
    from llm_manager.ports.runtime import ModelRuntimePort
    from llm_manager.ports.service import Service
    from llm_manager.ports.system_ops import SystemOps

    assert DeviceName is not None  # sanity
    assert all(
        c is not None
        for c in [
            DevicePlugin, DeviceRegistry, Probe, EventBus, GatewayPort, ProxyRequest,
            MeteringSink, TokenParser, ProcessBackend, ModelRuntimePort, Service, SystemOps,
        ]
    )
