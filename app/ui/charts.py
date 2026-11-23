# -*- coding: utf-8 -*-
"""图表控件：温度曲线编辑器（可拖点）与实时折线图。"""
from __future__ import annotations

from . import theme as T
from .kit import Node
from .theme import C, font


def interp(points, temp):
    """按曲线点插值出占空比（端点外取端值）。"""
    if not points:
        return 0
    pts = sorted(points, key=lambda p: p[0])
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        t0, d0 = pts[i]
        t1, d1 = pts[i + 1]
        if t0 <= temp <= t1:
            if t1 == t0:
                return d1
            k = (temp - t0) / float(t1 - t0)
            return d0 + (d1 - d0) * k
    return pts[-1][1]


class CurveEditor(Node):
    """温度曲线：横轴 30–100°C，纵轴 0–100%。"""

    T_MIN, T_MAX = 30.0, 100.0
    PAD_L, PAD_R, PAD_T, PAD_B = 30, 12, 12, 22

    def __init__(self, points=None, on_change=None, accent=None):
        super().__init__()
        self.points = [[float(p[0]), float(p[1])] for p in (points or [])]
        self.on_change = on_change
        self.accent = accent or C.accent
        self.temp = None          # 当前温度（画竖线）
        self.duty = None          # 当前占空比
        self._grab = -1
        self._hover_pt = -1

    # ---- 坐标换算 ----
    def _plot(self):
        return (self.x + self.PAD_L, self.y + self.PAD_T,
                self.w - self.PAD_L - self.PAD_R, self.h - self.PAD_T - self.PAD_B)

    def px(self, temp):
        x0, _y0, w, _h = self._plot()
        return x0 + (temp - self.T_MIN) / (self.T_MAX - self.T_MIN) * w

    def py(self, duty):
        _x0, y0, _w, h = self._plot()
        return y0 + (1 - duty / 100.0) * h

    def to_data(self, x, y):
        x0, y0, w, h = self._plot()
        temp = self.T_MIN + (x - x0) / max(1.0, w) * (self.T_MAX - self.T_MIN)
        duty = (1 - (y - y0) / max(1.0, h)) * 100.0
        return max(self.T_MIN, min(self.T_MAX, temp)), max(0.0, min(100.0, duty))

    # ---- 绘制 ----
    def paint(self, gc):
        if not self.visible:
            return
        x0, y0, w, h = self._plot()
        # 背景
        T.card(gc, x0, y0, w, h, 8, fill="#101821")
        # 网格
        for t in range(30, 101, 10):
            gx = self.px(t)
            T.card(gc, gx, y0, 1, h, 0, fill="#1a2531")
            T.text(gc, f"{t}°", gx, y0 + h + 4, font(9.5), C.mute, align="center")
        for d in (0, 25, 50, 75, 100):
            gy = self.py(d)
            T.card(gc, x0, gy, w, 1, 0, fill="#1a2531")
            T.text(gc, str(d), x0 - 6, gy - 7, font(9.5), C.mute, align="right")

        pts = sorted(self.points, key=lambda p: p[0])
        if not pts:
            return
        # 填充
        path = gc.CreatePath()
        path.MoveToPoint(self.px(pts[0][0]), y0 + h)
        for t, d in pts:
            path.AddLineToPoint(self.px(t), self.py(d))
        path.AddLineToPoint(self.px(pts[-1][0]), y0 + h)
        path.CloseSubpath()
        gc.SetBrush(gc.CreateLinearGradientBrush(
            x0, y0, x0, y0 + h, T.col(self.accent, 74), T.col(self.accent, 4)))
        gc.FillPath(path)
        # 折线
        line = gc.CreatePath()
        line.MoveToPoint(self.px(pts[0][0]), self.py(pts[0][1]))
        for t, d in pts[1:]:
            line.AddLineToPoint(self.px(t), self.py(d))
        gc.SetPen(T.pen(gc, self.accent, 2.0))
        gc.StrokePath(line)

        # 当前温度标记
        if self.temp is not None:
            cx = self.px(max(self.T_MIN, min(self.T_MAX, self.temp)))
            gc.SetPen(T.pen(gc, "#ffffff", 1.0, 60))
            gc.StrokeLine(cx, y0, cx, y0 + h)
            if self.duty is not None:
                cy = self.py(self.duty)
                T.card(gc, cx - 4, cy - 4, 8, 8, 4, fill="#ffffff")

        # 控制点
        for i, (t, d) in enumerate(pts):
            cx, cy = self.px(t), self.py(d)
            big = (i == self._hover_pt or i == self._grab)
            r = 6.0 if big else 4.5
            T.card(gc, cx - r - 1, cy - r - 1, (r + 1) * 2, (r + 1) * 2, r + 1, fill="#0b1219")
            T.card(gc, cx - r, cy - r, r * 2, r * 2, r, fill=self.accent)

        # 拖动气泡
        if self._grab >= 0:
            t, d = pts[self._grab]
            bx, by = self.px(t), self.py(d) - 30
            label = f"{int(round(t))}° · {int(round(d))}%"
            tw = T.text_w(gc, label, font(10.5))
            bw = tw + 16
            T.card(gc, bx - bw / 2, by, bw, 22, 6, fill="#22303d", stroke=C.line_strong)
            T.text(gc, label, bx, by + 4, font(10.5), C.text, align="center")

    # ---- 交互 ----
    def _nearest(self, x, y):
        best, bd = -1, 1e9
        for i, (t, d) in enumerate(self.points):
            dx = self.px(t) - x
            dy = self.py(d) - y
            dist = dx * dx + dy * dy
            if dist < bd:
                best, bd = i, dist
        return best if bd <= 15 * 15 else -1

    def on_mouse(self, kind, px, py, evt=None):
        if not self.enabled:
            return False
        x0, y0, w, h = self._plot()
        in_plot = x0 <= px <= x0 + w and y0 <= py <= y0 + h

        if kind == "move":
            i = self._nearest(px, py) if in_plot or self._grab >= 0 else -1
            if i != self._hover_pt:
                self._hover_pt = i
                self.repaint()
            if self._grab >= 0:
                temp, duty = self.to_data(px, py)
                idx = self._grab
                lo = self.points[idx - 1][0] + 1 if idx > 0 else self.T_MIN
                hi = self.points[idx + 1][0] - 1 if idx < len(self.points) - 1 else self.T_MAX
                if lo > hi:
                    lo = hi = self.points[idx][0]
                self.points[idx] = [max(lo, min(hi, temp)), duty]
                self.repaint()
                return True
            self.cursor = "hand" if in_plot else None
            return False

        if kind == "down":
            i = self._nearest(px, py)
            if i >= 0:
                self._grab = i
                self.repaint()
                return True
            return in_plot

        if kind == "up":
            if self._grab >= 0:
                self._grab = -1
                self._emit()
                return True
            # 空白处单击 = 加一个点
            if in_plot and self._nearest(px, py) < 0:
                temp, duty = self.to_data(px, py)
                self.points.append([temp, duty])
                self.points.sort(key=lambda p: p[0])
                self._emit()
                return True
            return False

        if kind == "ctx":
            # 右键：点在圆点上就删掉，点在空白处就加一个
            i = self._nearest(px, py)
            if i >= 0:
                if len(self.points) > 2:
                    self.points.pop(i)
                    self._emit()
                    return True
                return False
            if in_plot:
                temp, duty = self.to_data(px, py)
                self.points.append([temp, duty])
                self.points.sort(key=lambda p: p[0])
                self._emit()
                return True
        return False

    def _emit(self):
        self.repaint()
        if self.on_change:
            self.on_change([list(p) for p in self.points])

    def set_points(self, points, notify=False):
        self.points = [[float(p[0]), float(p[1])] for p in (points or [])]
        self.repaint()
        if notify and self.on_change:
            self.on_change([list(p) for p in self.points])


