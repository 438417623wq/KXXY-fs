# -*- coding: utf-8 -*-
"""原生窗口外壳：自绘画布、左侧导航、页面切换、数据刷新。

设计思路：
  * 整个界面只有一个 wx.Panel（一个 HWND），所有内容用 GraphicsContext 画，
    没有浏览器、没有子进程，内存基本就是 Python + wx 本身。
  * 逻辑尺寸固定，窗口变化时整体等比缩放（和网页版 CSS transform 一样），
    所以换什么分辨率都不会「底部显示不全」。
"""
from __future__ import annotations

import os
import time
import traceback

import wx

from . import theme as T
from .charts import CurveEditor, LineChart, interp
from .kit import (Button, Chip, Dot, Label, Node, RichLabel, Segmented, Select,
                  Slider, Switch)
from .theme import C, font

DESIGN_W, DESIGN_H = T.DESIGN_W, T.DESIGN_H
RAIL_W = T.RAIL_W

_err_seen: set = set()


def _log_exc(tag: str) -> None:
    """界面异常只抛一次堆栈到 crash.log —— 打包成无控制台时全靠它排查。"""
    text = traceback.format_exc()
    print("%s\n%s" % (tag, text))
    if tag in _err_seen:
        return
    _err_seen.add(tag)
    _log_line("%s\n%s" % (tag, text), name="crash.log")


def _log_line(text: str, name: str = "app.log") -> None:
    try:
        import sys

        base = os.environ.get("ECPILOT_DATA") or os.path.join(
            os.path.dirname(sys.executable), "data")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, name), "a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except Exception:
        pass

VIEWS = [
    ("overview", "概览", "温度与风扇的当前状态"),
    ("perf", "性能模式", "省电、静音、娱乐、性能四档"),
    ("fans", "风扇", "场景、方案、曲线、手动转速"),
    ("light", "键盘灯", "颜色、亮度、灯效"),
    ("safety", "安全", "保护策略与写入日志"),
    ("diag", "诊断", "环境、传感器、EC 寄存器"),
    ("settings", "设置", "开机自启、屏幕适配、维护"),
]


class Page:
    """页面基类。"""

    title = ""
    subtitle = ""

    def __init__(self, app):
        self.app = app
        self.nodes: list[Node] = []
        self.W = DESIGN_W - RAIL_W
        self.H = DESIGN_H
        self.dirty = True

    # --- 生命周期 ---
    def build(self):
        pass

    def layout(self):
        pass

    def paint(self, gc):
        for n in self.nodes:
            n.paint(gc)

    def paint_overlay(self, gc):
        sel = self.open_select()
        if sel is not None:
            sel.paint_overlay(gc)

    def open_select(self):
        for n in self.nodes:
            if isinstance(n, Select) and n.open:
                return n
        return None

    def tick(self, snap):
        pass

    def on_show(self):
        pass

    # --- 事件 ---
    def hit(self, x, y):
        sel = self.open_select()
        if sel is not None:
            return sel
        for n in reversed(self.nodes):
            got = n.hit(x, y)
            if got:
                return got
        return None

    def mouse(self, kind, x, y, evt=None):
        sel = self.open_select()
        if sel is not None and kind in ("move", "up", "down"):
            if sel.on_mouse_overlay(kind, x, y):
                return True
        node = self.hit(x, y)
        if node is not None:
            return node.on_mouse(kind, x, y, evt)
        if kind == "down":
            for n in self.nodes:
                if isinstance(n, Select):
                    n.open = False
        return False

    def add(self, *nodes):
        for n in nodes:
            n.page = self
            self.nodes.append(n)
        return nodes[0] if len(nodes) == 1 else nodes

    def request_repaint(self):
        self.dirty = True
        self.app.request_repaint()

    def hint(self, gc, text, x, y, size=12):
        T.text(gc, text, x, y, font(size), C.mute)


