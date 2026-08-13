"""Self-update API: GET /api/update/status + POST /api/update/apply。

状态:fetch 后以 git 标签身份对比本地/远端(不动工作树),给出两个目标的可用性
(tag=最新标签 / commit=最新提交);
应用:fetch + ff-only 合并到所选目标(严格向前,冲突/分叉 → 409)→ 成功后走
common.trigger_restart(与 /api/config/restart 同通道)→ worker 退出 81 →
parent 监督器拉全新 worker 加载新代码(editable 安装,工作树即源码)。
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_manager.gateway.api.common import trigger_restart
from llm_manager.runtime.update import UpdateError, apply_update, check_update


class UpdateTarget(BaseModel):
    """更新目标细粒度:commit = origin/main 最新提交;tag = 最新标签(稳定发布)。"""

    target: Literal["commit", "tag"] = "commit"


def register_update_routes(api: APIRouter) -> None:
    @api.get("/update/status")
    def update_status() -> dict:
        """检查更新(网络 fetch;超时/离线 → ok=False + error)。同步 def → 线程池,不阻塞事件循环。"""
        return asdict(check_update())

    @api.post("/update/apply", status_code=202)
    async def update_apply(body: UpdateTarget, request: Request) -> dict:
        """拉取所选目标(commit/tag)并重启:fetch + ff-only 合并 → trigger_restart
        (202 先冲刷,worker 优雅收口后以 81 退出,parent 拉新 worker 加载新代码)。
        冲突 / 分叉 / 网络失败 / 目标不可用 → 409,不动本地任何东西。
        async def 使 trigger_restart 的 asyncio.create_task 落在事件循环;阻塞的 git
        拉取经 to_thread 外包线程池。"""
        try:
            new_sha = await asyncio.to_thread(apply_update, target=body.target)
        except UpdateError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        trigger_restart(request)
        return {"updated": True, "target": body.target, "sha": new_sha}
