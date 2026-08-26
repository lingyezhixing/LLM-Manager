"""非破坏性的 Claude settings 预设应用。

Claude Code 的 settings.json 把环境变量嵌套在 ``data["env"]`` 下(不在顶层)。
``apply_preset`` 在那里写入预设键,同时保留其余全部顶层与 env 键;
``detect_current_preset`` 读取当前 ``ANTHROPIC_BASE_URL`` 并与已配置预设匹配,
使托盘子菜单能标记当前生效项。
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