class Canvas(wx.Panel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.scale = 1.0
        self._first_paint = False
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetDoubleBuffered(True)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Bind(wx.EVT_ERASE_BACKGROUND, lambda e: None)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_up)
        self.Bind(wx.EVT_LEFT_DCLICK, self._on_dclick)
        self.Bind(wx.EVT_RIGHT_DOWN, self._on_right)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)
        self.Bind(wx.EVT_MOUSEWHEEL, self._on_wheel)

    # ---- 几何 ----
    def logical_size(self):
        w, h = self.GetClientSize()
        if w <= 0 or h <= 0:
            return DESIGN_W, DESIGN_H
        self.scale = min(w / float(DESIGN_W), h / float(DESIGN_H))
        self.scale = max(0.55, min(2.0, self.scale))
        return w / self.scale, h / self.scale

    def _pt(self, evt):
        x, y = evt.GetPosition()
        s = self.scale or 1.0
        return x / s, y / s

    # ---- 事件 ----
    def _on_size(self, evt):
        self.logical_size()
        self.app.relayout()
        self.Refresh(False)
        evt.Skip()

    def _on_paint(self, evt):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(T.col(C.bg)))
        dc.Clear()
        gc = wx.GraphicsContext.Create(dc)
        if not gc:
            _log_exc("GraphicsContext.Create 返回 None")
            return
        gc.SetAntialiasMode(wx.ANTIALIAS_DEFAULT)
        gc.Scale(self.scale, self.scale)
        if not self._first_paint:
            self._first_paint = True
            w, h = self.GetClientSize()
            _log_line("[ui] 首次绘制 client=%dx%d scale=%.3f 逻辑=%.0fx%.0f" % (
                w, h, self.scale, w / (self.scale or 1), h / (self.scale or 1)))
        try:
            self.app.paint(gc)
        except Exception:
            _log_exc("paint 失败")

    def _on_motion(self, evt):
        x, y = self._pt(evt)
        handled = self.app.mouse("move", x, y, evt)
        cur = self.app.cursor_at(x, y)
        if cur == "hand":
            self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        else:
            self.SetCursor(wx.Cursor(wx.CURSOR_ARROW))
        self.app.set_tip(self.app.tip_at(x, y))
        if handled:
            return
        evt.Skip()

    def _on_down(self, evt):
        x, y = self._pt(evt)
        self.CaptureMouse()
        if self.app.mouse("down", x, y, evt):
            return
        evt.Skip()

    def _on_up(self, evt):
        x, y = self._pt(evt)
        if self.HasCapture():
            self.ReleaseMouse()
        if self.app.mouse("up", x, y, evt):
            return
        evt.Skip()

    def _on_dclick(self, evt):
        x, y = self._pt(evt)
        if self.app.mouse("dclick", x, y, evt):
            return
        evt.Skip()

    def _on_right(self, evt):
        x, y = self._pt(evt)
        self.app.mouse("ctx", x, y, evt)
        evt.Skip()

    def _on_leave(self, evt):
        self.app.mouse("leave", -1, -1, evt)
        self.app.set_tip(None)
        evt.Skip()

    def _on_wheel(self, evt):
        evt.Skip()


