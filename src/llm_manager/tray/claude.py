"""Non-destructive Claude settings preset application."""
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
    data.update(preset)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
