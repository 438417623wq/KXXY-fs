# -*- coding: utf-8 -*-
"""Clevo / 蓝天 EC 协议实现。

协议来自对本机 ClevoEcInfo.dll 的逆向，与 tuxedo-fan-control / ClevoECView
的实现一致：

    命令端口 0x66，数据端口 0x62
    bit1 = IBF（输入缓冲满），bit0 = OBF（输出缓冲有数据）

    读 EC RAM : 0x66 <- 0x80 ; 0x62 <- 地址 ; 读 0x62
    写 EC RAM : 0x66 <- 0x81 ; 0x62 <- 地址 ; 0x62 <- 数据
    温度      : 0x66 <- 0x9E ; 0x62 <- 风扇序号 ; 连读 3 字节，首字节为摄氏温度
    手动转速  : 0x66 <- 0x99 ; 0x62 <- 风扇序号 ; 0x62 <- 占空比(0-100)
    交还自动  : 0x66 <- 0x99 ; 0x62 <- 0xFF     ; 0x62 <- 风扇序号

已确认的 EC RAM 地址：
    0xC8      风扇数量
    0xD0/0xD1 CPU 风扇转速（高字节在前）
    0xD2/0xD3 第二风扇
    0xD4/0xD5 GPU 风扇转速
    0xD8/0xD9 部分机型的第三风扇

注意：本模块提供 write_ram / set_fan_duty 等写操作，调用方必须先经过
safety 的日志与快照，否则不允许直接调用（见 control/safety.py 的守卫）。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

EC_CMD_PORT = 0x66
EC_DATA_PORT = 0x62

CMD_READ_RAM = 0x80
CMD_WRITE_RAM = 0x81
CMD_TEMP_FAN = 0x9E
CMD_FAN_DUTY = 0x99
CMD_EC_VERSION = 0x93
CMD_OPTION = 0x9A

REG_FAN_COUNT = 0xC8

# 风扇编号（EC 内部编号，1 起）→ 转速寄存器（高字节, 低字节）
# 顺序以厂商 ClevoEcInfo.dll 为准（GetCpuFanRpm / GetGpuFanRpm / GetGpu1FanRpm）：
#   1 = CPU → 0xD0/0xD1
#   2 = GPU → 0xD2/0xD3      ← 之前写成 0xD4/0xD5，导致 GPU 转速读不到
#   3 = 第二颗 GPU → 0xD4/0xD5
#   4 = 部分机型的第四风扇 → 0xD8/0xD9
DEFAULT_RPM_MAP = {
    1: (0xD0, 0xD1),
    2: (0xD2, 0xD3),
    3: (0xD4, 0xD5),
    4: (0xD8, 0xD9),
}
RPM_REGS = DEFAULT_RPM_MAP


class EcTimeout(RuntimeError):
    """EC 没有在规定时间内响应，通常意味着驱动没装或被其它软件占用。"""


class EcBus:
    """线程不安全的 EC 总线；外部必须串行调用（engine 用单线程轮询）。"""

    def __init__(self, port_io, min_gap: float = 0.002, rpm_map: Optional[Dict[int, tuple]] = None):
        self.io = port_io
        self._min_gap = min_gap
        self._last = 0.0
        self.rpm_map = dict(rpm_map or DEFAULT_RPM_MAP)

    # ---------- 基础时序 ----------
    def _pace(self) -> None:
        now = time.perf_counter()
        wait = self._min_gap - (now - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.perf_counter()

    @staticmethod
    def _deadline(timeout: float) -> float:
        return time.perf_counter() + timeout

    def _wait_ibf_clear(self, timeout: float = 0.25) -> None:
        end = self._deadline(timeout)
        while self.io.inp(EC_CMD_PORT) & 0x02:
            if time.perf_counter() > end:
                raise EcTimeout("EC 忙（IBF 未清）")
            time.sleep(0.0002)

    def _wait_obf_set(self, timeout: float = 0.25) -> None:
        # 厂商 DLL 这里是 65535 次空转、永不放弃；0.05s 对慢一点的
        # EC（或驱动 IOCTL 有开销时）太紧，会误判成"读不到"。
        end = self._deadline(timeout)
        while not (self.io.inp(EC_CMD_PORT) & 0x01):
            if time.perf_counter() > end:
                raise EcTimeout("EC 无输出（OBF 未置位）")
            time.sleep(0.0002)

    def _command(self, cmd: int) -> None:
        self._wait_ibf_clear()
        self.io.outp(EC_CMD_PORT, cmd)

    def _data(self, value: int) -> None:
        self._wait_ibf_clear()
        self.io.outp(EC_DATA_PORT, value)

    def _read(self) -> int:
        self._wait_obf_set()
        return self.io.inp(EC_DATA_PORT)

    # ---------- 读 ----------
    def read_ram(self, addr: int) -> int:
        """读一个 EC RAM 字节；失败重试一次（可能是被别家软件插了一拍）。"""
        last: Optional[Exception] = None
        for attempt in range(2):
            try:
                self._pace()
                self._command(CMD_READ_RAM)
                self._data(addr & 0xFF)
                return self._read()
            except EcTimeout as exc:
                last = exc
                time.sleep(0.005)
        raise last  # type: ignore[misc]

    def read_block(self, start: int, count: int) -> List[Optional[int]]:
        out: List[Optional[int]] = []
        for i in range(count):
            try:
                out.append(self.read_ram((start + i) & 0xFF))
            except EcTimeout:
                out.append(None)
        return out

    def read_temp_raw(self, fan_index: int) -> tuple:
        """命令 0x9E：一次返回 3 个字节。

        厂商 DLL 的 GetTempFanDuty 把这三个字节打包成
        (b3<<16)|(b2<<8)|b1 返回，b1 就是摄氏温度。中间那个字节是
        固件当前使用的占空比——这也是自动模式下能显示真实 % 的来源。
        """
        self._pace()
        self._command(CMD_TEMP_FAN)
        self._data(fan_index)
        b1 = self._read()          # 温度
        b2 = self._read()          # 固件当前占空比
        b3 = self._read()          # 保留 / 目标值
        return b1, b2, b3

    def read_temp(self, fan_index: int) -> int:
        """返回该风扇对应的传感器温度（摄氏度）。"""
        return self.read_temp_raw(fan_index)[0]

    def read_rpm(self, fan_no: int) -> int:
        hi_reg, lo_reg = self.rpm_map.get(fan_no, (0, 0))
        if not hi_reg:
            return 0
        hi = self.read_ram(hi_reg)
        lo = self.read_ram(lo_reg)
        return (hi << 8) | lo

    def fan_count(self) -> int:
        return self.read_ram(REG_FAN_COUNT)

    def ec_version(self) -> str:
        self._pace()
        self._command(CMD_EC_VERSION)
        chars = []
        for _ in range(0x1E):
            ch = self._read()
            if ch == 0x24:  # '$' 结束符
                break
            chars.append(chr(ch) if 32 <= ch < 127 else "?")
        return "".join(chars).strip()

    # ---------- 写（务必经由 safety 守卫） ----------
    def write_ram(self, addr: int, value: int) -> None:
        self._pace()
        self._command(CMD_WRITE_RAM)
        self._data(addr & 0xFF)
        self._data(value & 0xFF)

    def set_fan_duty(self, fan_no: int, percent: int) -> None:
        # 0x99 是 8 位占空比：0-254 有效，255 保留给「交还固件」。
        pct = max(0, min(254, int(percent)))
        self._pace()
        self._command(CMD_FAN_DUTY)
        self._data(fan_no)
        self._data(pct)

    def set_fan_auto(self, fan_no: int) -> None:
        self._pace()
        self._command(CMD_FAN_DUTY)
        self._data(0xFF)
        self._data(fan_no)

    # ---------- 组合 ----------
    def snapshot(self, addrs) -> Dict[int, Optional[int]]:
        return {a: (lambda v: v)(_safe(self.read_ram, a)) for a in addrs}

    def restore(self, snap: Dict[int, Optional[int]], only_changed: bool = True) -> int:
        """把快照写回去，返回实际写回的寄存器数量。"""
        n = 0
        for addr, value in snap.items():
            if value is None:
                continue
            if only_changed:
                try:
                    if self.read_ram(addr) == value:
                        continue
                except EcTimeout:
                    pass
            self.write_ram(addr, value)
            n += 1
        return n


def _safe(fn, *args):
    try:
        return fn(*args)
    except EcTimeout:
        return None
