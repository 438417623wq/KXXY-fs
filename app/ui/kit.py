# -*- coding: utf-8 -*-
"""迷你自绘控件库。

每个控件只是一个「几何 + 绘制 + 命中测试 + 鼠标事件」的小对象，
全部画在同一个 wx.Panel 上（一个窗口、一次 GC），所以内存和 CPU 都很低。
坐标一律是逻辑像素（页面会先做整体缩放换算）。
"""
from __future__ import annotations

import wx

from . import theme as T
from .theme import C, font


class Node:
    """控件基类。"""

    def __init__(self):
        self.x = self.y = 0.0
        self.w = self.h = 0.0
        self.visible = True
        self.enabled = True
        self.hover = False
        self.pressed = False
        self.page = None          # 由页面注入，用来抓重绘/回调
        self.cursor = None        # "hand" 等
        self._drag = False

    # --- 几何 ---
    def place(self, x, y, w, h):
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)
        return self

    def inside(self, px, py) -> bool:
        return (self.visible and self.x <= px <= self.x + self.w
                and self.y <= py <= self.y + self.h)

    # --- 绘制 ---
    def paint(self, gc):
        pass

    # --- 交互 ---
    def hit(self, px, py):
        return self if self.inside(px, py) else None

    def on_mouse(self, kind, px, py, evt=None) -> bool:
        return False

    def tip(self, px, py):
        return None

    def repaint(self):
        if self.page is not None:
            self.page.request_repaint()


class Label(Node):
    def __init__(self, text="", size=13, weight="normal", color=None,
                 align="left", mono=False, alpha=255, ellipsis=True):
        super().__init__()
        self.text = text
        self.size = size
        self.weight = weight
        self.color = color or C.text
        self.align = align
        self.mono = mono
        self.alpha = alpha
        self.ellipsis = ellipsis

    def paint(self, gc):
        if not self.visible or not self.text:
            return
        f = font(self.size, self.weight, self.mono)
        cx = self.x
        if self.align == "center":
            cx = self.x + self.w / 2.0
        elif self.align == "right":
            cx = self.x + self.w
        T.text(gc, self.text, cx, self.y + (self.h - self.size * 1.32) / 2.0, f,
               self.color, self.alpha, self.align,
               max_w=self.w if self.ellipsis and self.align != "center" else None)

    def set(self, text):
        if text != self.text:
            self.text = text
            self.repaint()


class RichLabel(Node):
    """一行里不同字号/颜色的组合：[(text, size, weight, color), ...]"""

    def __init__(self, parts=None, gap=0, align="left"):
        super().__init__()
        self.parts = parts or []
        self.gap = gap
        self.align = align

    def widths(self, gc):
        out = []
        for text, size, weight, _color, *_ in self.parts:
            gc.SetFont(font(size, weight), T.col(C.text))
            out.append(gc.GetTextExtent(text)[0] if text else 0.0)
        return out

    def paint(self, gc):
        if not self.parts:
            return
        ws = self.widths(gc)
        total = sum(ws) + self.gap * (len(ws) - 1)
        cx = self.x
        if self.align == "center":
            cx = self.x + (self.w - total) / 2.0
        elif self.align == "right":
            cx = self.x + self.w - total
        base = self.y + self.h / 2.0
        for (text, size, weight, color, *rest), w in zip(self.parts, ws):
            if not text:
                continue
            alpha = rest[0] if rest else 255
            T.text(gc, text, cx, base - size * 0.70, font(size, weight), color, alpha)
            cx += w + self.gap

    def set_parts(self, parts):
        self.parts = parts
        self.repaint()


