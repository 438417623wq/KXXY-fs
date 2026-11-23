# -*- coding: utf-8 -*-
"""紧急还原：不打开界面，直接把风扇交还固件自动 / 恢复写过的寄存器。

用法：
    runtime\py32\python.exe tools\restore.py
    或双击「还原并退出.bat」
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from control.safety import SessionGuard           # noqa: E402
from hardware.backend import create_backend       # noqa: E402
from store import Store                           # noqa: E402


def main() -> int:
    print("")
    print("  EC Pilot 紧急还原")
    print("  " + "-" * 40)
    store = Store(os.path.join(ROOT, "data", "config.json"))
    try:
        backend = create_backend(ROOT, allow_sim=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  [X] 无法连接 EC：{exc}")
        print("      请确认已安装 NTPORT 驱动，并用 32 位运行时执行。")
        return 2
    print(f"  后端：{backend.label}")

    guard = SessionGuard(os.path.join(ROOT, "data"), store.data)
    guard.bind(backend.bus)
    print(f"  会话开始时：{guard.session_started}")
    print(f"  记录的寄存器：{guard.ram_touched or '无'}")

    res = guard.restore_all()
    for item in res.get("done") or []:
        print("  [OK]", item)
    for err in res.get("errors") or []:
        print("  [!!]", err)

    for fan in store.data["fans"].values():
        fan["mode"] = "auto"
    store.save()
    print("\n  完成。所有风扇已交还固件自动控制。")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
