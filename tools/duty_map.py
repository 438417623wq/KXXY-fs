# -*- coding: utf-8 -*-
"""标定占空比与转速的对应关系（期望：值越小转速越高）。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from hardware.vendor import VendorEC  # noqa: E402

v = VendorEC(os.path.join(ROOT, "drivers")).open()
print("vendor:", v.reason, flush=True)

rows = []
try:
    for duty in (0, 20, 40, 60, 80, 100):
        for _ in range(6):
            v.set_duty(1, duty)
            time.sleep(0.6)
        d = v.fan_raw(1) or {}
        rpms = [v.rpm(1) for _ in range(3)]
        rows.append((duty, d.get("b1"), d.get("b2"), rpms))
        print(f"  duty={duty:>3} b1={d.get('b1'):>3} b2={d.get('b2'):>3} rpm={rpms}", flush=True)
finally:
    v.set_auto(1)
    time.sleep(2)
    d = v.fan_raw(1) or {}
    print(f"\n  auto      b1={d.get('b1')} b2={d.get('b2')} rpm={v.rpm(1)}", flush=True)
print("\n映射汇总:")
for duty, b1, b2, rpms in rows:
    print(f"  {duty:>3} -> {sum(rpms) // len(rpms):>5} RPM")
