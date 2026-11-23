# -*- coding: utf-8 -*-
"""键盘背光探针：验证 InsydeDCHU / Clevo WMI 两条链路能不能真的点亮键盘。

用法（需要管理员权限）：
    python tools/kbled_probe.py            # 探测能力，不写硬件
    python tools/kbled_probe.py --red      # 写红色（肉眼确认用）
    python tools/kbled_probe.py --off      # 关灯
    python tools/kbled_probe.py --dim 2    # 亮度档位 1..4

协议来源（三处独立实现互相印证）：
  * rzrwolf/LED-manager-for-Clevo  -> WMI CLEVO_GET.SetKBLED(Data=uint32)
  * DeviceIoControl/CppKeyboardColour -> InsydeDCHU.SetDCHU_Data(0x67, &code, 4)
  * xuha233/ClevoRGBControl -> SetDCHU_Data(0x67, (zone<<24)|color, 4)
两边用的是同一套 32 位灯效码，只是投递方式不同。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time

DLL_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "drivers", "InsydeDCHU.dll"),
    r"C:\Program Files (x86)\ControlCenter\InsydeDCHU.dll",
    r"C:\Program Files\ControlCenter\InsydeDCHU.dll",
    r"C:\Program Files\ClevoRGBControl\Service\InsydeDCHU.dll",
    r"C:\Program Files\ClevoRGBControl\Experimental\InsydeDCHU.dll",
]


def find_dll() -> str:
    for p in DLL_CANDIDATES:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            return p
    return ""


def color_code(r: int, g: int, b: int, zone: int = 0) -> int:
    """Clevo 灯效码：0xF<zone> 高半字节 + B<<16 | R<<8 | G"""
    code = ((b & 0xFF) << 16) | ((r & 0xFF) << 8) | (g & 0xFF)
    if zone == 0:
        return code
    return (0xF0 + zone - 1) << 24 | code


class Dchu:
    def __init__(self, dll_path: str):
        self.dll_path = dll_path
        self.dll = ctypes.WinDLL(dll_path)
        self.set_data = getattr(self.dll, "SetDCHU_Data", None)
        if self.set_data:
            self.set_data.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
            self.set_data.restype = ctypes.c_int
        self.write_app = getattr(self.dll, "WriteAppSettings", None)
        if self.write_app:
            self.write_app.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte)]
            self.write_app.restype = ctypes.c_int
        self.get_int = getattr(self.dll, "GetDCHU_Data_Integer", None)

    def send(self, code: int, cmd: int = 0x67) -> int:
        buf = (ctypes.c_ubyte * 4).from_buffer_copy((code & 0xFFFFFFFF).to_bytes(4, "little"))
        rc = self.set_data(cmd, buf, 4)
        return int(rc)

    def write_color_app(self, r: int, g: int, b: int, mode: int = 8) -> tuple:
        rc1 = rc2 = -1
        if self.write_app:
            rgb = (ctypes.c_ubyte * 3)(r, g, b)
            rc1 = int(self.write_app(2, 0x51, 3, rgb))
            one = (ctypes.c_ubyte * 1)(mode)
            rc2 = int(self.write_app(2, 0x20, 1, one))
        return rc1, rc2


def probe() -> int:
    print("=" * 62)
    print("键盘背光链路探测")
    print("=" * 62)
    dll = find_dll()
    print(f"[DLL] {dll or '未找到 InsydeDCHU.dll'}")
    if dll:
        try:
            d = Dchu(dll)
            print(f"[导出] SetDCHU_Data={'有' if d.set_data else '无'}  "
                  f"WriteAppSettings={'有' if d.write_app else '无'}  "
                  f"GetDCHU_Data_Integer={'有' if d.get_int else '无'}")
        except OSError as exc:
            print(f"[失败] 加载 DLL 出错：{exc}")

    # WMI CLEVO_GET（三区机型）
    try:
        out = os.popen('powershell -NoProfile -Command "try{Get-CimClass -Namespace root\\WMI -ClassName CLEVO_GET -EA Stop|Out-Null;\'yes\'}catch{\'no\'}"').read().strip()
        print(f"[WMI ] CLEVO_GET 类：{out}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WMI ] 检测失败：{exc}")

    # 谁在占用
    try:
        out = os.popen('tasklist /FI "IMAGENAME eq ColorfulLedKeyboard.Service.exe" /FO CSV /NH').read().strip()
        print(f"[占用] ColorfulLedKeyboard.Service: {'运行中' if 'Colorful' in out else '未运行'}")
        out = os.popen('tasklist /FI "IMAGENAME eq ColorfulLedKeyboard.Tray.exe" /FO CSV /NH').read().strip()
        print(f"[占用] ColorfulLedKeyboard.Tray   : {'运行中' if 'Colorful' in out else '未运行'}")
    except Exception:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--red", action="store_true")
    ap.add_argument("--white", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--dim", type=int, default=0)
    ap.add_argument("--zone", type=int, default=0, help="0=单区直写, 1..3=分区")
    ap.add_argument("--hold", type=float, default=0.0)
    args = ap.parse_args()

    dll = find_dll()
    if not dll:
        print("找不到 InsydeDCHU.dll")
        return 2
    d = Dchu(dll)
    if not d.set_data:
        print("DLL 没有导出 SetDCHU_Data")
        return 3

    if args.red or args.white or args.off or args.dim:
        if args.dim:
            code = 0xF4000000 | ((args.dim - 1) * 0x30 + 0x2F)
            print(f"亮度档 {args.dim} -> 0x{code:08X}")
            print("  rc =", d.send(code))
        if args.red:
            code = color_code(255, 0, 0, args.zone)
            print(f"红色 -> 0x{code:08X}  rc = {d.send(code)}")
        if args.white:
            code = color_code(255, 255, 255, args.zone)
            print(f"白色 -> 0x{code:08X}  rc = {d.send(code)}")
        if args.off:
            print("关闭 -> 0xE0000007  rc =", d.send(0xE0000007))
            print("打开 -> 0xE007F001  rc =", d.send(0xE007F001))
        if args.hold:
            time.sleep(args.hold)
        return 0

    return probe()


if __name__ == "__main__":
    sys.exit(main())
