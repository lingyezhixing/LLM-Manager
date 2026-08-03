"""Scheduling: pure resource-deficit + eviction-scoring functions.

Stop side-effects are NOT here — Lifecycle executes the returned decisions.
Pure → unit-testable without fakes."""
from __future__ import annotations

from dataclasses import dataclass

from llm_manager.devices import DeviceInfo


@dataclass(frozen=True, slots=True)
class RunnableInfo:
    mem_mb: dict[str, int]    # per-device occupancy of this running model
    pending: int
    last_access: float


def compute_deficit(required: dict[str, int], available: dict[str, int]) -> dict[str, int]:
    """Per-device gap = max(0, required - available). Pure."""
    deficit: dict[str, int] = {}
    for dev, need in required.items():
        gap = need - available.get(dev, 0)
        if gap > 0:
            deficit[dev] = gap
    return deficit


def score_candidates(
    runnable: dict[str, RunnableInfo], deficit_devs: set[str], now: float
) -> list[str]:
    """idle_sec / mem_gb (mem_gb floor 0.5), descending. Excludes pending>0 and
    models occupying no deficit device. Pure."""
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
) -> list[str]:
    """Simulate eviction until deficit satisfied or no evictable candidate.
    Returns model names to stop (in eviction order). Pure.

    若驱逐所有可驱逐模型后 deficit 仍非空(资源根本不足),返回 []——lifecycle 随后
    _deficit_satisfied 判 FAILED。这样不会「白停一批正在跑的模型后才失败」(B5)。
    注:真实停模型后重快照可能因实际占用 ≠ 声明 memory_mb 而更乐观,但不应以此不确定性
    赌注杀运行中模型;配置应保证 memory_mb 准确(见 config.scheme_memory_warnings)。"""
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
        return []      # 模拟无可满足:不返回部分驱逐名单(白停),交由 lifecycle 判 FAILED
    return stopped
