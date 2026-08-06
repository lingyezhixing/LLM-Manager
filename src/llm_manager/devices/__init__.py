"""Device detection: backends enumerate all present hardware (NVIDIA via nvidia-smi,
Intel GPU via i915+intel_gpu_top/LHM, AMD GPU via amdgpu/LHM, CPU via psutil/LHM) →
DeviceMonitor fuzzy-matches config device names to detected hardware (token-subset)
and atomically rebinds a config-keyed cache (+ unmatched devices keyed by raw name
for display). 每个适配器文件内按平台分割路径,每次仅激活一条;频率/温度字段为
可空增量(读不到 → None/0),全链路不新增进程、不新增调用。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol


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
    freq_mhz: float | None = None       # 当前核心频率(核心语义,非显存):clocks.gr / LHM Clock/Core / intel_gpu_top
    power_watts: float | None = None    # 功耗(intel_gpu_top 专用,其余设备读不到)


# DeviceInfo 必须先于各适配器模块导入定义:模块顶层 `from . import DeviceInfo`,
# 若包仍处部分初始化(DeviceInfo 未绑定)则 ImportError(拆包循环导入陷阱)。
from .nvidia import NvidiaAdapter  # noqa: E402 — 显式 re-export(供 build_adapters 装配)
from .intel import IntelAdapter  # noqa: E402
from .amd import AmdAdapter  # noqa: E402
from .cpu import CpuAdapter  # noqa: E402


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


class DeviceAdapter(Protocol):
    """一个平台×厂商数据源 → 统一 DeviceInfo。实现契约:enumerate() 永不抛(内部兜底)。"""

    def enumerate(self) -> list[DeviceInfo]:
        ...


class DeviceMonitor:
    """On-demand 枚举 + 模糊匹配。refresh() 跑全部适配器 → candidates →
    match_devices(get_referenced()) → 原子 rebind self._cache(config 名键控 + 未引用实测名键控)。

    referenced **动态获取**(每次 refresh 按活配置重算):配置运行时可变(WebUI 在线加
    模型),若冻结在启动时,新模型引用的设备名不会进入 online → 启动报 no adaptive
    scheme,必须重启才生效。get_referenced 返回「归一化 config 名」集合。"""
    def __init__(
        self,
        adapters: list["DeviceAdapter"],
        get_referenced: Callable[[], set[str]],
    ) -> None:
        self._adapters = adapters
        self._get_referenced = get_referenced
        self._cache: dict[str, DeviceInfo] = {}

    def refresh(self) -> None:
        candidates: list[DeviceInfo] = []
        for ad in self._adapters:
            try:
                result = ad.enumerate()
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


def build_adapters() -> list[DeviceAdapter]:
    """恒注册 4 个设备适配器;平台/工具检测内移到各适配器 enumerate()(不适用时返回 [])。"""
    return [NvidiaAdapter(), IntelAdapter(), AmdAdapter(), CpuAdapter()]
