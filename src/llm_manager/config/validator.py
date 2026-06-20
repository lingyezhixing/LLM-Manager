"""Cross-model config validation. Returns a list of human-readable error strings
(empty == valid).

mode is validated against the ModelMode enum — the declarative source of valid
modes. Every ModelMode gets a registered probe once devices/probes.py (Plan 2)
populates the probes registry, so the enum and the registry stay in sync; this
keeps config depending only on domain (no config -> ports import). Device names
are compared case-insensitively and inconsistent casing is flagged.
"""

from __future__ import annotations

from llm_manager.config.schema import AppConfig
from llm_manager.domain.model import ModelMode


def validate(cfg: AppConfig) -> list[str]:
    errors: list[str] = []

    seen_ports: dict[int, str] = {}
    seen_aliases: dict[str, str] = {}
    supported_modes = {m.value for m in ModelMode}
    device_casings: dict[str, set[str]] = {}  # normalized -> {original casings}

    for name, entry in cfg.models.items():
        if entry.port in seen_ports:
            errors.append(
                f"Port {entry.port} shared by models '{seen_ports[entry.port]}' and '{name}'"
            )
        else:
            seen_ports[entry.port] = name

        if not entry.aliases:
            errors.append(f"Model '{name}' has no aliases")
        for alias in entry.aliases:
            if alias in seen_aliases:
                errors.append(
                    f"Alias '{alias}' shared by models '{seen_aliases[alias]}' and '{name}'"
                )
            else:
                seen_aliases[alias] = name

        if entry.mode not in supported_modes:
            errors.append(
                f"Model '{name}' mode '{entry.mode}' is not supported "
                f"(supported: {sorted(supported_modes)})"
            )

        if not entry.schemes:
            errors.append(f"Model '{name}' has no device scheme")

        for scheme in entry.schemes.values():
            for dev in scheme.required_devices:
                device_casings.setdefault(dev.lower(), set()).add(dev)
            for dev in scheme.memory_mb:
                device_casings.setdefault(dev.lower(), set()).add(dev)

    for norm, casings in device_casings.items():
        if len(casings) > 1:
            errors.append(
                f"Device '{norm}' referenced with inconsistent casing: {sorted(casings)}"
            )

    return errors
