# -*- coding: utf-8 -*-
"""使用项目32位构建环境打包；失败不覆盖成品，不自动删除构建文件。"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY32 = os.path.join(os.path.dirname(ROOT), ".build", "py32build", "python.exe")
WORK = os.path.join(os.path.dirname(ROOT), ".build", "work")


def _count_wx_entries(exe_path):
    # 必须在有PyInstaller的同一构建解释器内读包；读包失败不再放行。
    code = ("import sys;from PyInstaller.archive.readers import CArchiveReader;"
            "r=CArchiveReader(sys.argv[1]);"
            "print(sum(1 for n in r.toc if n.replace(chr(92),'/').lower().startswith('wx/')))")
    check = subprocess.run([PY32, "-c", code, exe_path], capture_output=True, text=True, timeout=300)
    if check.returncode:
        raise RuntimeError("无法验证产物归档：" + check.stderr)
    return int(check.stdout.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=os.path.join(ROOT, "dist", "ECPilot.exe"))
    args = parser.parse_args()
    if not os.path.isfile(PY32):
        print("[X] 找不到32位构建环境：" + PY32)
        return 1
    # 冷启动首次导入 wx 可能被安全软件扫描拖到一分钟以上，超时不能当成环境损坏。
    check = subprocess.run([PY32, "-c", "import struct,PyInstaller,wx;print(struct.calcsize('P')*8,PyInstaller.__version__,wx.__version__)"],
                           capture_output=True, text=True, timeout=300)
    if check.returncode or not check.stdout.startswith("32 "):
        print("[X] 32位/wxPython/PyInstaller预检失败：" + check.stderr)
        return 1
    print("构建环境：" + check.stdout.strip(), flush=True)
    output = os.path.abspath(args.output)
    if not os.path.isdir(os.path.dirname(output)):
        print("[X] 输出目录不存在：" + os.path.dirname(output))
        return 1
    os.makedirs(WORK, exist_ok=True)
    stage = tempfile.mkdtemp(prefix="ecpilot-", dir=WORK)
    code = subprocess.run([PY32, "-m", "PyInstaller", os.path.join(ROOT, "ecpilot.spec"),
                           "--noconfirm", "--distpath", os.path.join(stage, "dist"),
                           "--workpath", os.path.join(stage, "build")], cwd=ROOT).returncode
    if code:
        return code
    built = os.path.join(stage, "dist", "ECPilot.exe")
    count = _count_wx_entries(built)
    if count < 5:
        print(f"[X] wx扩展数量异常：{count}，不交付该产物")
        return 1
    shutil.copy2(built, output)
    print(f"已生成：{output}；{os.path.getsize(output) / 1048576:.2f} MiB；wx扩展 {count} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
