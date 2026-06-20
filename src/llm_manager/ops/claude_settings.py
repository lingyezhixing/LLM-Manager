"""Non-destructive rewrite of ~/.claude/settings.json env preset
(extracted from tray.py). Loads existing JSON, ensures an 'env' dict, overwrites
only the preset's keys, writes back with ensure_ascii=False, indent=2."""

from __future__ import annotations

import json
import pathlib


def apply_preset(settings_path: pathlib.Path, preset: dict[str, str]) -> None:
    settings_path = pathlib.Path(settings_path)
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{settings_path} is not a JSON object")
    env = data.setdefault("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"{settings_path} .env is not a JSON object")
    env.update(preset)
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
