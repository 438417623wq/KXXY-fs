# -*- coding: utf-8 -*-
"""
EC 只读探针 —— 仅做读取，绝不写入。
用途：验证 32 位运行时 + NTPORT 驱动 + EC 协议链路是否通畅，
     并抓取 EC RAM 全表，用于比对温度/转速/键盘背光寄存器。

运行：runtime\py32\python.exe tools\ec_probe.py
"""
import ctypes
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

EC_CMD_PORT = 0x66
EC_DATA_PORT = 0x62

CMD_READ_RAM = 0x80
CMD_FAN_DUTY = 0x99
CMD_TEMP = 0x9E
CMD_EC_VERSION = 0x93
CMD_FAN_COUNT_OPT = 0x9A


class NtPort:
    """NTPORT 驱动封装（需已安装 ZNTPORT.SYS）"""

    def __init__(self, dll_path):
        self.dll = ctypes.WinDLL(dll_path)
        self.Inp = self.dll.Inp
        self.Inp.argtypes = [ctypes.c_short]
        self.Inp.restype = ctypes.c_ubyte
        self.Outp = self.dll.Outp
        self.Outp.argtypes = [ctypes.c_short, ctypes.c_short]
        self.Outp.restype = None

    def inp(self, port):
        return self.Inp(port)

    def outp(self, port, value):
        self.Outp(port, value)


class Ec:
    def __init__(self, ntport):
        self.np = ntport

    def _wait_ibf_clear(self, timeout=0.02):
        t0 = time.perf_counter()
        while self.np.inp(EC_CMD_PORT) & 0x02:
            if time.perf_counter() - t0 > timeout:
                return False
            time.sleep(0.0005)
        return True

    def _wait_obf_set(self, timeout=0.02):
        t0 = time.perf_counter()
        while not (self.np.inp(EC_CMD_PORT) & 0x01):
            if time.perf_counter() - t0 > timeout:
                return False
            time.sleep(0.0005)
        return True

    def read_ram(self, addr):
        if not self._wait_ibf_clear():
            return None
        self.np.outp(EC_CMD_PORT, CMD_READ_RAM)
        if not self._wait_ibf_clear():
            return None
        self.np.outp(EC_DATA_PORT, addr)
        if not self._wait_obf_set():
            return None
        return self.np.inp(EC_DATA_PORT)

    def read_temp(self, index):
        """对应 ClevoEcInfo.GetTempFanDuty(index)，返回 24bit，低字节是温度。"""
        if not self._wait_ibf_clear():
            return None
        self.np.outp(EC_CMD_PORT, CMD_TEMP)
        if not self._wait_ibf_clear():
            return None
        self.np.outp(EC_DATA_PORT, index)
        out = []
        for _ in range(3):
            if not self._wait_obf_set():
                return None
            out.append(self.np.inp(EC_DATA_PORT))
        return out[0] | (out[1] << 8) | (out[2] << 16)

    def read_block(self, addr, count):
        return [self.read_ram((addr + i) & 0xFF) for i in range(count)]


def main():
    drv_dir = os.path.join(ROOT, "..", "驱动程序_含说明", "驱动程序_含说明")
    ntport_dll = os.path.join(drv_dir, "Ntport.dll")
    clevo_dll = os.path.join(ROOT, "..", "Brz蓝天风扇控制_2022-9-15", "MyEcFanCtrl", "ClevoEcInfo.dll")

    print("=" * 60)
    print("EC 只读探针 (READ-ONLY)")
    print("python:", sys.version.split()[0], sys.maxsize > 2**32 and "64bit" or "32bit")
    print("=" * 60)

    if not os.path.exists(ntport_dll):
        print("[X] 找不到 Ntport.dll:", ntport_dll)
        return 1
    try:
        np_ = NtPort(ntport_dll)
        print("[OK] Ntport.dll 已加载")
    except OSError as e:
        print("[X] Ntport.dll 加载失败:", e)
        return 1

    ec = Ec(np_)
    v = ec.read_ram(0xC8)
    print("[..] EC RAM[0xC8] 风扇数量 =", v)
    if v is None:
        print("[X] EC 无响应，检查驱动 / 端口")
        return 1

    print("\n-- 温度 (cmd 0x9E) --")
    for i in (1, 2, 3, 4):
        r = ec.read_temp(i)
        if r is None:
            print(f"  idx{i}: 无响应")
        else:
            print(f"  idx{i}: raw=0x{r:06X}  temp={r & 0xFF}°C  b1={(r >> 8) & 0xFF}  b2={(r >> 16) & 0xFF}")

    print("\n-- 风扇转速 (EC RAM) --")
    def rpm(a):
        hi = ec.read_ram(a)
        lo = ec.read_ram(a + 1)
        if hi is None or lo is None:
            return None
        return (hi << 8) | lo
    for name, a in (("CPU 0xD0", 0xD0), ("?  0xD2", 0xD2), ("GPU 0xD4", 0xD4), ("?  0xD8", 0xD8)):
        print(f"  {name}: {rpm(a)}")

    print("\n-- 键盘背光候选：EC RAM 0xD6..0xDF --")
    block = ec.read_block(0xD0, 16)
    for i, b in enumerate(block):
        print(f"  0x{0xD0 + i:02X} = {b if b is not None else '--'}")

    print("\n-- ClevoEcInfo.dll 交叉验证 --")
    if os.path.exists(clevo_dll):
        try:
            dll = ctypes.WinDLL(clevo_dll)
            dll.InitIo.restype = ctypes.c_int
            print("  InitIo ->", dll.InitIo())
            dll.GetFanCount.restype = ctypes.c_int
            print("  GetFanCount ->", dll.GetFanCount())
            for fn in ("GetCpuFanRpm", "GetGpuFanRpm", "GetGpu1FanRpm"):
                f = getattr(dll, fn, None)
                if f:
                    f.restype = ctypes.c_int
                    print(f"  {fn} -> {f()}")
            f = getattr(dll, "GetTempFanDuty", None)
            if f:
                f.restype = ctypes.c_int
                f.argtypes = [ctypes.c_int]
                for i in (1, 2, 3, 4):
                    print(f"  GetTempFanDuty({i}) -> 0x{f(i):06X} temp={f(i) & 0xFF}")
        except Exception as e:
            print("  [X]", e)
    else:
        print("  跳过（未找到 DLL）")

    print("\n-- EC RAM 全表快照 (0x00-0xFF) --")
    full = ec.read_block(0x00, 0x100)
    for row in range(0, 0x100, 16):
        cells = " ".join(
            f"{full[row + c]:02X}" if full[row + c] is not None else "--" for c in range(16)
        )
        print(f"  {row:02X}: {cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
