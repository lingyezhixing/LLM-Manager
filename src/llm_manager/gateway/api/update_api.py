"""Self-update API: GET /api/update/status + POST /api/update/check + POST /api/update/apply。

检测语义:程序(worker)启动时后台检测一次(check_update,见 app.py 的
_startup_update_check),结果缓存到 app.state.update_status。此后**无任何自动检测**:
* GET  /status → 读缓存快照(无网络;启动检测未完成 → checking=True 占位);
* POST /check  → 手动触发一次全新检测(前端「检查更新」按钮),刷新缓存并返回;
* POST /apply  → fetch + ff-only 合并到所选目标(冲突/分叉 → 409)→ 成功后走
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
from llm_manager.runtime.update import UpdateError, UpdateStatus, apply_update, check_update


class UpdateTarget(BaseModel):
    """更新目标细粒度:commit = origin/main 最新提交;tag = 最新标签(稳定发布)。"""

    target: Literal["commit", "tag"] = "commit"


def _cached_status(request: Request) -> UpdateStatus:
    """启动检测缓存;未就绪 → checking=True 占位(前端据此轮询等待,不新增检测)。"""
    cached = getattr(request.app.state, "update_status", None)
    return cached if cached is not None else UpdateStatus(checking=True)


def register_update_routes(api: APIRouter) -> None:
    @api.get("/update/status")
    def update_status(request: Request) -> dict:
        """读启动检测缓存快照(无网络)。同步 def → 线程池,不阻塞事件循环。"""
        return asdict(_cached_status(request))

    @api.post("/update/check", status_code=200)
    async def update_check(request: Request) -> dict:
        """手动检查更新(仅「检查更新」按钮触发):跑一次全新 check_update(fetch,
        网络),结果写回缓存并返回。async def → to_thread 外包阻塞的 git 调用。"""
        st = await asyncio.to_thread(check_update)
        request.app.state.update_status = st
        return asdict(st)

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
