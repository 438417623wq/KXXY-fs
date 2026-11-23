# -*- coding: utf-8 -*-
"""NTPORT 驱动封装：提供 inp / outp 原语。

ZNTPORT.SYS 是一个内核级 I/O 端口驱动（随机型驱动包里自带）。
本模块只负责最底层的端口读写，不做任何安全判断——所有写入必须经过
control/safety.py 的日志与回滚流程。
"""
from __future__ import annotations

import ctypes
import os

_DLL_NAMES = ("Ntport.dll", "NTPORT.DLL")


class PortIOError(RuntimeError):
    pass


class NtPortIO:
    """对 NTPORT 的极薄封装。"""

    def __init__(self, dll_path: str):
        if not os.path.exists(dll_path):
            raise PortIOError(f"找不到驱动库：{dll_path}")
        try:
            self._dll = ctypes.WinDLL(dll_path)
        except OSError as exc:  # 32/64 位不匹配或依赖缺失
            raise PortIOError(f"加载驱动库失败：{exc}") from exc

        # 已确认导出函数是 stdcall（Outp 结尾为 ret 8，参数走栈）
        self._inp = self._dll.Inp
        self._inp.argtypes = [ctypes.c_int]
        self._inp.restype = ctypes.c_int

        self._outp = self._dll.Outp
        self._outp.argtypes = [ctypes.c_int, ctypes.c_int]
        self._outp.restype = None

    def inp(self, port: int) -> int:
        return int(self._inp(port)) & 0xFF

    def outp(self, port: int, value: int) -> None:
        self._outp(port, value & 0xFF)

    def self_test(self) -> bool:
        """端口可读且返回值稳定，说明驱动正常工作。"""
        try:
            a = self.inp(0x66)
            b = self.inp(0x66)
            # 端口被正常接管时状态寄存器只有低 2 位会跳变；恒为 0xFF 说明没接管
            return a != 0xFF and b != 0xFF
        except Exception:
            return False
