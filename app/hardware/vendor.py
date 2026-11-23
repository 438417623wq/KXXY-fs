# -*- coding: utf-8 -*-
"""厂商 DLL（ClevoEcInfo.dll）桥接层。

为什么要这一层
--------------
自己按协议裸读写 EC 时，只要时序上差一点（EC 响应慢、被别的软件占用），
等待 OBF 超时就会抛异常，界面上只能显示 "--"。而厂商这个 DLL 里有本机
模具对应的**正确寄存器表**和**无限重试的握手循环**，它不会中途放弃。

逆向结论（与 ClevoEcInfo.dll 的导出函数逐条对齐）：

    GetCpuFanRpm     读 0xD0/0xD1  → CPU 风扇转速（高字节在前）
    GetGpuFanRpm     读 0xD2/0xD3  → GPU 风扇转速
    GetGpu1FanRpm    读 0xD4/0xD5  → 第二颗 GPU 风扇（部分机型）
    GetFanCount      读 0xC8       → 风扇数量
    GetTempFanDuty   cmd 0x9E + 序号，连读 3 字节，打包成
                     (b3<<16)|(b2<<8)|b1，b1 = 摄氏温度
    SetFanDuty       cmd 0x99, 序号, 占空比
    SetFanDutyAuto   cmd 0x99, 0xFF, 序号
    InitIo           内部调用 EnablePorts(0, 0xFF) 放开端口 + LicenseInfo

调用约定：这些导出函数结尾都是不带操作数的 `ret`（由调用方清栈），
所以必须用 CDLL(cdecl)；用 WinDLL(stdcall) 会让每次调用都少弹栈参数，
跑久了直接把进程栈吃掉。

注意：本模块只做"转发"，不做任何安全判断。所有写操作仍然必须经过
control/safety.py 的记账与回滚流程。
"""
from __future__ import annotations

import ctypes
import os
from typing import Dict, Optional


class VendorEC:
    """对 ClevoEcInfo.dll 的薄封装。"""

    #: 风扇序号 → 转速函数名（与 DLL 内部 switch 分支一一对应）
    RPM_FUNCS = {1: "GetCpuFanRpm", 2: "GetGpuFanRpm", 3: "GetGpu1FanRpm"}

    def __init__(self, drivers_dir: str):
        self.ok = False
        self.reason = ""
        self.path = os.path.join(drivers_dir, "ClevoEcInfo.dll")
        self._dll: Optional[ctypes.CDLL] = None

    # ------------------------------------------------------------------ 加载
    def open(self) -> "VendorEC":
        if not os.path.exists(self.path):
            self.reason = "未找到 ClevoEcInfo.dll"
            return self
        try:
            dll = ctypes.CDLL(self.path)
        except OSError as exc:
            self.reason = f"加载失败：{exc}"
            return self
        try:
            dll.InitIo.argtypes = []
            dll.InitIo.restype = ctypes.c_int

            dll.GetTempFanDuty.argtypes = [ctypes.c_int]
            dll.GetTempFanDuty.restype = ctypes.c_int

            for name in ("GetCpuFanRpm", "GetGpuFanRpm", "GetGpu1FanRpm", "GetFanCount"):
                fn = getattr(dll, name)
                fn.argtypes = []
                fn.restype = ctypes.c_int

            dll.GetECVersion.argtypes = []
            dll.GetECVersion.restype = ctypes.c_char_p

            dll.SetFanDuty.argtypes = [ctypes.c_int, ctypes.c_int]
            dll.SetFanDuty.restype = None
            dll.SetFanDutyAuto.argtypes = [ctypes.c_int]
            dll.SetFanDutyAuto.restype = None
        except AttributeError as exc:
            self.reason = f"导出函数缺失：{exc}"
            return self

        # 无参数调用，无论 stdcall/cdecl 都不会弄坏栈
        try:
            dll.InitIo()
        except OSError:
            pass

        self._dll = dll
        self.ok = True
        self.reason = "已就绪"
        return self

    # ------------------------------------------------------------------ 读
    def fan_raw(self, fan_no: int) -> Optional[Dict[str, int]]:
        """返回 0x9E 三连读的原始三字节，失败返回 None。"""
        if not self._dll:
            return None
        try:
            v = int(self._dll.GetTempFanDuty(int(fan_no))) & 0xFFFFFFFF
        except OSError:
            return None
        return {
            "raw": v,
            "temp": v & 0xFF,
            "b1": (v >> 8) & 0xFF,
            "b2": (v >> 16) & 0xFF,
        }

    def temp(self, fan_no: int) -> Optional[int]:
        data = self.fan_raw(fan_no)
        return None if data is None else data["temp"]

    def rpm(self, fan_no: int) -> Optional[int]:
        name = self.RPM_FUNCS.get(int(fan_no))
        if not self._dll or not name:
            return None
        try:
            return int(getattr(self._dll, name)()) & 0xFFFF
        except OSError:
            return None

    def fan_count(self) -> Optional[int]:
        if not self._dll:
            return None
        try:
            return int(self._dll.GetFanCount()) & 0xFF
        except OSError:
            return None

    def ec_version(self) -> Optional[str]:
        if not self._dll:
            return None
        try:
            raw = self._dll.GetECVersion()
        except OSError:
            return None
        if not raw:
            return None
        return raw.decode("latin-1", "ignore").split("\x00")[0].strip()

    # ------------------------------------------------------------------ 写
    def set_duty(self, fan_no: int, percent: int) -> bool:
        """写入占空比。注意这里是 EC 的 8 位量程 0-254，不是 0-100。

        旧版在这里硬编码 min(100, …)，无论上层算出什么都只写到 100，
        等于把满速砍到 39%——风扇听起来像没转。别再往这加钳位。
        """
        if not self._dll:
            return False
        try:
            self._dll.SetFanDuty(int(fan_no), int(max(0, min(254, percent))))
            return True
        except OSError:
            return False

    def set_auto(self, fan_no: int) -> bool:
        if not self._dll:
            return False
        try:
            self._dll.SetFanDutyAuto(int(fan_no))
            return True
        except OSError:
            return False
