# -*- coding: utf-8 -*-
"""真机 EC 链路逐步探测：定位 OBF 超时到底发生在哪一步。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from hardware.ec import EcBus, EcTimeout          # noqa: E402
from hardware.ntport import NtPortIO             # noqa: E402


def p(*a):
    print(*a, flush=True)


io = NtPortIO(os.path.join(ROOT, "drivers", "Ntport.dll"))
bus = EcBus(io)
p("status idle:", [hex(io.inp(0x66)) for _ in range(4)])

p("\n-- step by step (0xD0) --")
bus._wait_ibf_clear()
io.outp(0x66, 0x80)
p("  after cmd 0x80 status:", hex(io.inp(0x66)))
bus._pace()
bus._wait_ibf_clear()
io.outp(0x62, 0xD0)
p("  after addr status:", hex(io.inp(0x66)))
n = 0
t0 = time.perf_counter()
while not (io.inp(0x66) & 0x01) and n < 5000:
    n += 1
    time.sleep(0.0002)
p(f"  obf spins={n} elapsed={time.perf_counter() - t0:.4f}s status={hex(io.inp(0x66))}")
p("  data =", io.inp(0x62))

p("\n-- tight spin (no sleep) --")
bus._wait_ibf_clear()
io.outp(0x66, 0x80)
bus._wait_ibf_clear()
io.outp(0x62, 0xD0)
n = 0
while not (io.inp(0x66) & 0x01) and n < 20000:
    n += 1
p(f"  spins={n} status={hex(io.inp(0x66))} data={io.inp(0x62)}")

p("\n-- EcBus.read_ram --")
for addr in (0xC8, 0xD0, 0xD1, 0xD2, 0xD3):
    try:
        p(f"  0x{addr:02X} = {bus.read_ram(addr)}")
    except EcTimeout as exc:
        p(f"  0x{addr:02X} FAIL {exc}")

p("\n-- read_temp_raw --")
try:
    p("  triple:", bus.read_temp_raw(1))
except EcTimeout as exc:
    p("  FAIL", exc)