class LineChart(Node):
    """实时折线：CPU / GPU 温度 + 占空比。"""

    def __init__(self):
        super().__init__()
        self.history = []      # [(cpu, gpu, duty)]

    def set_history(self, history):
        self.history = history or []
        self.repaint()

    def paint(self, gc):
        if not self.visible:
            return
        x0, y0 = self.x, self.y
        w, h = self.w, self.h
        T.card(gc, x0, y0, w, h, 10, fill="#101821")
        gx0, gy0 = x0 + 30, y0 + 10
        gw, gh = w - 42, h - 32

        for i, v in enumerate((20, 40, 60, 80, 100)):
            gy = gy0 + gh * (1 - v / 100.0)
            T.card(gc, gx0, gy, gw, 1, 0, fill="#18222e")
            T.text(gc, str(v), gx0 - 6, gy - 6, font(9), C.mute, align="right")
        for i in range(1, 6):
            gxx = gx0 + gw * i / 6.0
            T.card(gc, gxx, gy0, 1, gh, 0, fill="#141d27")

        n = len(self.history)
        if n < 2:
            T.text(gc, "等待采样…", gx0 + gw / 2, gy0 + gh / 2 - 7, font(11), C.mute, align="center")
            return

        def series(idx, color, width=1.8):
            path = gc.CreatePath()
            for i, row in enumerate(self.history):
                v = max(0.0, min(100.0, float(row[idx])))
                x = gx0 + gw * i / (n - 1)
                y = gy0 + gh * (1 - v / 100.0)
                if i == 0:
                    path.MoveToPoint(x, y)
                else:
                    path.AddLineToPoint(x, y)
            gc.SetPen(T.pen(gc, color, width))
            gc.StrokePath(path)

        series(2, "#fbbf24", 1.0)
        series(0, "#38e1ff", 1.9)
        series(1, "#7c5cff", 1.9)
