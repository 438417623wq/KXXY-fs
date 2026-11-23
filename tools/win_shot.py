# -*- coding: utf-8 -*-
"""按窗口标题找窗口并截图（窗口被遮挡也能拍到内容）。

用法：python tools/win_shot.py "EC Pilot" out.png
"""
import ctypes
import os
import struct
import sys
import zlib
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

try:  # 不声明 DPI 感知的话，GetWindowRect 给的是缩放过的小尺寸，截出来会缺一块
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def find_window(part):
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if part in buf.value:
            hits.append((hwnd, buf.value))
        return True

    user32.EnumWindows(_cb, 0)
    return hits


def shot(hwnd, w, h, path):
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, 2)  # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BI(ctypes.Structure):
        _fields_ = [("bmiHeader", BIH), ("bmiColors", wintypes.DWORD * 3)]

    if ok:
        bi = BI()
        bi.bmiHeader.biSize = ctypes.sizeof(BIH)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        stride = w * 4
        raw = bytearray()
        data = buf.raw
        for y in range(h):
            row = bytearray(data[y * stride:(y + 1) * stride])
            row[0::4], row[2::4] = row[2::4], row[0::4]
            row[3::4] = b"\xff" * (len(row) // 4)
            raw.append(0)
            raw += row

        def chunk(tag, body):
            return (struct.pack(">I", len(body)) + tag + body
                    + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        png += chunk(b"IEND", b"")
        with open(path, "wb") as fh:
            fh.write(png)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)
    return ok


def main():
    part = sys.argv[1] if len(sys.argv) > 1 else "EC Pilot"
    out = sys.argv[2] if len(sys.argv) > 2 else "shot.png"
    hits = find_window(part)
    if not hits:
        print("not found:", part)
        return 1
    hwnd, title = hits[0]
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    ok = shot(hwnd, w, h, out)
    print("hwnd=%s title=%s size=%dx%d ok=%s -> %s" % (hwnd, title, w, h, ok, os.path.abspath(out)))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
