"""主机 CPU 适配器:psutil RAM/占用 + 温度(LHM Tctl 仅 Windows;hwmon 仅 Linux)。"""
from __future__ import annotations

import os

import psutil

from . import DeviceInfo
from .common import _system_mem, _lhm_computer


def _valid_reading(s) -> bool:
    """LHM 传感器读数佐证:Power/Clock/Temperature 值 > 0 才算有效。0/NaN 是通道不可用
    哨兵(非管理员 Ryzen 整组全 0/nan);Load/Voltage 非管理员下也正常,不作佐证。"""
    if str(getattr(s, "SensorType", "")) not in ("Power", "Clock", "Temperature"):
        return False
    v = getattr(s, "Value", None)
    if v is None:
        return False
    try:
        return float(v) > 0  # NaN > 0 为 False,一并排除
    except (TypeError, ValueError):
        return False


def _lhm_cpu_temp() -> float | None:
    """Windows CPU 温度解析(私有,见 _cpu_temp 平台分支):遍历 LHM Cpu 硬件取 Tctl/Tdie。
    0°C 需同硬件有有效佐证(真制冷)才上报;整组 0/NaN(通道不可用)→ None;NaN 永不返回。"""
    c = _lhm_computer()
    if c is None:
        return None
    try:
        for hw in c.Hardware:
            if str(hw.HardwareType) != "Cpu":
                continue
            hw.Update()
            zero_seen = False
            for s in hw.Sensors:
                if str(s.SensorType) != "Temperature" or not (
                    "Tctl" in str(s.Name) or "Tdie" in str(s.Name)
                ):
                    continue
                v = s.Value
                if v is None:
                    continue
                fv = float(v)
                if fv > 0:
                    return fv
                if fv == 0:
                    zero_seen = True
            if zero_seen and any(_valid_reading(x) for x in hw.Sensors):
                return 0.0
    except Exception:  # noqa: BLE001 — 读 LHM CPU 温度失败 → None
        return None
    return None


def _cpu_temp() -> float | None:
    """CPU 温度:平台内部分支(与 intel/amd 适配器同构)——Windows → LHM Tctl/Tdie
    (佐证逻辑见 _lhm_cpu_temp);Linux → hwmon(coretemp/k10temp)。"""
    if os.name == "nt":
        return _lhm_cpu_temp()
    return _cpu_temp_hwmon()


def _lhm_cpu_freq() -> float | None:
    """Windows CPU 频率解析(私有):遍历 LHM Cpu 硬件 Clock/Core 传感器取有效值最大者。
    nan 是非管理员哨兵(与 Tctl 同源);CPU 频率无真实 0 值(min freq > 0),>0 即可信,无需佐证。"""
    c = _lhm_computer()
    if c is None:
        return None
    try:
        best = 0.0
        for hw in c.Hardware:
            if str(hw.HardwareType) != "Cpu":
                continue
            hw.Update()
            for s in hw.Sensors:
                if str(s.SensorType) != "Clock" or "Core" not in str(s.Name):
                    continue
                v = float(s.Value) if s.Value is not None else None
                if v is not None and v > 0:  # nan > 0 为 False,一并排除
                    best = max(best, v)
        return float(best) if best > 0 else None
    except Exception:  # noqa: BLE001 — 读 LHM CPU 频率失败 → None
        return None


def _cpu_freq_psutil() -> float | None:
    """Linux CPU 频率(psutil.cpu_freq().current,容器实测可读);不可用 → None。"""
    try:
        f = psutil.cpu_freq()
        return float(f.current) if f is not None and f.current > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _cpu_freq() -> float | None:
    """CPU 频率:平台内部分支(同 _cpu_temp)——Windows → LHM Clock/Core;Linux → psutil。"""
    if os.name == "nt":
        return _lhm_cpu_freq()
    return _cpu_freq_psutil()


def _cpu_temp_hwmon() -> float | None:
    """Linux hwmon CPU 温度(psutil.sensors_temperatures,容器内 /sys 只读可见)。
    只认 CPU 芯片 coretemp/k10temp/cpu_thermal;优先 label 为 Package id 0 / Tctl / Tdie 的
    条目,无则取首个 current>0;不拿 acpitz/it8613(主板/环境温度)兜底。Windows 无此函数 → None。"""
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is None:
        return None
    try:
        chips = sensors()
    except Exception:  # noqa: BLE001 — 平台不支持/权限问题 → None
        return None
    for chip in ("coretemp", "k10temp", "cpu_thermal"):
        for st in chips.get(chip, []):
            if st.current > 0 and st.label in ("Package id 0", "Tctl", "Tdie"):
                return float(st.current)
    for chip in ("coretemp", "k10temp", "cpu_thermal"):
        for st in chips.get(chip, []):
            if st.current > 0:
                return float(st.current)
    return None


class CpuAdapter:
    """主机 CPU:psutil 取 RAM/占用 + _cpu_temp/_cpu_freq 取温度/频率。device_name='CPU'(token 含 cpu)。
    CPU 物理恒在 → 恒返回 1 元素:psutil 调用包 try/except,失败返回降级 DeviceInfo(零值/温度频率 None)不抛。"""

    def enumerate(self) -> list[DeviceInfo]:
        total, avail, used = _system_mem()
        try:
            usage = float(psutil.cpu_percent(interval=None))
        except Exception:  # noqa: BLE001
            return [DeviceInfo("CPU", "CPU", "RAM", total, avail, used, 0.0, None)]
        return [DeviceInfo("CPU", "CPU", "RAM", total, avail, used, usage, _cpu_temp(), _cpu_freq())]