class Button(Node):
    def __init__(self, label, on_click=None, kind="normal", size=12.5, tip=None,
                 icon=None):
        super().__init__()
        self.label = label
        self.on_click = on_click
        self.kind = kind          # primary / normal / ghost / danger
        self.size = size
        self.tip_text = tip
        self.icon = icon
        self.cursor = "hand"
        self.busy = False

    def measure(self, gc):
        f = font(self.size, "semibold" if self.kind == "primary" else "normal")
        w = T.text_w(gc, self.label, f) + (34 if self.icon else 24)
        return w, 30

    def paint(self, gc):
        if not self.visible:
            return
        r = 9
        if self.kind == "primary":
            base, fg, bd = C.accent, "#04121a", None
            if self.hover:
                base = T.mix(C.accent, "#ffffff", 0.18)
        elif self.kind == "danger":
            base, fg, bd = "#2a1618", C.danger, "#472126"
            if self.hover:
                base = "#361a1d"
        elif self.kind == "ghost":
            base, fg, bd = None, C.dim, C.line
            if self.hover:
                base, fg = C.surface2, C.text
        else:
            base, fg, bd = C.surface3, C.text, C.line
            if self.hover:
                base = "#26333f"
        if self.pressed and base:
            base = T.mix(base, "#000000", 0.18)
        if base:
            T.card(gc, self.x, self.y, self.w, self.h, r, fill=base)
        if bd:
            T.card(gc, self.x, self.y, self.w, self.h, r, stroke=bd)
        f = font(self.size, "semibold" if self.kind == "primary" else "normal")
        label = self.label
        if self.busy:
            label = "处理中…"
        T.text(gc, label, self.x + self.w / 2.0, self.y + (self.h - self.size * 1.34) / 2.0,
               f, fg, align="center")

    def on_mouse(self, kind, px, py, evt=None):
        if not self.enabled:
            return False
        if kind == "down":
            self.pressed = True
            self.repaint()
            return True
        if kind == "up":
            was = self.pressed
            self.pressed = False
            self.repaint()
            if was and self.inside(px, py) and self.on_click:
                self.on_click()
            return True
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.repaint()
        return False

    def tip(self, px, py):
        return self.tip_text


class Chip(Node):
    """小胶囊按钮（曲线工具条那种）。"""

    def __init__(self, label, on_click=None, active=False):
        super().__init__()
        self.label = label
        self.on_click = on_click
        self.active = active
        self.cursor = "hand"

    def measure(self, gc):
        return T.text_w(gc, self.label, font(11.5)) + 22, 26

    def paint(self, gc):
        if not self.visible:
            return
        if self.active:
            fill, fg, bd = T.mix(C.accent, C.bg, 0.82), C.accent, T.mix(C.accent, C.bg, 0.55)
        else:
            fill, fg, bd = C.surface2, C.dim, C.line
            if self.hover:
                fill, fg = C.surface3, C.text
        T.card(gc, self.x, self.y, self.w, self.h, self.h / 2.0, fill=fill, stroke=bd)
        T.text(gc, self.label, self.x + self.w / 2.0,
               self.y + (self.h - 11.5 * 1.3) / 2.0, font(11.5), fg, align="center")

    def on_mouse(self, kind, px, py, evt=None):
        if kind == "up" and self.inside(px, py):
            if self.on_click:
                self.on_click()
            return True
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.repaint()
        return False


class Switch(Node):
    def __init__(self, value=False, on_change=None):
        super().__init__()
        self.value = bool(value)
        self.on_change = on_change
        self.cursor = "hand"

    def paint(self, gc):
        if not self.visible:
            return
        w, h = self.w, self.h
        track = C.accent if self.value else "#2a3846"
        T.card(gc, self.x, self.y + (h - 22) / 2.0, 40, 22, 11, fill=track,
               alpha=255 if self.value else 255)
        kx = self.x + (40 - 18 - 2) if self.value else self.x + 2
        T.card(gc, kx, self.y + (h - 18) / 2.0, 18, 18, 9, fill="#ffffff" if self.value else "#8b9dae")

    def on_mouse(self, kind, px, py, evt=None):
        if not self.enabled:
            return False
        if kind == "up" and self.inside(px, py):
            self.value = not self.value
            self.repaint()
            if self.on_change:
                self.on_change(self.value)
            return True
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.cursor = "hand" if h else None
                self.repaint()
        return False


