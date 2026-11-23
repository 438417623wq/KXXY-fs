# -*- coding: utf-8 -*-
"""直接验证 DCHU 桥的性能模式命令（不经过界面）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from hardware.dchu_bridge import DchuBridge  # noqa: E402


def main():
    mode = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    br = DchuBridge(log=lambda m: print("[log]", m))
    print("dll:", br.dll_path or "(未找到)")
    rc = br.set_perf_mode(mode)
    print("set_perf_mode(%d) -> %r  reason=%r" % (mode, rc, br.reason))
    br.stop()
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
