# -*- coding: utf-8 -*-
"""硬件后端探测：优先真实 EC（厂商 DLL + 裸端口双通道），失败则进入模拟模式。"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from typing import Dict, List, Optional

from hardware.ec import DEFAULT_RPM_MAP, EcBus, EcTimeout
from hardware.ntport import NtPortIO, PortIOError
from hardware.vendor import VendorEC


class SimBus:
    """模拟 EC：让界面在没有驱动/没有权限时也能完整跑起来。

    行为刻意做得像真机：温度随"负载"漂移，转速跟随占空比，
    这样界面上的曲线、联动、熔断保护都能真实演示。
    """

    def __init__(self):
        self.t0 = time.time()
        self.duty: Dict[int, int] = {1: 0, 2: 0}
        self.manual: Dict[int, bool] = {1: False, 2: False}
        self.ram = {i: 0 for i in range(256)}
        self.ram[0xC8] = 2
        self.rpm_map = dict(DEFAULT_RPM_MAP)
        self._phase = random.random() * 6

    def _load_wave(self) -> float:
        t = time.time() - self.t0 + self._phase
        base = 0.42 + 0.33 * math.sin(t / 26.0)
        jitter = random.uniform(-0.03, 0.03)
        return max(0.05, min(1.0, base + jitter))

    def _firmware_duty(self) -> int:
        return int(28 + self._load_wave() * 56)

    # ---- 读 ----
    def read_ram(self, addr: int) -> int:
        addr &= 0xFF
        rpm = self.read_rpm(1) if addr in (0xD0, 0xD1) else None
        if rpm is not None:
            return (rpm >> 8) & 0xFF if addr == 0xD0 else rpm & 0xFF
        rpm2 = self.read_rpm(2) if addr in (0xD2, 0xD3) else None
        if rpm2 is not None:
            return (rpm2 >> 8) & 0xFF if addr == 0xD2 else rpm2 & 0xFF
        return self.ram.get(addr, 0)

    def read_block(self, start: int, count: int) -> List[Optional[int]]:
        return [self.read_ram(start + i) for i in range(count)]

    def read_temp_raw(self, fan_no: int):
        return self.read_temp(fan_no), self.read_duty(fan_no), 0

    def read_temp(self, fan_no: int) -> int:
        load = self._load_wave()
        duty = self.duty.get(fan_no, 0)
        base = 41 + load * 39 + (fan_no - 1) * 2
        cooling = duty * 0.055
        return int(max(30, min(97, base - cooling + random.uniform(-1.2, 1.2))))

    def read_rpm(self, fan_no: int) -> int:
        was_manual = self.manual.get(fan_no, False)
        d = self.duty.get(fan_no, 0) if was_manual else self._firmware_duty()
        return int(880 + d * 27 + random.uniform(-45, 45))

    def read_duty(self, fan_no: int) -> int:
        if self.manual.get(fan_no, False):
            return int(self.duty.get(fan_no, 0))
        return self._firmware_duty()

    def fan_count(self) -> int:
        return 2

    def ec_version(self) -> str:
        return "SIMULATED"

    # ---- 写 ----
    def write_ram(self, addr: int, value: int) -> None:
        self.ram[addr & 0xFF] = value & 0xFF

    def set_fan_duty(self, fan_no: int, percent: int) -> None:
        self.manual[fan_no] = True
        self.duty[fan_no] = max(0, min(100, percent))

    def set_fan_auto(self, fan_no: int) -> None:
        self.manual[fan_no] = False

    def snapshot(self, addrs) -> Dict[int, Optional[int]]:
        return {a: self.read_ram(a) for a in addrs}

    def restore(self, snap, only_changed: bool = True) -> int:
        n = 0
        for addr, value in snap.items():
            if value is None:
                continue
            self.write_ram(addr, value)
            n += 1
        return n


class HybridBus:
    """统一总线：优先走厂商 DLL（它知道本机正确寄存器、握手不会轻易放弃），
    读不到再退回自己按协议裸读写。

    真机上踩到的两个坑，这层专门用来避开：

    1. GPU 转速寄存器是 0xD2/0xD3（厂商 GetGpuFanRpm），不是 0xD4/0xD5；
    2. 自己拼的 0x80 读 RAM 时序在部分 EC 上拿不到响应，超时放弃后那半截
       命令会留在 EC 里（OBF 一直占着），轻则读不到、重则把端口驱动搞呆。
       所以只要厂商 DLL 可用，轮询期间就不再碰裸端口。
    """

    #: 0x99 是【8 位正向占空比】：0 = 停转，254 = 满速，255 = 交还固件。
    #: 量程不是 0-100！旧版把 100 当满速，其实只等于 254 的 39%，
    #: 所以「点全速」时风扇只是慢慢转。满载实测（转速计计数，越小越快）：
    #:   90→1011  100→920  150→657  180→570  200→525  230→477  254→441
    #: 而固件在 90℃ 时是 439 —— 254 才对得上满速。
    #: 方向是正向（0 停、254 满）；旧版两处都判错，方向反 + 量程只有 39%，
    #: 于是「全速」写成慢速、界面还把最慢读成满速，风扇自然“不动”。

    #: 0xD0/0xD1 一直是【转速计周期计数】，与转速成反比（不分自动/手动）。
    #: 系数按「满速计数 441 ≈ 5000 RPM」标定，误差约 10%，仅用于显示。
    RPM_K = 2_200_000
    RPM_CAP = 8000

    #: 关键：0x99 是【8 位】占空比，量程 0-255，255 保留给固件自动。
    #: 之前当 0-100 用，100% 只等于 254 的 39%，实测计数 920（满速是 441），
    #: 所以「全速」其实只跑了一半不到，听起来就是风扇没动。
    EC_FULL = 254   #: 254 = 满速（实测计数 441，与固件 90℃ 的 439 一致）
    EC_MIN = 12     #: 非零时不低于 12（≈5%），太低下风扇可能起转困难

    def __init__(self, raw: EcBus, vendor: Optional[VendorEC] = None):
        self.raw = raw
        self.vendor = vendor if (vendor and vendor.ok) else None
        self.rpm_map = dict(getattr(raw, "rpm_map", DEFAULT_RPM_MAP))
        # 常规裸读写失败后停用；交还固件自动的恢复写不受此软件熔断限制。
        self.raw_ok = True
        self.raw_error = ""
        self.stats = {"vendor": 0, "raw": 0, "fail": 0}
        self.last_error = ""
        # 哪些风扇当前处在手动模式（决定 0xD0/0xD1 的读数怎么解释）
        self.manual: Dict[int, bool] = {1: False, 2: False, 3: False, 4: False}

    # ---- 内部 ----
    def _raw(self, fn, *args):
        if not self.raw_ok:
            raise EcTimeout(f"裸端口已停用（{self.raw_error or '此前读取失败'}）")
        try:
            return fn(*args)
        except EcTimeout as exc:
            self.raw_ok = False
            self.raw_error = str(exc)
            self.stats["fail"] += 1
            raise

    # ---- 占空比换算 ----
    #: 0x99 的值就是占空比百分比，0 = 停转、100 = 满速，直接对应。
    #: 界面统一用 0-100 的"风量"，只需把 0 托到 EC_MIN 以免真的停转。
    def to_ec(self, pct: int) -> int:
        """界面风量 (0-100) → EC 的 8 位占空比 [0, 254]。

        0 表示停转（只有用户明确要 0 才会给到），其余不低于 EC_MIN。
        """
        pct = max(0, min(100, int(pct)))
        if pct <= 0:
            return 0
        ec = round(pct * self.EC_FULL / 100.0)
        return int(max(self.EC_MIN, min(self.EC_FULL, ec)))

    def from_ec(self, val: int) -> int:
        """EC 的 8 位占空比 → 界面风量；255 表示固件自动，返回 -1。"""
        if val == 255 or val < 0:
            return -1
        return int(max(0, min(100, round(val * 100.0 / self.EC_FULL))))

    # ---- 转速 ----
    def _conv_rpm(self, fan_no: int, raw: int) -> int:
        """把 0xD0/0xD1 的读数换成转速。

        这个寄存器存的始终是【转速计周期计数】，与转速成反比：
        满速时约 441，低速能到 15000+。旧版直接当 RPM 用，才会出现
        「显示 7000 转、实际不转」——那时读数其实是「最慢」。

        反算系数按满速 441 ≈ 5000 RPM 标定，只作参考显示（误差约 10%）。
        """
        if raw <= 0:
            return 0
        return int(max(0, min(self.RPM_CAP, round(self.RPM_K / raw))))

    # ---- 传感器 ----
    def read_fan(self, fan_no: int, temp_index: Optional[int] = None) -> Dict[str, int]:
        """一次取齐温度 / 占空比 / 转速，尽量少打扰 EC。"""
        idx = int(temp_index if temp_index is not None else fan_no)
        temp, duty, fw_duty = -1, -1, -1
        if self.vendor:
            data = self.vendor.fan_raw(idx)
            if data:
                self.stats["vendor"] += 1
                temp = data["temp"]
                # b1 = 固件按温度算出来的目标，b2 = 我们下发的设定值(255=自动)
                fw_duty = data["b1"] if 0 <= data["b1"] <= 100 else -1
                duty = self.from_ec(data["b2"])
        if temp <= 0:
            try:
                b1, b2, _ = self._raw(self.raw.read_temp_raw, idx)
                temp = b1
                if duty < 0:
                    duty = self.from_ec(b2)
            except EcTimeout as exc:
                self.last_error = f"温度：{exc}"
                temp = -1
        if duty < 0:
            duty = fw_duty          # 自动模式下显示固件曲线的目标值
        try:
            rpm = self.read_rpm(fan_no)
        except EcTimeout:
            rpm = -1
        return {"temp": temp, "duty": duty, "fw_duty": fw_duty, "rpm": rpm}

    def read_temp(self, fan_no: int) -> int:
        if self.vendor:
            v = self.vendor.temp(fan_no)
            if v is not None and 0 < v < 120:
                self.stats["vendor"] += 1
                return v
        try:
            return self._raw(self.raw.read_temp, fan_no)
        except EcTimeout as exc:
            self.last_error = f"温度：{exc}"
            raise

    def read_temp_raw(self, fan_no: int) -> tuple:
        if self.vendor:
            data = self.vendor.fan_raw(fan_no)
            if data:
                return data["temp"], data["b1"], data["b2"]
        return self._raw(self.raw.read_temp_raw, fan_no)

    def read_rpm(self, fan_no: int) -> int:
        if self.vendor:
            v = self.vendor.rpm(fan_no)
            if v is not None:
                self.stats["vendor"] += 1
                # 0 是该风扇停转，不能拿另一只风扇的非零寄存器替代。
                return self._conv_rpm(fan_no, v)
        try:
            # 裸通道只使用已确认的 fan_no → rpm_map，不扫描猜测传感器身份。
            return self._conv_rpm(fan_no, self._raw(self.raw.read_rpm, fan_no))
        except EcTimeout as exc:
            self.last_error = f"转速：{exc}"
            raise

    def read_duty(self, fan_no: int) -> int:
        """当前占空比（百分比）；固件自动且读不到目标值时返回 -1。"""
        try:
            _, b1, b2 = self.read_temp_raw(fan_no)
        except (EcTimeout, OSError):
            return -1
        val = self.from_ec(b2)
        if val >= 0:
            return val
        return b1 if 0 <= b1 <= 100 else -1

    # ---- RAM ----
    def read_ram(self, addr: int) -> int:
        return self._raw(self.raw.read_ram, addr)

    def write_ram(self, addr: int, value: int) -> None:
        return self._raw(self.raw.write_ram, addr, value)

    def read_block(self, start: int, count: int) -> List[Optional[int]]:
        if not self.raw_ok:
            return [None] * count
        return self.raw.read_block(start, count)

    def snapshot(self, addrs) -> Dict[int, Optional[int]]:
        if not self.raw_ok:
            return {a: None for a in addrs}
        return self.raw.snapshot(addrs)

    def restore(self, snap, only_changed: bool = True) -> int:
        return self.raw.restore(snap, only_changed)

    def fan_count(self) -> int:
        if self.vendor:
            v = self.vendor.fan_count()
            if v:
                return v
        return self._raw(self.raw.fan_count)

    def ec_version(self) -> str:
        if self.vendor:
            v = self.vendor.ec_version()
            if v:
                return v
        return self.raw.ec_version()

    # ---- 写 ----
    def set_fan_duty(self, fan_no: int, percent: int) -> None:
        ec_val = self.to_ec(percent)
        # 最后一道闸：非零指令不许掉进「起转不起来」的低占空比区
        if 0 < ec_val < self.EC_MIN:
            ec_val = self.EC_MIN
        if ec_val > self.EC_FULL:
            ec_val = self.EC_FULL
        self.manual[fan_no] = True
        if self.vendor and self.vendor.set_duty(fan_no, ec_val):
            return
        self._raw(self.raw.set_fan_duty, fan_no, ec_val)

    def set_fan_auto(self, fan_no: int) -> None:
        if self.vendor and self.vendor.set_auto(fan_no):
            self.manual[fan_no] = False
            return
        # 读超时的锁存不能封死安全恢复；每次请求只尝试一次，不重新开放普通写入。
        # 这只绕过软件熔断，不能取消真正阻塞在驱动内部的调用。
        try:
            self.raw.set_fan_auto(fan_no)
        except EcTimeout as exc:
            self.raw_ok = False
            self.raw_error = str(exc)
            self.stats["fail"] += 1
            raise
        self.manual[fan_no] = False


class Backend:
    def __init__(self, kind: str, bus, info: Dict[str, str], vendor: Optional[VendorEC] = None):
        self.kind = kind              # "real" | "sim"
        self.bus = bus
        self.info = info
        self.vendor = vendor
        self.ok = kind == "real"

    @property
    def label(self) -> str:
        return "真实 EC" if self.kind == "real" else "模拟模式"

    def probe_rpm_map(self) -> Dict[int, int]:
        """读出全部 4 组转速寄存器，供诊断页判断实际接的是哪个风扇。"""
        out: Dict[int, int] = {}
        for fan_no in DEFAULT_RPM_MAP:
            try:
                out[fan_no] = self.bus.read_rpm(fan_no)
            except Exception:
                out[fan_no] = -1
        return out

    def probe_detail(self) -> Dict[str, object]:
        """逐风扇自检：厂商通道 / 裸端口通道分别拿到什么，失败原因是什么。"""
        result: Dict[str, object] = {
            "has_vendor": bool(self.vendor and self.vendor.ok),
            "vendor_reason": (self.vendor.reason if self.vendor else "未提供"),
            "source": ("vendor" if (self.vendor and self.vendor.ok) else "raw"),
            "fans": [],
        }
        for fan_no in (1, 2, 3, 4):
            rec: Dict[str, object] = {"fan": fan_no, "regs": None}
            hi, lo = DEFAULT_RPM_MAP.get(fan_no, (0, 0))
            rec["regs"] = f"0x{hi:02X}/0x{lo:02X}"
            if self.vendor and self.vendor.ok:
                t0 = time.perf_counter()
                rec["vendor_raw"] = self.vendor.fan_raw(fan_no)
                rec["vendor_rpm"] = self.vendor.rpm(fan_no)
                rec["vendor_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if self.kind == "real":
                t0 = time.perf_counter()
                try:
                    rec["raw_rpm"] = self.bus.raw.read_rpm(fan_no) if hasattr(self.bus, "raw") else -1
                    rec["raw_error"] = ""
                except Exception as exc:  # noqa: BLE001
                    rec["raw_rpm"] = -1
                    rec["raw_error"] = f"{type(exc).__name__}: {exc}"
                rec["raw_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            result["fans"].append(rec)
        return result


def _exe_dir() -> Optional[str]:
    """打包成 exe 后，允许用户把新版驱动放在 exe 旁边覆盖内置的。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return None