class Slider(Node):
    """水平滑块，拖动实时回调。"""

    def __init__(self, value=50, on_change=None, min_v=0, max_v=100, on_done=None):
        super().__init__()
        self.value = float(value)
        self.on_change = on_change
        self.on_done = on_done
        self.min_v = min_v
        self.max_v = max_v
        self._drag = False

    def paint(self, gc):
        if not self.visible:
            return
        cy = self.y + self.h / 2.0
        x0, x1 = self.x + 8, self.x + self.w - 8
        span = max(1.0, x1 - x0)
        ratio = (self.value - self.min_v) / max(1e-6, (self.max_v - self.min_v))
        ratio = max(0.0, min(1.0, ratio))
        kx = x0 + span * ratio
        # 轨道
        T.card(gc, x0, cy - 3, span, 6, 3, fill="#22303d")
        if ratio > 0:
            T.card(gc, x0, cy - 3, max(6.0, span * ratio), 6, 3, fill=C.accent_dim)
        # 圆钮
        T.card(gc, kx - 8, cy - 8, 16, 16, 8, fill="#eef6ff",
               stroke=C.accent if self._drag or self.hover else None, stroke_w=2)

    def _set_from(self, px):
        x0, x1 = self.x + 8, self.x + self.w - 8
        ratio = (px - x0) / max(1.0, x1 - x0)
        ratio = max(0.0, min(1.0, ratio))
        val = self.min_v + (self.max_v - self.min_v) * ratio
        if int(val) != int(self.value):
            self.value = val
            self.repaint()
            if self.on_change:
                self.on_change(int(val))

    def on_mouse(self, kind, px, py, evt=None):
        if not self.enabled:
            return False
        if kind == "down":
            self._drag = True
            self._set_from(px)
            return True
        if kind == "move":
            if self._drag:
                self._set_from(px)
                return True
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.cursor = "hand" if h else None
                self.repaint()
        if kind == "up":
            if self._drag:
                self._drag = False
                self.repaint()
                if self.on_done:
                    self.on_done(int(self.value))
            return True
        return False

    def set(self, value):
        self.value = float(value)
        self.repaint()


class Segmented(Node):
    """三选一（自动 / 手动 / 曲线）。"""

    def __init__(self, options, index=0, on_change=None):
        super().__init__()
        self.options = list(options)       # [(label, value)]
        self.index = index
        self.on_change = on_change
        self.cursor = "hand"
        self._hover_i = -1

    def measure(self, gc):
        w = 0
        for label, _ in self.options:
            w += T.text_w(gc, label, font(11.5)) + 22
        return w, 26

    def paint(self, gc):
        if not self.visible:
            return
        T.card(gc, self.x, self.y, self.w, self.h, 8, fill=C.surface2, stroke=C.line)
        n = max(1, len(self.options))
        cw = self.w / n
        for i, (label, _) in enumerate(self.options):
            cx = self.x + cw * i
            if i == self.index:
                T.card(gc, cx + 2, self.y + 2, cw - 4, self.h - 4, 6,
                       fill=T.mix(C.accent, C.bg, 0.80))
            elif i == self._hover_i:
                T.card(gc, cx + 2, self.y + 2, cw - 4, self.h - 4, 6, fill="#22303d")
            color = C.accent if i == self.index else C.dim
            T.text(gc, label, cx + cw / 2.0, self.y + (self.h - 11.5 * 1.3) / 2.0,
                   font(11.5, "semibold" if i == self.index else "normal"),
                   color, align="center")

    def _index_at(self, px):
        n = max(1, len(self.options))
        if self.w <= 0:
            return -1
        i = int((px - self.x) / (self.w / n))
        return i if 0 <= i < n else -1

    def on_mouse(self, kind, px, py, evt=None):
        if not self.enabled:
            return False
        if kind == "move":
            i = self._index_at(px) if self.inside(px, py) else -1
            if i != self._hover_i:
                self._hover_i = i
                self.cursor = "hand" if i >= 0 else None
                self.repaint()
        if kind == "up" and self.inside(px, py):
            i = self._index_at(px)
            if 0 <= i < len(self.options) and i != self.index:
                self.index = i
                self.repaint()
                if self.on_change:
                    self.on_change(self.options[i][1])
            return True
        return False


