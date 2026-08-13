"""Tools API: WOL + Claude 预设路由(/api/tools/*)。

WOL/Claude 纯逻辑在 llm_manager.tools(与托盘 UI 解耦);本模块是它们的 HTTP 表面。
配置仍持久化在 AppConfig(wol / claude_configs),故 GET /api/config 快照仍返回这两块(只读投影);
专用写/动作在此。写回经 set_settings → store.reload(),返回与 config 写回同款 restart-status 形状
(工具配置恒在 RESTART_FIELDS → needs_restart 恒 False)。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from llm_manager.data.config_store import set_settings
from llm_manager.gateway.api.common import config_write_result, get_config_store, get_db
from llm_manager.tools import claude, wol

logger = logging.getLogger(__name__)


class WolUpdate(BaseModel):
    broadcast_address: str = Field(min_length=1)  # 必填非空(清除走 DELETE,不靠空串半残配置)
    mac_address: str = Field(min_length=1)


class ClaudeConfigsUpdate(BaseModel):
    configs: dict[str, dict[str, str]]


class ClaudeApplyRequest(BaseModel):
    name: str


def register_tools_routes(api: APIRouter) -> None:
    @api.put("/tools/wol")
    def put_wol(request: Request, body: WolUpdate) -> dict:
        # 两字段均必填(WolUpdate min_length=1);清除走 DELETE。整组写,与 delete_wol 对称。
        set_settings(
            get_db(request),
            {
                "wol_broadcast": body.broadcast_address,
                "wol_mac": body.mac_address,
            },
        )
        cfg = get_config_store(request).reload()
        return config_write_result(request, cfg)

    @api.delete("/tools/wol")
    def delete_wol(request: Request) -> dict:
        """清除 WOL 配置(删双键 → snapshot.wol=None,托盘动作提示未配置)。
        与 put_wol 对称:WOL 是双键一对,清除整对删,不留孤儿键。"""
        db = get_db(request)
        with db.write_lock:
            db.conn.execute("DELETE FROM system_settings WHERE key IN ('wol_broadcast', 'wol_mac')")
            db.conn.commit()
        cfg = get_config_store(request).reload()
        return config_write_result(request, cfg)

    @api.post("/tools/wol/send")
    def send_wol_now(request: Request, body: WolUpdate) -> dict:
        """立即发送魔术包(WebUI「发送魔术包」;按请求体地址,与托盘 send_wol 同款)。
        广播/MAC 非法(build_magic_packet 校验失败)→ 422。"""
        try:
            wol.send_wol(body.mac_address, body.broadcast_address)
        except Exception as e:
            raise HTTPException(422, f"发送失败: {e}") from e
        return {"ok": True}

    @api.put("/tools/claude")
    def put_claude(request: Request, body: ClaudeConfigsUpdate) -> dict:
        set_settings(
            get_db(request), {"claude_configs": json.dumps(body.configs, ensure_ascii=False)}
        )
        cfg = get_config_store(request).reload()
        return config_write_result(request, cfg)

    @api.post("/tools/claude/apply")
    def apply_claude_preset(request: Request, body: ClaudeApplyRequest) -> dict:
        """把已存预设写入 Claude settings.json(非破坏,仅更新 env 键)。404 未知预设;400 未配置路径。"""
        cfg = get_config_store(request).snapshot()
        preset = (cfg.claude_configs or {}).get(body.name)
        if preset is None:
            raise HTTPException(404, f"preset '{body.name}' not found")
        path = cfg.program.claude_settings_path
        if not path:
            raise HTTPException(400, "未配置 Claude settings 路径")
        try:
            claude.apply_preset(Path(path), dict(preset))
        except OSError as e:
            raise HTTPException(500, f"写入 settings.json 失败:{e}")
        return {"applied": body.name}

    @api.get("/tools/claude/current")
    def current_claude_preset(request: Request) -> dict:
        """探测当前生效预设(按 ANTHROPIC_BASE_URL 子串匹配);未配置路径/读失败 → "(未知)"。"""
        cfg = get_config_store(request).snapshot()
        path = Path(cfg.program.claude_settings_path) if cfg.program.claude_settings_path else None
        current = claude.detect_current_preset(path, dict(cfg.claude_configs)) if path else "(未知)"
        return {"current": current}
