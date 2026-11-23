# -*- coding: utf-8 -*-
"""EC 差分实验：用厂商 DCHU 调用当"标准答案"，在 EC RAM 里找键盘灯的影子寄存器。

跑法：x64 python（能加载 InsydeDCHU.dll）→ 内部用 py32 子进程读 EC。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY32 = os.path.join(ROOT, "runtime", "py32", "python.exe")

READER = r"""
import sys, json
sys.path.insert(0, r'{root}\app')
from hardware.ntport import NtPortIO
from hardware.ec import EcBus
io = NtPortIO(r'{root}\drivers\Ntport.dll')
bus = EcBus(io)
data = bus.read_block(0x00, 0x100)
json.dump(data, open(r'{out}', 'w'))
"""


def dump_ec(tag: str):
    out = os.path.join(tempfile.gettempdir(), f"ec_{tag}.json")
    src = os.path.join(tempfile.gettempdir(), f"ec_reader_{tag}.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(READER.format(root=ROOT, out=out))
    subprocess.run([PY32, src], check=False, capture_output=True)
    if not os.path.exists(out):
        return []
    with open(out, "r") as fh:
        return json.load(fh)


def main():
    sys.path.insert(0, HERE)
    from kbled_probe import Dchu, find_dll, color_code

    d = Dchu(find_dll())
    snaps = {}
    plan = [
        ("base", None),
        ("red", color_code(255, 0, 0, 0)),
        ("blue", color_code(0, 0, 255, 0)),
        ("green", color_code(0, 255, 0, 0)),
        ("off", 0xE0000007),
        ("on", 0xE007F001),
    ]
    for tag, code in plan:
        if code is not None:
            d.send(code)
            time.sleep(0.35)
        snaps[tag] = dump_ec(tag)
        print(f"[{tag}] 快照 {sum(1 for x in snaps[tag] if x is not None)}/256", flush=True)

    # 找出在 red/blue/green 之间变化的地址
    keys = ["base", "red", "blue", "green", "off", "on"]
    interesting = []
    for addr in range(256):
        vals = [snaps[k][addr] if addr < len(snaps[k]) else None for k in keys]
        if any(v is None for v in vals):
            continue
        if len(set(vals)) > 1:
            interesting.append((addr, vals))
    print("\n变化的地址：")
    for addr, vals in interesting:
        print(f"  0x{addr:02X}: " + " ".join(f"{k}={v:02X}" for k, v in zip(keys, vals)))

    with open(os.path.join(ROOT, "data", "kbled_ec_diff.json"), "w", encoding="utf-8") as fh:
        json.dump({"keys": keys, "rows": [{"addr": a, "vals": v} for a, v in interesting]}, fh, indent=2)
    print(f"\n共 {len(interesting)} 个地址，已写入 data/kbled_ec_diff.json")

    # 收尾：关灯（保持和原状态一致）
    d.send(0xE0000007)


if __name__ == "__main__":
    main()
