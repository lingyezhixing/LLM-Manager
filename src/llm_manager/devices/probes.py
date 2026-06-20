"""Register one stub probe per ModelMode so probes.keys() == all ModelMode values
(keeps the validator's single-source invariant). Real health-check logic comes later."""

from __future__ import annotations

from llm_manager.domain.model import ModelMode
from llm_manager.domain.result import ProbeResult
from llm_manager.ports.devices import probes


def _stub_probe(
    alias: str,
    port: int,
    start_time: float | None = None,
    timeout: float = 300,
) -> ProbeResult:
    # TODO(phase-devices): real shallow+deep health check per mode.
    return ProbeResult(ok=False, message="probe not implemented")


for _mode in ModelMode:
    if _mode not in probes:  # idempotent: safe if module body re-executes
        probes.register(_mode)(_stub_probe)
