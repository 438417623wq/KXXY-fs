# -*- coding: utf-8 -*-
"""系统级信息：CPU 占用、内存占用、运行时长（全部走 Win32 API，不装三方库）。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def value(self) -> int:
        return (self.high << 32) | self.low


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SystemSampler:
    def __init__(self):
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._prev_idle = 0
        self._prev_kernel = 0
        self._prev_user = 0
        self.cpu = 0.0
        self.mem = 0.0
        self.mem_used_gb = 0.0
        self.mem_total_gb = 0.0
        self._sample_cpu()  # 丢掉第一帧

    def _sample_cpu(self) -> float:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not self._k32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return self.cpu
        i, k, u = idle.value, kernel.value, user.value
        di, dk, du = i - self._prev_idle, k - self._prev_kernel, u - self._prev_user
        self._prev_idle, self._prev_kernel, self._prev_user = i, k, u
        total = dk + du
        if total <= 0:
            return self.cpu
        self.cpu = max(0.0, min(100.0, (total - di) * 100.0 / total))
        return self.cpu

    def _sample_mem(self) -> None:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if self._k32.GlobalMemoryStatusEx(ctypes.byref(st)):
            self.mem = float(st.dwMemoryLoad)
            self.mem_total_gb = st.ullTotalPhys / (1024 ** 3)
            self.mem_used_gb = (st.ullTotalPhys - st.ullAvailPhys) / (1024 ** 3)

    def sample(self) -> dict:
        self._sample_cpu()
        self._sample_mem()
        return {
            "cpu_load": round(self.cpu, 1),
            "mem_load": round(self.mem, 1),
            "mem_used": round(self.mem_used_gb, 1),
            "mem_total": round(self.mem_total_gb, 1),
            "cores": os.cpu_count() or 0,
        }
