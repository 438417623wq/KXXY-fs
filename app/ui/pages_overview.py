# -*- coding: utf-8 -*-
"""概览页：温度、风量、实时曲线、最近动作。全部手绘，没有子窗口。"""
from __future__ import annotations

import time

from . import theme as T
from .kit import Node
from .shell import Page
from .theme import C, font
from .charts import interp

M = 18


def metric(gc, x, y, w, label, value, unit="", color=None, size=26):
    """指标块：大数字 + 单位 + 小标签。"""
    color = color or C.text
    v = f"{value}"
    f = font(size, "semibold")
    T.text(gc, v, x, y, f, color)
    vw = T.text_w(gc, v, f)
    if unit:
        T.text(gc, unit, x + vw + 3, y + size * 0.52, font(11), C.mute)
    T.text(gc, label, x, y + size * 1.28, font(10.5), C.mute)


class OverviewPage(Page):
    title = "概览"
    subtitle = "现在什么状态，一眼看完"

    def build(self):
        self.snap = {}

    def tick(self, snap):
        self.snap = snap
        self.request_repaint()

    # ------------------------------------------------------------------ 绘制
    def paint(self, gc):
        W, H = self.W, self.H
        snap = self.snap or {}
        fans = snap.get("fans") or {}
        state = snap.get("state") or {}
        hw = W - M * 2

        y = 46
        hero_h = 92
        self._hero(gc, M, y, hw, hero_h, fans, state)

        y += hero_h + 12
        bottom_h = 196
        chart_h = max(150, H - y - bottom_h - 12 - 12)
        self._dbg = (W, H, y, chart_h)
        self._chart(gc, M, y, hw, chart_h, state.get("history") or [])

        y += chart_h + 12
        bw = (hw - 12) / 2.0
        self._policy(gc, M, y, bw, bottom_h, snap)
        self._actions(gc, M + bw + 12, y, bw, bottom_h, snap)

    # --- 顶部状态条 ---
    def _hero(self, gc, x, y, w, h, fans, state):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line, top_glow="#1b2836")
        # 左侧：当前策略
        mode, label, desc = self._mode_of(fans)
        T.card(gc, x + 16, y + 18, 4, h - 36, 2, fill=C.accent)
        T.text(gc, label, x + 30, y + 16, font(19, "semibold"), C.text)
        T.text(gc, desc, x + 30, y + 46, font(11.5), C.mute)

        # 右侧：三个指标
        data = [
            ("CPU", self._temp(fans, 1), "°C", "#38e1ff"),
            ("GPU", self._temp(fans, 2), "°C", "#7c5cff"),
            ("合计风量", self._rpm(fans), "RPM", "#fbbf24"),
        ]
        colw = 132
        cx = x + w - 16 - colw * 3
        for i, (lb, v, unit, color) in enumerate(data):
            bx = cx + i * colw
            T.card(gc, bx - 10, y + 20, 1, h - 40, 0, fill=C.line_soft)
            metric(gc, bx + 6, y + 24, colw - 20, lb + " 温度" if unit == "°C" else lb,
                   v if v is not None else "--", unit, color,
                   26 if unit == "°C" else 22)

        # 标题右侧：状态胶囊
        alerts = state.get("alerts") or []
        fuse = state.get("fuse")
        if fuse:
            tag, color = "熔断保护中", C.danger
        elif alerts:
            tag, color = "有提醒 %d 条" % len(alerts), C.warn
        elif state.get("degraded"):
            tag, color = "部分降级", C.warn
        else:
            tag, color = "运行正常", C.ok
        tw = T.text_w(gc, tag, font(10))
        T.pill(gc, x + 30 + T.text_w(gc, label, font(19, "semibold")) + 12, y + 17,
               tw + 18, 20, tag, color, T.mix(color, C.surface, 0.86))

    def _mode_of(self, fans):
        modes = [f.get("mode") for f in fans.values()]
        if not modes:
            return "auto", "等待数据", ""
        uniq = set(modes)
        if uniq == {"auto"}:
            return "auto", "电脑自己管", "没有做任何干预，主板按自己的温控曲线走。"
        if uniq == {"curve"}:
            return "curve", "按曲线走", "风扇跟着你设的温度曲线调整，热了才加速。"
        if uniq == {"manual"}:
            duty = next(iter(fans.values())).get("manual_duty", 50)
            return "manual", "固定 %d%% 风量" % duty, "不随温度变化，一直保持这个转速。"
        return "mixed", "混合设置", "两个风扇用了不同方式，可在「风扇」页分别调整。"

    @staticmethod
    def _temp(fans, no):
        t = (fans.get(str(no)) or {}).get("temp", -1)
        return int(t) if isinstance(t, (int, float)) and t >= 0 else None

    @staticmethod
    def _rpm(fans):
        out = 0
        for f in fans.values():
            r = f.get("rpm", -1)
            if isinstance(r, (int, float)) and r > 0:
                out += int(r)
        return out if out else None

    # --- 实时曲线 ---
    def _chart(self, gc, x, y, w, h, history):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "实时曲线", x + 16, y + 12, font(12.5, "semibold"), C.text)
        # 图例
        lg = [("CPU", "#38e1ff"), ("GPU", "#7c5cff"), ("占空比", "#fbbf24")]
        lx = x + w - 16
        for name, color in reversed(lg):
            tw = T.text_w(gc, name, font(10.5))
            T.text(gc, name, lx - tw, y + 15, font(10.5), C.dim)
            T.card(gc, lx - tw - 14, y + 20, 8, 3, 1.5, fill=color)
            lx -= tw + 24

        gx0, gy0 = x + 40, y + 42
        gw, gh = w - 56, h - 64
        if gw < 40 or gh < 40:
            return
        for v in (20, 40, 60, 80, 100):
            gy = gy0 + gh * (1 - v / 100.0)
            T.card(gc, gx0, gy, gw, 1, 0, fill="#17222e")
            T.text(gc, str(v), gx0 - 8, gy - 7, font(9), C.mute, align="right")

        rows = history[-120:]
        n = len(rows)
        if n < 2:
            T.text(gc, "正在采集…", gx0 + gw / 2, gy0 + gh / 2 - 7, font(11), C.mute, align="center")
            return

        def draw(key, color, width, alpha=255):
            path = gc.CreatePath()
            for i, row in enumerate(rows):
                raw = row.get(key, -1)
                if not isinstance(raw, (int, float)) or raw < 0:
                    raw = 0
                v = max(0.0, min(100.0, float(raw)))
                px = gx0 + gw * i / (n - 1)
                py = gy0 + gh * (1 - v / 100.0)
                if i == 0:
                    path.MoveToPoint(px, py)
                else:
                    path.AddLineToPoint(px, py)
            gc.SetPen(T.pen(gc, color, width, alpha))
            gc.StrokePath(path)

        draw("cpu_duty", "#fbbf24", 1.1, 150)
        draw("gpu", "#7c5cff", 1.8)
        draw("cpu", "#38e1ff", 1.8)

    # --- 策略状态 ---
    def _policy(self, gc, x, y, w, h, snap):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "策略状态", x + 16, y + 12, font(12.5, "semibold"), C.text)
        fans = snap.get("fans") or {}
        safety = snap.get("safety") or {}
        yy = y + 40
        mode_text = {"auto": "电脑自己管", "curve": "按曲线", "manual": "固定转速"}
        for no in (1, 2):
            fan = fans.get(str(no)) or {}
            name = fan.get("label") or ("风扇%d" % no)
            mode = mode_text.get(fan.get("mode"), "未知")
            duty = fan.get("duty", -1)
            rpm = fan.get("rpm", -1)
            T.text(gc, name, x + 16, yy, font(11.5), C.dim)
            T.text(gc, mode, x + 86, yy, font(11.5), C.text)
            right = ("%d%% · %d RPM" % (duty, rpm)) if duty >= 0 else "—"
            T.text(gc, right, x + w - 16, yy, font(11), C.dim, align="right")
            yy += 26
        T.card(gc, x + 16, yy + 2, w - 32, 1, 0, fill=C.line_soft)
        yy += 12
        T.text(gc, "最低占空比", x + 16, yy, font(11), C.mute)
        T.text(gc, "%d%%" % int(safety.get("min_duty", 20)), x + w - 16, yy,
               font(11), C.dim, align="right")
        yy += 24
        T.text(gc, "熔断温度", x + 16, yy, font(11), C.mute)
        T.text(gc, "%d°C" % int(safety.get("fuse_temp", 92)), x + w - 16, yy,
               font(11), C.dim, align="right")

    # --- 最近动作 ---
    def _actions(self, gc, x, y, w, h, snap):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "最近动作", x + 16, y + 12, font(12.5, "semibold"), C.text)
        entries = ((snap.get("guard") or {}).get("entries") or [])[-5:]
        cnt = len(((snap.get("guard") or {}).get("entries") or []))
        if cnt:
            tw = T.text_w(gc, "%d 条" % cnt, font(10))
            T.pill(gc, x + w - tw - 28, y + 10, tw + 16, 20, "%d 条" % cnt, C.dim, C.surface3)
        if not entries:
            T.text(gc, "还没有动过风扇 —— 这是最安全的状态。", x + 16, y + 44, font(11), C.mute)
            return
        yy = y + 40
        for e in entries:
            when = T.ts_short(e.get("ts"))
            act = e.get("kind") or e.get("action") or ""
            obj = e.get("target") or ""
            ok = e.get("status") != "failed"
            T.text(gc, when, x + 16, yy, font(10, mono=True), C.mute)
            T.text(gc, str(act), x + 78, yy, font(11), C.dim, max_w=w - 180)
            T.text(gc, str(obj), x + w - 60, yy, font(10.5, mono=True), C.mute, align="right")
            T.card(gc, x + w - 26, yy + 4, 7, 7, 3.5, fill=C.ok if ok else C.danger)
            yy += 24
