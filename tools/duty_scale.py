# -*- coding: utf-8 -*-
"""摸清占空比的取值范围：分别写 0-255 的几个档位，看转速如何变化。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from hardware.vendor import VendorEC  # noqa: E402

v = VendorEC(os.path.join(ROOT, "drivers")).open()
print("vendor:", v.reason, flush=True)


def show(tag):
    d = v.fan_raw(1) or {}
    print(f"  {tag:22s} temp={d.get('temp'):>3} 0x9E_b1={d.get('b1'):>3} rpm={v.rpm(1)}", flush=True)


try:
    show("auto 基线")
    for duty in (64, 128, 192, 255):
        print(f"\n== 目标 {duty}/255 ({duty * 100 // 255}%) ==", flush=True)
        for i in range(10):
            v.set_duty(1, duty)
            time.sleep(0.5)
        show(f"{duty} 保持 5s")
    print("\n== 回到自动 ==", flush=True)
finally:
    v.set_auto(1)
    time.sleep(3)
    show("auto")
