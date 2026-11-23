# -*- coding: utf-8 -*-
"""EC RAM 全表转储（只读），给性能模式寄存器定位用。

用法（必须 32 位 Python + 已装 NTPORT 驱动）：
    python tools/pm_dump.py out.json
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CMD, DATA = 0x66, 0x62


def main():
    import ctypes

    drv = os.path.join(ROOT, "drivers")
    np = ctypes.WinDLL(os.path.join(drv, "Ntport.dll"))
    np.Inp.argtypes = [ctypes.c_short]
    np.Inp.restype = ctypes.c_ubyte
    np.Outp.argtypes = [ctypes.c_short, ctypes.c_short]
    np.Outp.restype = None

    def ibf(t=0.05):
        end = time.perf_counter() + t
        while np.Inp(CMD) & 0x02:
            if time.perf_counter() > end:
                return False
            time.sleep(0.0003)
        return True

    def obf(t=0.05):
        end = time.perf_counter() + t
        while not (np.Inp(CMD) & 0x01):
            if time.perf_counter() > end:
                return False
            time.sleep(0.0003)
        return True

    def ram(addr):
        for _ in range(2):
            ibf()
            np.Outp(CMD, 0x80)
            ibf()
            np.Outp(DATA, addr & 0xFF)
            if obf():
                return np.Inp(DATA)
        return -1

    out = {}
    for a in range(0x100):
        out["%02X" % a] = ram(a)
        time.sleep(0.0008)

    dll = ctypes.CDLL(os.path.join(drv, "ClevoEcInfo.dll"))
    dll.InitIo.restype = ctypes.c_int
    dll.GetCpuFanRpm.restype = ctypes.c_int
    dll.GetGpuFanRpm.restype = ctypes.c_int
    dll.GetTempFanDuty.argtypes = [ctypes.c_int]
    dll.GetTempFanDuty.restype = ctypes.c_int
    dll.InitIo()
    out["_rpm1"] = int(dll.GetCpuFanRpm()) & 0xFFFF
    out["_rpm2"] = int(dll.GetGpuFanRpm()) & 0xFFFF
    out["_temp1"] = int(dll.GetTempFanDuty(1)) & 0xFF

    path = sys.argv[1] if len(sys.argv) > 1 else "dump.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print("saved", path, "rpm", out["_rpm1"], out["_rpm2"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
