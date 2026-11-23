# -*- coding: utf-8 -*-
"""原生界面可行性自检：开一个深色窗口 + 托盘图标，测内存与 CPU，然后自动退出。"""
import ctypes
import sys


def rss_mb():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    p = PMC()
    p.cb = ctypes.sizeof(PMC)
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                               ctypes.byref(p), p.cb)
    return p.WorkingSetSize / 1048576.0, p.PeakWorkingSetSize / 1048576.0


import wx  # noqa: E402
import wx.adv  # noqa: E402

out = []
BG, CARD, LINE = wx.Colour(13, 19, 27), wx.Colour(21, 29, 39), wx.Colour(29, 42, 56)
TEXT, DIM, ACCENT = wx.Colour(232, 240, 248), wx.Colour(122, 140, 160), wx.Colour(56, 225, 255)


class Probe(wx.Frame):
    def __init__(self):
        super().__init__(None, title="ECPilot 原生界面自检", size=(1320, 792))
        self.SetBackgroundColour(BG)
        root = wx.BoxSizer(wx.HORIZONTAL)

        rail = wx.Panel(self, size=(204, -1))
        rail.SetBackgroundColour(wx.Colour(10, 15, 22))
        root.Add(rail, 0, wx.EXPAND)

        body = wx.Panel(self)
        body.SetBackgroundColour(BG)
        bs = wx.BoxSizer(wx.VERTICAL)

        head = wx.StaticText(body, label="原生渲染自检 · 一个进程、无浏览器内核")
        head.SetForegroundColour(TEXT)
        head.SetFont(wx.Font(15, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_SEMIBOLD))
        bs.Add(head, 0, wx.ALL, 18)

        for i in range(6):
            card = wx.Panel(body, size=(-1, 96))
            card.SetBackgroundColour(CARD)
            cs = wx.BoxSizer(wx.VERTICAL)
            t = wx.StaticText(card, label="卡片 %d · CPU 62°C · 风扇 2126 RPM" % (i + 1))
            t.SetForegroundColour(TEXT)
            cs.Add(t, 0, wx.ALL, 14)
            sl = wx.Slider(card, value=40, minValue=0, maxValue=100, style=wx.SL_HORIZONTAL)
            cs.Add(sl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
            card.SetSizer(cs)
            bs.Add(card, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 18)

        body.SetSizer(bs)
        root.Add(body, 1, wx.EXPAND)
        self.SetSizer(root)

        self.tray = None
        try:
            icon = wx.Icon(_make_icon())
            self.SetIcon(icon)
            self.tray = wx.adv.TaskBarIcon()
            self.tray.SetIcon(icon, "ECPilot")
            m = wx.Menu()
            m.Append(wx.ID_ANY, "打开界面")
            m.Append(wx.ID_EXIT, "退出")
            self.tray.CreatePopupMenu = lambda: m
            out.append("tray=ok")
        except Exception as exc:  # noqa: BLE001
            out.append("tray=fail:%s" % exc)

    def report(self):
        ws, peak = rss_mb()
        out.append("rss=%.1fMB peak=%.1fMB" % (ws, peak))
        out.append("children_procs=%d" % _count_child())
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write("\n".join(out))
        self.Close()


def _make_icon():
    bmp = wx.Bitmap(32, 32)
    dc = wx.MemoryDC(bmp)
    dc.SetBackground(wx.Brush(wx.Colour(13, 19, 27)))
    dc.Clear()
    dc.SetBrush(wx.Brush(ACCENT))
    dc.SetPen(wx.Pen(ACCENT))
    dc.DrawCircle(16, 16, 11)
    dc.SelectObject(wx.NullBitmap)
    return wx.Icon(bmp)


def _count_child():
    return 0


app = wx.App(False)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
f = Probe()
f.Show()
wx.CallLater(1500, f.report)
app.MainLoop()