def _driver_dirs(root: str) -> List[str]:
    out: List[str] = []
    exe_dir = _exe_dir()
    if exe_dir:
        out.append(os.path.join(exe_dir, "drivers"))
    out += [
        os.path.join(root, "drivers"),
        os.path.join(root, "..", "驱动程序_含说明", "驱动程序_含说明"),
        os.path.join(root, "..", "驱动程序_含说明"),
    ]
    return out


def _find_driver(root: str) -> Optional[str]:
    for d in _driver_dirs(root):
        p = os.path.join(d, "Ntport.dll")
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def _find_vendor_dll(root: str) -> Optional[str]:
    for d in _driver_dirs(root):
        p = os.path.join(d, "ClevoEcInfo.dll")
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def _open_vendor(root: str) -> Optional[VendorEC]:
    path = _find_vendor_dll(root)
    if not path:
        vendor = VendorEC("")
        vendor.reason = "未找到 ClevoEcInfo.dll"
        return vendor
    return VendorEC(os.path.dirname(path)).open()


class _RawStub:
    """厂商 DLL 可用时的空裸端口：所有裸访问直接判失败。

    以前只要裸端口探测慢一点（NTPORT 驱动被卡过之后很常见），整个后端就
    退化成模拟模式，界面看起来在管风扇、其实什么都没写。有厂商 DLL 就够用了，
    裸端口只是兜底，探不通不该拖垮主功能。
    """

    def __getattr__(self, name):
        if name == "rpm_map":
            return {}
        def _fail(*_a, **_k):
            raise EcTimeout("裸端口未启用（仅使用厂商 DLL）")

        return _fail


