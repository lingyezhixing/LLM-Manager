"""Device detection: backends enumerate all present hardware (NVIDIA via nvidia-smi,
Intel iGPU via i915 sysfs, AMD GPU via LHM, CPU via psutil) → DeviceMonitor
fuzzy-matches config device names to detected hardware (token-subset) and atomically
rebinds a config-keyed cache (+ unmatched devices keyed by raw name for display)."""
from __future__ import annotations

import os  # noqa: F401 — Task 3 build_adapters 装配时使用(平台判断)
import re
import shutil  # noqa: F401 — Task 3 build_adapters 装配时使用(nvidia-smi 探测)
from dataclasses import dataclass
from pathlib import Path  # noqa: F401 — Task 3 build_adapters 装配时使用(sysfs 检查)
from typing import Callable


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    device_name: str
    device_type: str
    memory_type: str
    total_memory_mb: int
    available_memory_mb: int
    used_memory_mb: int
    usage_percentage: float
    temperature_celsius: float | None


# DeviceInfo 必须先于 adapters 导入定义:adapters.py 顶层 `from . import DeviceInfo`,
# 若包仍处部分初始化(DeviceInfo 未绑定)则 ImportError(拆包循环导入陷阱)。
from .adapters import *  # noqa: F401,F403,E402 — re-export 全部适配器符号(公共+测试私有接缝)
from . import adapters as _adapters  # noqa: E402
# 私有测试接缝:`import *` 不含下划线名(adapters 无 __all__),显式 re-export 保持
# `from llm_manager.devices import _parse_smi, _aggregate_sensors`(test_devices 顶部)不破。
from .adapters import _parse_smi, _aggregate_sensors  # noqa: F401,E402


# 兼容转发:现有测试 `import llm_manager.devices as dev; dev.enumerate_cpu()` 不破。
# 注意:函数体全局名在 adapters 模块解析,monkeypatch 必须 setattr(adapters, ...)。
def enumerate_nvidia() -> list["DeviceInfo"]:
    return _adapters.NvidiaAdapter().enumerate()


def enumerate_cpu() -> list["DeviceInfo"]:
    return _adapters.CpuAdapter().enumerate()


def enumerate_lhm_gpus() -> list["DeviceInfo"]:
    return _adapters.LhmAdapter().enumerate()


def enumerate_intel_igpus() -> list["DeviceInfo"]:
    return _adapters.IntelLinuxAdapter().enumerate()


def _tokens(name: str) -> set[str]:
    """小写 + 按非字母数字拆 token。'RTX 4060 Ti'→{rtx,4060,ti};'V100-SXM2'→{v100,sxm2}。"""
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def match_devices(
    referenced: set[str], candidates: list[DeviceInfo]
) -> tuple[dict[str, DeviceInfo], list[DeviceInfo]]:
    """每个 config 名取全子集(required ⊆ online)的候选;并列去歧义键=(精确等同, -多余 token 数, -索引)
    取最大。多余 token 数 = |detected − config|(越少越贴近 config)。一个候选只配一个 config 名(used 集)。
    返回 (config 键控匹配 dict, 未引用候选 list)。遍历 referenced 按 sorted() 保证赋值决定性。"""
    matched: dict[str, DeviceInfo] = {}
    used: set[int] = set()
    for name in sorted(referenced):
        ct = _tokens(name)
        if not ct:
            continue
        best_idx = -1
        best_key: tuple | None = None
        for i, cand in enumerate(candidates):
            if i in used:
                continue
            dt = _tokens(cand.device_name)
            if not ct <= dt:  # 要求全子集
                continue
            key = (ct == dt, -len(dt - ct), -i)  # 精确等同优先 → 多余 token 最少 → 索引最小
            if best_key is None or key > best_key:
                best_key = key
                best_idx = i
        if best_idx >= 0:
            matched[name] = candidates[best_idx]
            used.add(best_idx)
    unmatched = [c for i, c in enumerate(candidates) if i not in used]
    return matched, unmatched


class DeviceMonitor:
    """On-demand 枚举 + 模糊匹配。refresh() 跑全部枚举器 → candidates →
    match_devices(get_referenced()) → 原子 rebind self._cache(config 名键控 + 未引用实测名键控)。

    referenced **动态获取**(每次 refresh 按活配置重算):配置运行时可变(WebUI 在线加
    模型),若冻结在启动时,新模型引用的设备名不会进入 online → 启动报 no adaptive
    scheme,必须重启才生效。get_referenced 返回「归一化 config 名」集合。"""
    def __init__(
        self,
        enumerators: list[Callable[[], list[DeviceInfo]]],
        get_referenced: Callable[[], set[str]],
    ) -> None:
        self._enumerators = enumerators
        self._get_referenced = get_referenced
        self._cache: dict[str, DeviceInfo] = {}

    def refresh(self) -> None:
        candidates: list[DeviceInfo] = []
        for enum in self._enumerators:
            try:
                result = enum()
                if result:
                    candidates.extend(result)
            except Exception:  # noqa: BLE001 — 单个后端失败不影响其他
                pass
        matched, unmatched = match_devices(self._get_referenced(), candidates)
        cache: dict[str, DeviceInfo] = dict(matched)
        for c in unmatched:
            cache[c.device_name] = c  # 未引用:以实测名入快照供展示(对调度无害)
        self._cache = cache  # 原子 rebind

    def online_devices(self) -> set[str]:
        return set(self._cache)

    def snapshot(self) -> dict[str, DeviceInfo]:
        return dict(self._cache)


# 公共 API 兼容(纯移动):app.py 仍消费 ENUMERATORS 常量,Task 3 换 build_adapters() 后删除。
ENUMERATORS: list[Callable[[], list[DeviceInfo]]] = [enumerate_nvidia, enumerate_intel_igpus, enumerate_lhm_gpus, enumerate_cpu]
