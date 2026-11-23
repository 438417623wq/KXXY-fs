# -*- coding: utf-8 -*-
"""开发期自检：用模拟后端启动原生界面，方便截图核对排版。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

VIEW = os.environ.get("VIEW", "")
SHOT = os.environ.get("SHOT", "")


def _print_window(hwnd, w, h, path):
    """用 PrintWindow 抓窗口自身内容，窗口被遮挡也拿得到。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc_win = user32.GetWindowDC(hwnd)
    if not hdc_win:
        return False
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, 2)  # PW_RENDERFULLCONTENT
    if ok:
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        import struct
        import zlib

        raw = bytearray()
        stride = w * 4
        data = buf.raw
        for y in range(h):
            row = bytearray(data[y * stride:(y + 1) * stride])
            # GetDIBits 给的是 BGRA，PNG 要 RGBA，红蓝对调
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
    return bool(ok)


def run_native(ctx, runner, show_window, view=""):
    import wx

    M.enable_dpi_aware()
    from ui.app_native import NativeApp

    app = wx.App(False)
    ctx.clients = 1
    frame = NativeApp(ctx, runner, start_hidden=False, start_view=VIEW or view)
    frame.Show()
    frame.Raise()
    if os.environ.get("LIGHT_ON"):
        ctx.light.update({"enabled": True, "hex": os.environ.get("LIGHT_ON"),
                          "brightness": 85})
    print("NATIVE READY", flush=True)

    if os.environ.get("SELFTEST"):
        def s1():
            print("SELFTEST shown_initial=%s tray=%s" % (frame.IsShown(), bool(frame.tray)), flush=True)
            frame.Close()

        def s2():
            print("SELFTEST shown_after_close=%s (expect False)" % frame.IsShown(), flush=True)
            frame.show_window()

        def s3():
            print("SELFTEST shown_after_reopen=%s (expect True) ticks_ok=%s" % (
                frame.IsShown(), bool(frame.last_snap)), flush=True)
            app.ExitMainLoop()

        wx.CallLater(2000, s1)
        wx.CallLater(3200, s2)
        wx.CallLater(4400, s3)

    if os.environ.get("PERF_TEST"):
        def p1():
            page = frame.shell.pages.get("perf")
            frame.shell.switch("perf")
            mode = int(os.environ.get("PERF_TEST"))
            print("PERF_TEST pick mode=%d" % mode, flush=True)
            page._pick(mode)
            wx.CallLater(9000, p2)

        def p2():
            page = frame.shell.pages.get("perf")
            print("PERF_TEST msg=%r busy=%r cards=%s cfg=%s" % (
                page._msg, page._busy_id,
                [(c.mode["name"], c.active) for c in page.cards],
                frame.ctx.store.data.get("perf")), flush=True)
            app.ExitMainLoop()

        wx.CallLater(2200, p1)

    if SHOT:
        def _shot():
            pos, size = frame.GetScreenPosition(), frame.GetSize()
            cs = frame.GetClientSize()
            cc = frame.canvas.GetClientSize()
            print("DIAG frame=%dx%d client=%dx%d canvas=%dx%d scale=%.3f shell=%.0fx%.0f page=%.0fx%.0f dbg=%s" % (
                size.width, size.height, cs.width, cs.height,
                cc.width, cc.height, frame.canvas.scale,
                frame.shell.W, frame.shell.H,
                frame.shell.current().W if frame.shell.current() else 0,
                frame.shell.current().H if frame.shell.current() else 0,
                getattr(frame.shell.current(), "_dbg", None)), flush=True)
            if not _print_window(frame.GetHandle(), size.width, size.height, SHOT):
                sdc = wx.ScreenDC()
                bmp = wx.Bitmap(size.width, size.height)
                mdc = wx.MemoryDC(bmp)
                mdc.Blit(0, 0, size.width, size.height, sdc, pos.x, pos.y)
                mdc.SelectObject(wx.NullBitmap)
                bmp.SaveFile(SHOT, wx.BITMAP_TYPE_PNG)
            print("SHOT OK %s %dx%d" % (SHOT, size.width, size.height), flush=True)
            app.ExitMainLoop()

        wx.CallLater(2600, _shot)

    app.MainLoop()
    runner.shutdown()
    return 0


import main as M  # noqa: E402

M.run_native = run_native
sys.argv = ["ecpilot", "--sim", "--ui", "native", "--port", os.environ.get("PORT", "8911")]
sys.exit(M.main())
