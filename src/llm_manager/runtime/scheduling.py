"""调度:纯资源缺口 + 驱逐评分函数。

副作用不在此处——Lifecycle 执行返回的决策。纯函数 → 无需 fake 即可单测。"""

from __future__ import annotations

from dataclasses import dataclass

from llm_manager.devices import DeviceInfo


@dataclass(frozen=True, slots=True)
class RunnableInfo:
    mem_mb: dict[str, int]  # 该运行模型在各设备的占用
    pending: int
    last_access: float


def compute_deficit(required: dict[str, int], available: dict[str, int]) -> dict[str, int]:
    """各设备缺口 = max(0, required - available)。纯函数。"""
    deficit: dict[str, int] = {}
    for dev, need in required.items():
        gap = need - available.get(dev, 0)
        if gap > 0:
            deficit[dev] = gap
    return deficit


def score_candidates(
    runnable: dict[str, RunnableInfo], deficit_devs: set[str], now: float
) -> list[str]:
    """idle_sec / mem_gb(mem_gb 下限 0.5)降序。排除 pending>0 与
    未占用缺口设备的模型。纯函数。"""
    scored: list[tuple[float, str]] = []
    for name, info in runnable.items():
        if info.pending > 0:
            continue
        occ = sum(mb for dev, mb in info.mem_mb.items() if dev in deficit_devs)
        if occ <= 0:
            continue
        idle_sec = max(0.0, now - info.last_access)
        mem_gb = max(0.5, occ / 1024.0)
        scored.append((idle_sec / mem_gb, name))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in scored]


def _available(snap: dict[str, DeviceInfo]) -> dict[str, int]:
    return {dev: info.available_memory_mb for dev, info in snap.items()}


def check_and_free(
    required: dict[str, int],
    snap: dict[str, DeviceInfo],
    runnable: dict[str, RunnableInfo],
    now: float,
) -> list[str] | None:
    """模拟驱逐直至缺口满足或无候选可驱逐。
    返回要停止的模型名(按驱逐顺序);无需驱逐时 [] (缺口一开始即为空);
    驱逐全部可驱逐候选后缺口仍无法满足时 None。纯函数。

    驱逐语义:仅「占用缺口设备 + 无 pending 请求」的模型可驱逐(有请求的不能动,
    没占用缺口设备的驱逐也无效);加权 = idle_sec / mem_gb 降序,逐一下场。
    返回 None(而非 [])让调用方区分「无需驱逐即满足」与「驱逐后仍欠」——后者可
    回退到下一方案,而非白停一批模型后失败。注:真实停模型后重快照可能因
    实际占用 ≠ 声明 memory_mb 而更乐观,但不应以此不确定性赌注杀运行中模型;
    配置应保证 memory_mb 准确。"""
    working = _available(snap)
    deficit_devs = set(compute_deficit(required, working))
    stopped: list[str] = []
    while deficit_devs:
        candidates = [c for c in score_candidates(runnable, deficit_devs, now) if c not in stopped]
        if not candidates:
            break
        victim = candidates[0]
        stopped.append(victim)
        for dev, mb in runnable[victim].mem_mb.items():
            working[dev] = working.get(dev, 0) + mb
        deficit_devs = set(compute_deficit(required, working))
    if deficit_devs:
        return None  # 模拟无可满足:不返回部分驱逐名单(白停),交由调用方回退/判 FAILED
    return stopped
