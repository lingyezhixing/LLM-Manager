"""Intel Linux iGPU 适配器:i915 uevent 识别 + intel_gpu_top 指标。"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import DeviceInfo
from .common import _DRM_CLASS, _drm_cards, _system_mem

# ==================== Intel iGPU(i915 + intel_gpu_top)====================
_INTEL_IGPU_NAMES = {
    "8086:46d0": "Intel UHD Graphics (Alder Lake-N)",
    "8086:46d1": "Intel UHD Graphics (Alder Lake-N)",
}


def _intel_gpu_name(dev: Path) -> str:
    """uevent 的 PCI_ID(如 8086:46d1)→ 已知映射名,未知 → 'Intel UHD Graphics (8086:xxxx)'。"""
    try:
        for line in dev.joinpath("uevent").read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("PCI_ID="):
                pci_id = line.split("=", 1)[1].strip().lower()
                return _INTEL_IGPU_NAMES.get(pci_id, f"Intel UHD Graphics ({pci_id})")
    except OSError:
        pass
    return "Intel UHD Graphics"


def _is_i915(dev: Path) -> bool:
    """uevent 含 DRIVER=i915 → Intel GPU(比 vendor 更准,不依赖 gpu_busy_percent 文件)。"""
    try:
        uevent = dev.joinpath("uevent").read_text(encoding="ascii", errors="ignore")
    except OSError:
        return False
    return "DRIVER=i915" in uevent


def _run_intel_gpu_top() -> str | None:
    """intel_gpu_top -J 采样输出(JSON 流)或 None(工具缺失/超时/失败)。
    -s 1000 采样 1s;timeout 2 兜底;每轮 refresh 短进程(与 nvidia-smi 同模式)。
    timeout 杀进程返回 124 属预期(指标照收);非预期失败(工具缺失/超时 4s)→ None。"""
    if shutil.which("intel_gpu_top") is None:
        return None
    try:
        r = subprocess.run(
            ["timeout", "2", "intel_gpu_top", "-J", "-s", "1000"],
            capture_output=True, text=True, timeout=4, check=False,
        )
        return r.stdout if r.returncode in (0, 124) else None  # 124=timeout 杀进程,stdout 含完整采样帧
    except Exception:  # noqa: BLE001 — 子进程异常/超时 → None,指标降级
        return None


def _parse_intel_gpu_top(stdout: str | None) -> dict | None:
    """流式解析 intel_gpu_top -J 输出(pretty 多行/单行/逗号分隔均兼容),
    取最后一帧完整采样(跳过 period.duration<100ms 的初始化帧)。
    返回 {busy_pct, freq_mhz, power_watts};无有效帧/不可解析 → None。"""
    if not stdout:
        return None
    import json
    decoder = json.JSONDecoder()
    buf, last = stdout, None
    while True:
        buf = buf.lstrip(" \t\r\n,[]")
        if not buf:
            break
        try:
            frame, end = decoder.raw_decode(buf)
        except json.JSONDecodeError:
            break
        buf = buf[end:]
        if not isinstance(frame, dict):
            break  # 非 dict JSON(标量/数组)→ 非帧对象,停止(防 .get 穿透)
        if frame.get("period", {}).get("duration", 0) >= 100:
            last = frame
    if last is None:
        return None
    busy = max((e.get("busy", 0.0) for e in last.get("engines", {}).values()), default=0.0)
    freq = last.get("frequency", {}).get("actual")
    power = last.get("power", {}).get("GPU")
    return {
        "busy_pct": busy,
        "freq_mhz": float(freq) if isinstance(freq, (int, float)) else None,
        "power_watts": float(power) if isinstance(power, (int, float)) else None,
    }


class IntelLinuxAdapter:
    """Linux Intel iGPU:uevent DRIVER=i915 识别;利用率/频率/功耗来自 intel_gpu_top
    (sysfs 实测无 busy 接口)。识别与指标解耦:工具缺失 → 设备照常出现,指标 None/0。
    温度:None(N100 平台无 hwmon 传感器,诚实标注)。内存:共享系统 RAM。"""

    def enumerate(self) -> list[DeviceInfo]:
        if os.name == "nt" or not _DRM_CLASS.is_dir():
            return []
        total, avail, used = _system_mem()
        cards = [c for c in _drm_cards() if _is_i915(c / "device")]
        if not cards:
            return []
        metrics = _parse_intel_gpu_top(_run_intel_gpu_top()) or {}
        return [DeviceInfo(
            _intel_gpu_name(c / "device"), "GPU (iGPU)", "Shared RAM",
            total, avail, used, metrics.get("busy_pct", 0.0),
            None, metrics.get("freq_mhz"), metrics.get("power_watts"),
        ) for c in cards]  # 多 i915 卡:按卡命名;指标共享同一次 intel_gpu_top 采样