def make_sim_backend(reason: str, info_extra: Optional[Dict[str, str]] = None) -> Backend:
    """显式构造一个模拟后端（用于端口驱动卡死、探测超时等场景）。"""
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    info: Dict[str, str] = {
        "runtime": f"Python {sys.version.split()[0]}（{bits} 位）",
        "driver": "未使用（端口无响应）",
        "fallback": reason,
    }
    if info_extra:
        info.update(info_extra)
    return Backend("sim", SimBus(), info)


def create_backend(root: str, allow_sim: bool = True) -> Backend:
    info: Dict[str, str] = {}
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    info["runtime"] = f"Python {sys.version.split()[0]}（{bits} 位）"
    reason = ""

    dll = _find_driver(root)
    if bits == 64:
        info["driver"] = "不可用（64 位解释器）"
        reason = "EC 驱动是 32 位库，必须用 32 位运行时启动（请双击启动程序）"
    elif not dll:
        info["driver"] = "未找到 Ntport.dll"
        reason = "缺少 NTPORT 驱动库，请先在诊断页点『安装 / 修复驱动』"
    else:
        vendor = _open_vendor(root)
        info["vendor_dll"] = vendor.reason
        # 先试厂商 DLL：快（<1 秒）且不碰裸端口，不会把端口驱动拖住
        if getattr(vendor, "ok", False):
            try:
                count = vendor.fan_count() or 2
                if 1 <= count <= 4:
                    bus = HybridBus(_RawStub(), vendor)
                    bus.raw_ok = False
                    bus.raw_error = "仅使用厂商 DLL（裸端口未探测）"
                    info["driver"] = "厂商 DLL"
                    info["fan_count"] = str(count)
                    info["sensor_path"] = "厂商 DLL"
                    try:
                        info["ec_version"] = bus.ec_version()
                    except EcTimeout:
                        info["ec_version"] = "读取超时"
                    return Backend("real", bus, info, vendor)
            except Exception as exc:  # noqa: BLE001
                info["vendor_dll"] = f"尝试失败：{exc}"
        try:
            io = NtPortIO(dll)
            raw = EcBus(io)
            bus = HybridBus(raw, vendor)
            count = bus.fan_count()
            if 1 <= count <= 4:
                info["driver"] = f"正常（{os.path.basename(dll)}）"
                info["fan_count"] = str(count)
                info["sensor_path"] = "厂商 DLL" if bus.vendor else "裸端口协议"
                try:
                    info["ec_version"] = bus.ec_version()
                except EcTimeout:
                    info["ec_version"] = "读取超时"
                return Backend("real", bus, info, vendor)
            reason = f"EC 无响应（风扇数量读到 {count}）"
            info["driver"] = reason
        except (PortIOError, EcTimeout, OSError) as exc:
            reason = str(exc)
            info["driver"] = f"失败：{reason}"
        except Exception as exc:  # noqa: BLE001 端口访问可能抛任意异常
            reason = f"{type(exc).__name__}: {exc}"
            info["driver"] = f"失败：{reason}"

    info["fallback"] = reason
    if not allow_sim:
        raise RuntimeError(reason)
    return Backend("sim", SimBus(), info)
