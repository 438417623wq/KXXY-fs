# -*- coding: utf-8 -*-
"""原生界面的主题：颜色、字体、圆角绘制工具。

界面按 1320×792 的逻辑尺寸排版，实际窗口大小不同时整体等比缩放，
所以这里所有尺寸都写逻辑像素，不用关心 DPI。
"""
from __future__ import annotations

import wx


class C:
    """颜色常量（与网页版一致）。"""

    bg = "#0c1219"
    rail = "#0a0f15"
    surface = "#141c26"
    surface2 = "#18222e"
    surface3 = "#1e2a37"
    line = "#1e2a38"
    line_soft = "#18222e"
    line_strong = "#2b3a4b"
    text = "#e8f0f8"
    dim = "#9aabbd"
    mute = "#6d7f92"
    accent = "#38e1ff"
    accent_dim = "#1f7f94"
    ok = "#34d399"
    warn = "#fbbf24"
    danger = "#f87171"
    purple = "#7c5cff"


# 设计基准（逻辑像素）
DESIGN_W = 1320
DESIGN_H = 792
RAIL_W = 196

_cache: dict = {}


def col(hex_str: str, alpha: int = 255) -> wx.Colour:
    """#rrggbb → wx.Colour。"""
    key = (hex_str, alpha)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    h = hex_str.lstrip("#")
    c = wx.Colour(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    _cache[key] = c
    return c


def mix(a: str, b: str, t: float) -> str:
    """两色按 t 混合，返回 #rrggbb。"""
    a, b = a.lstrip("#"), b.lstrip("#")
    out = []
    for i in (0, 2, 4):
        va, vb = int(a[i:i + 2], 16), int(b[i:i + 2], 16)
        out.append(int(round(va + (vb - va) * t)))
    return "#%02x%02x%02x" % tuple(out)


FAMILY = "Microsoft YaHei UI"
MONO = "Consolas"


def font(size: float, weight: str = "normal", mono: bool = False) -> wx.Font:
    """逻辑字号（px 近似）。wx 用 pt，按 96dpi 换算成 pt≈px*0.75。"""
    key = (round(size, 1), weight, mono)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    w = {"normal": wx.FONTWEIGHT_NORMAL,
         "medium": wx.FONTWEIGHT_MEDIUM,
         "bold": wx.FONTWEIGHT_BOLD,
         "semibold": wx.FONTWEIGHT_SEMIBOLD}.get(weight, wx.FONTWEIGHT_NORMAL)
    if mono:
        f = wx.Font(wx.FontInfo(max(6.0, size * 0.75)).FaceName(MONO).Weight(w))
    else:
        f = wx.Font(wx.FontInfo(max(6.0, size * 0.75)).FaceName(FAMILY).Weight(w))
    _cache[key] = f
    return f


# ---------------- 绘制工具 ----------------
# 说明：全部基于 wx.GraphicsContext，argb 抗锯齿，圆角和渐变都能画。


def brush(gc, hex_str: str, alpha: int = 255):
    return gc.CreateBrush(wx.Brush(col(hex_str, alpha)))


def pen(gc, hex_str: str, width: float = 1.0, alpha: int = 255):
    info = wx.GraphicsPenInfo(col(hex_str, alpha)).Width(width)
    return gc.CreatePen(info)


def round_path(gc, x: float, y: float, w: float, h: float, r: float):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    path = gc.CreatePath()
    path.AddRoundedRectangle(x, y, w, h, r)
    return path


def card(gc, x, y, w, h, r=14, fill=None, stroke=None, stroke_w=1.0,
         top_glow=None, alpha=255):
    """圆角卡片：可选顶部微高光（模拟网页版的渐变质感）。"""
    path = round_path(gc, x, y, w, h, r)
    if fill:
        if top_glow:
            gc.SetBrush(gc.CreateLinearGradientBrush(
                x, y, x, y + h * 0.7, col(top_glow, alpha), col(fill, alpha)))
        else:
            gc.SetBrush(brush(gc, fill, alpha))
        gc.FillPath(path)
    if stroke:
        gc.SetPen(pen(gc, stroke, stroke_w))
        gc.StrokePath(path)
    return path


def text(gc, s: str, x: float, y: float, f: wx.Font, color: str,
         alpha: int = 255, align: str = "left", max_w: float | None = None) -> tuple:
    """画一行文字，返回 (宽, 高)。align: left/center/right。"""
    gc.SetFont(f, col(color, alpha))
    tw, th = gc.GetTextExtent(s)
    if max_w is not None and tw > max_w:
        s = _ellipsize(gc, s, max_w)
        tw, th = gc.GetTextExtent(s)
    if align == "center":
        x -= tw / 2.0
    elif align == "right":
        x -= tw
    gc.DrawText(s, x, y)
    return tw, th


def _ellipsize(gc, s: str, max_w: float) -> str:
    if gc.GetTextExtent(s)[0] <= max_w:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if gc.GetTextExtent(s[:mid] + "…")[0] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + "…"


def text_w(gc, s: str, f: wx.Font) -> float:
    gc.SetFont(f, col(C.text))
    return gc.GetTextExtent(s)[0]


def line_h(gc, f: wx.Font) -> float:
    gc.SetFont(f, col(C.text))
    return gc.GetTextExtent("Ag")[1]


def pill(gc, x, y, w, h, label, fg, bg, alpha=255, f=None):
    """小圆角标签（tag/chip）。"""
    card(gc, x, y, w, h, h / 2.0, fill=bg, alpha=alpha)
    f = f or font(11)
    gc.SetFont(f, col(fg, alpha))
    tw, th = gc.GetTextExtent(label)
    gc.DrawText(label, x + (w - tw) / 2.0, y + (h - th) / 2.0 - 0.5)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ts_short(ts, fallback: str = "--:--:--") -> str:
    """日志里的时间可能已经是字符串（"2026-11-14 22:00:00"），也可能是时间戳。"""
    if isinstance(ts, str):
        return ts[-8:] if len(ts) >= 8 else ts
    try:
        import time

        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return fallback
