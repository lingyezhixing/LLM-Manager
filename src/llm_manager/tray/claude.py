"""Non-destructive Claude settings preset application.

Claude Code's settings.json nests environment variables under ``data["env"]``
(not at the top level). ``apply_preset`` writes preset keys there while
preserving all other top-level and env keys; ``detect_current_preset`` reads the
current ``ANTHROPIC_BASE_URL`` and matches it against configured presets so the
tray submenu can mark the active one.
"""

from __future__ import annotations

import json
from pathlib import Path


def apply_preset(settings_path: Path, preset: dict[str, str]) -> None:
    p = Path(settings_path)
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
        data["env"] = env
    env.update(preset)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def detect_current_preset(settings_path: Path, presets: dict[str, dict[str, str]]) -> str:
    p = Path(settings_path)
    if not p.exists():
        return "(未知)"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "(未知)"
    base_url = (data.get("env") or {}).get("ANTHROPIC_BASE_URL", "")
    if not base_url:
        return "(未知)"
    for name, preset in presets.items():
        preset_url = preset.get("ANTHROPIC_BASE_URL", "")
        if preset_url and preset_url in base_url:
            return name
    return "(未知)"