class Shell:
    """非窗口部分：导航 + 页面管理 + 刷新。由 NativeApp 驱动。"""

    def __init__(self, ctx, app_frame):
        self.ctx = ctx
        self.frame = app_frame
        self.view = "overview"
        self.pages: dict[str, Page] = {}
        self.W, self.H = DESIGN_W, DESIGN_H
        self._nav_hover = -1
        self._tip = None
        self._tip_ts = 0.0
        self.clock_tick = 0

    # ---- 页面 ----
    def register(self, key, page):
        self.pages[key] = page
        page.build()
        page.layout()

    def current(self):
        return self.pages.get(self.view)

    def switch(self, key):
        if key not in self.pages or key == self.view:
            return
        self.view = key
        page = self.pages[key]
        page.on_show()
        page.layout()
        page.request_repaint()
        self.frame.request_repaint()

    # ---- 布局 ----
    def relayout(self, w=None, h=None):
        self.W = max(DESIGN_W * 0.85, w or self.W)
        self.H = max(DESIGN_H * 0.85, h or self.H)
        page = self.current()
        if page:
            page.W = self.W - RAIL_W
            page.H = self.H
            page.layout()

    # ---- 绘制 ----
    def paint(self, gc):
        page = self.current()
        self._paint_rail(gc)
        if page:
            gc.PushState()
            gc.Translate(RAIL_W, 0)
            page.paint(gc)
            page.paint_overlay(gc)
            gc.PopState()
        self._paint_top(gc)
        if self._tip:
            self._paint_tip(gc)

    def _paint_rail(self, gc):
        h = self.H
        T.card(gc, 0, 0, RAIL_W, h, 0, fill=C.rail)
        T.card(gc, RAIL_W - 1, 0, 1, h, 0, fill="#111a24")

        # 品牌
        T.card(gc, 20, 20, 34, 34, 11, fill=T.mix(C.accent, C.rail, 0.78))
        T.text(gc, "EC", 37, 27, font(13, "bold"), C.accent, align="center")
        T.text(gc, "EC Pilot", 62, 22, font(13.5, "semibold"), C.text)
        T.text(gc, "战神 Z8 · 温控台", 62, 40, font(10.5), C.mute)

        y = 76
        for i, (key, name, desc) in enumerate(VIEWS):
            on = key == self.view
            hot = i == self._nav_hover
            if on:
                T.card(gc, 12, y, RAIL_W - 24, 40, 10,
                       fill=T.mix(C.accent, C.rail, 0.88))
                T.card(gc, 12, y + 11, 3, 18, 1.5, fill=C.accent)
            elif hot:
                T.card(gc, 12, y, RAIL_W - 24, 40, 10, fill="#121b24")
            color = C.accent if on else (C.text if hot else C.dim)
            T.text(gc, name, 30, y + 11, font(12.5, "semibold" if on else "normal"), color)
            y += 44

        # 底部状态
        snap = self.frame.last_snap or {}
        backend = (snap.get("backend") or {})
        ok = not backend.get("is_sim") and backend.get("label")
        by = h - 62
        T.card(gc, 12, by, RAIL_W - 24, 50, 10, fill="#0e151d", stroke="#151f2a")
        T.card(gc, 22, by + 14, 7, 7, 3.5, fill=C.ok if ok else C.warn)
        T.text(gc, "驱动已就绪" if ok else (backend.get("label") or "未连接"),
               34, by + 9, font(11), C.text if ok else C.warn, max_w=RAIL_W - 60)
        T.text(gc, "v" + (snap.get("version") or "1.3.1"), 34, by + 26, font(10), C.mute)

    def _paint_top(self, gc):
        page = self.current()
        if not page:
            return
        T.text(gc, page.title, RAIL_W + 22, 16, font(15, "semibold"), C.text)
        if page.subtitle:
            T.text(gc, page.subtitle, RAIL_W + 22 + T.text_w(gc, page.title, font(15, "semibold")) + 10,
                   20, font(11), C.mute)

    @staticmethod
    def _wrap_tip(gc, text, f, max_w, max_lines=3):
        """把提示文字按窗口宽度折成最多 max_lines 行。

        原来这里只画一行：性能模式的模式卡把整段描述当提示，一行就有四五十个字，
        气泡会横着压到隔壁卡片上去。折行后宽度永远不超过窗口。
        """
        lines, cur = [], ""
        for ch in str(text):
            if ch == "\n":
                lines.append(cur)
                cur = ""
                if len(lines) >= max_lines:
                    break
                continue
            trial = cur + ch
            if cur and T.text_w(gc, trial, f) > max_w:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    break
            else:
                cur = trial
        if cur and len(lines) < max_lines:
            lines.append(cur)
        elif cur and lines:
            lines[-1] = lines[-1].rstrip("，,。.、") + "…"
        return [ln for ln in lines if ln] or [""]

    def _paint_tip(self, gc):
        if not self._tip:
            return
        # 鼠标移出窗口时 wx 会发 leave，正常会清掉；但用 Alt+Tab、程序化移动光标
        # 等方式离开时可能收不到，气泡就会一直浮在那里。加个超时兜底。
        if time.monotonic() - self._tip_ts > 8.0:
            self._tip = None
            return
        try:
            text, x, y = self._tip
        except (TypeError, ValueError):
            return
        f = font(10.5)
        lines = self._wrap_tip(gc, text, f, max(140.0, self.W - 80))
        tw = max(T.text_w(gc, ln, f) for ln in lines)
        w, lh = tw + 16, 17
        h = lh * len(lines) + 10
        x = max(4, min(self.W - w - 4, x + 14))
        y = max(4, min(self.H - h - 4, y - h - 12))
        # 气泡底色要和卡片拉开差距：原来用 #243040 跟卡片几乎一个色，
        # 悬停时看着像「文字糊到隔壁卡片上」，不像气泡
        T.card(gc, x, y, w, h, 6, fill="#31435a", stroke="#4a6484")
        for i, ln in enumerate(lines):
            T.text(gc, ln, x + 8, y + 5 + i * lh, f, "#eaf2fa")

    # ---- 事件 ----
    def mouse(self, kind, x, y, evt=None):
        if x < RAIL_W:
            return self._rail_mouse(kind, x, y)
        page = self.current()
        if page:
            return page.mouse(kind, x - RAIL_W, y, evt)
        return False

    def _rail_mouse(self, kind, x, y):
        idx = -1
        if 76 <= y:
            i = int((y - 76) // 44)
            if 0 <= i < len(VIEWS) and 12 <= x <= RAIL_W - 12:
                idx = i
        if kind == "move":
            if idx != self._nav_hover:
                self._nav_hover = idx
                self.frame.request_repaint()
        elif kind == "up" and idx >= 0:
            self.switch(VIEWS[idx][0])
        return kind in ("up",) and idx >= 0

    def cursor_at(self, x, y):
        if x < RAIL_W:
            if 76 <= y:
                i = int((y - 76) // 44)
                if 0 <= i < len(VIEWS):
                    return "hand"
            return None
        page = self.current()
        if not page:
            return None
        node = page.hit(x - RAIL_W, y)
        return node.cursor if node and node.cursor else None

    def tip_at(self, x, y):
        if x < RAIL_W:
            if 76 <= y:
                i = int((y - 76) // 44)
                if 0 <= i < len(VIEWS):
                    return (VIEWS[i][2], x, y)
            return None
        page = self.current()
        if not page:
            return None
        node = page.hit(x - RAIL_W, y)
        if node:
            t = node.tip(x - RAIL_W, y)
            if t:
                return (t, x, y)
        return None

    def set_tip(self, tip):
        if tip != self._tip:
            self._tip = tip
            self._tip_ts = time.monotonic()
            self.frame.request_repaint()