class Select(Node):
    """下拉选择：点开后在同一画布最上层画列表（页面负责 overlay）。"""

    def __init__(self, options, index=0, on_change=None, w=200, size=12.5):
        super().__init__()
        self.options = list(options)   # [(label, value)]
        self.index = index
        self.on_change = on_change
        self.size = size
        self.w = w
        self.h = 32
        self.cursor = "hand"
        self.open = False
        self._hover_i = -1

    def label(self):
        return self.options[self.index][0] if self.options else ""

    def paint(self, gc):
        if not self.visible:
            return
        T.card(gc, self.x, self.y, self.w, self.h, 9, fill=C.surface2,
               stroke=C.line_strong if self.hover else C.line)
        T.text(gc, self.label(), self.x + 12, self.y + (self.h - self.size * 1.35) / 2.0,
               font(self.size), C.text, max_w=self.w - 34)
        # 箭头
        cx, cy = self.x + self.w - 14, self.y + self.h / 2.0
        path = gc.CreatePath()
        path.MoveToPoint(cx - 4, cy - 2)
        path.AddLineToPoint(cx, cy + 2.5)
        path.AddLineToPoint(cx + 4, cy - 2)
        gc.SetPen(T.pen(gc, C.dim, 1.6))
        gc.StrokePath(path)

    def overlay_rect(self):
        n = len(self.options)
        h = n * 28 + 8
        y = self.y + self.h + 4
        return self.x, y, self.w, h

    def paint_overlay(self, gc):
        x, y, w, h = self.overlay_rect()
        T.card(gc, x + 2, y + 6, w, h, 10, fill="#000000", alpha=110)
        T.card(gc, x, y, w, h, 10, fill="#1b2733", stroke=C.line_strong)
        for i, (label, _) in enumerate(self.options):
            iy = y + 4 + i * 28
            if i == self._hover_i:
                T.card(gc, x + 4, iy, w - 8, 28, 7, fill="#26333f")
            T.text(gc, label, x + 12, iy + (28 - self.size * 1.35) / 2.0,
                   font(self.size), C.accent if i == self.index else C.text,
                   max_w=w - 24)

    def on_mouse_overlay(self, kind, px, py):
        x, y, w, h = self.overlay_rect()
        inside = x <= px <= x + w and y <= py <= y + h
        if kind == "move":
            i = int((py - y - 4) // 28) if inside else -1
            if i != self._hover_i:
                self._hover_i = i
                self.cursor = "hand" if inside else None
                self.repaint()
            return True
        if kind == "up":
            if inside:
                i = int((py - y - 4) // 28)
                if 0 <= i < len(self.options):
                    self.index = i
                    self.open = False
                    self.repaint()
                    if self.on_change:
                        self.on_change(self.options[i][1])
                    return True
            self.open = False
            self.repaint()
            return True
        return True

    def on_mouse(self, kind, px, py, evt=None):
        if not self.enabled:
            return False
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.repaint()
        if kind == "up" and self.inside(px, py):
            self.open = not self.open
            self._hover_i = -1
            self.repaint()
            return True
        return False


class Dot(Node):
    """状态圆点（在线/离线）。"""

    def __init__(self, color=None):
        super().__init__()
        self.color = color or C.ok

    def paint(self, gc):
        if not self.visible:
            return
        r = min(self.w, self.h) / 2.0
        cx, cy = self.x + r, self.y + r
        T.card(gc, cx - r, cy - r, r * 2, r * 2, r, fill=self.color, alpha=60)
        T.card(gc, cx - r / 2, cy - r / 2, r, r, r / 2, fill=self.color)


class Spacer(Node):
    pass
