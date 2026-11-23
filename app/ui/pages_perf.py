# -*- coding: utf-8 -*-
"""性能模式页：蓝天 EC 的四档功耗策略（省电 / 静音 / 娱乐 / 性能）。

这一页只做一件事：把那四档做成四张能点的卡片，点哪张就把 EC 切到哪档，
并且把「这档到底改了什么」写清楚——因为它在 EC 里，改的是功耗上限和
固件风扇曲线，而不是我们自己的风扇设置。
"""
from __future__ import annotations

import threading
import time

import wx

from . import theme as T
from . import actions as A
from .kit import Node
from .shell import Page
from .theme import C, font

M = 18


def _wrap(gc, text, x, y, w, color, size, max_lines=2, lh=1.62):
    """折行：优先在标点后断开，超过 max_lines 就省略号收尾。

    中文按字硬切会切出「热。」这种孤字行，所以到达宽度时先往回找最近的
    标点（且不能把这一行削掉一半以上），找到了就在标点后断。
    """
    f = font(size)
    lines, cur = [], ""
    for ch in str(text):
        if T.text_w(gc, cur + ch, f) > w:
            cut = cur
            for i in range(len(cur) - 1, max(1, int(len(cur) * 0.6)), -1):
                if cur[i] in "，。、；：！？,;":
                    cut = cur[: i + 1]
                    break
            lines.append(cut)
            cur = cur[len(cut):] + ch
            if len(lines) >= max_lines:
                break
        else:
            cur += ch
    if cur and len(lines) < max_lines:
        lines.append(cur)
    elif cur and len(lines) >= max_lines:
        lines[-1] = lines[-1][:-1] + "…"
    # 不要留下只有标点的一行（中文按字切很容易切出个「。」）
    if len(lines) > 1 and len(lines[-1].strip("。．.，,、")) == 0:
        lines.pop()
    for i, line in enumerate(lines):
        T.text(gc, line, x, y + i * size * lh, f, color, max_w=w)
    return y + len(lines) * size * lh


def _mode_icon(gc, key, cx, cy, color, bg=C.surface2):
    """用几何图形画模式图标，不依赖字体或图片。"""
    if key == "quiet":            # 静音：月亮（用底色挖掉一块）
        T.card(gc, cx - 9, cy - 9, 18, 18, 9, fill=color)
        T.card(gc, cx - 3, cy - 12, 17, 17, 8.5, fill=bg)
    elif key == "powersave":      # 省电：电池
        T.card(gc, cx - 9, cy - 6, 16, 12, 3, stroke=color, stroke_w=1.6)
        T.card(gc, cx + 7, cy - 3, 2.6, 6, 1.2, fill=color)
        T.card(gc, cx - 6.5, cy - 3.5, 5, 7, 1.4, fill=color)
    elif key == "performance":    # 性能：闪电
        path = gc.CreatePath()
        path.MoveToPoint(cx + 1, cy - 10)
        path.AddLineToPoint(cx - 7, cy + 1.5)
        path.AddLineToPoint(cx - 1, cy + 1.5)
        path.AddLineToPoint(cx - 3, cy + 10)
        path.AddLineToPoint(cx + 7, cy - 2)
        path.AddLineToPoint(cx + 1, cy - 2)
        path.CloseSubpath()
        gc.SetBrush(T.brush(gc, color))
        gc.FillPath(path)
    else:                          # 娱乐：播放三角
        path = gc.CreatePath()
        path.MoveToPoint(cx - 6, cy - 8)
        path.AddLineToPoint(cx + 8, cy)
        path.AddLineToPoint(cx - 6, cy + 8)
        path.CloseSubpath()
        gc.SetBrush(T.brush(gc, color))
        gc.FillPath(path)


class ModeCard(Node):
    """一张可点的模式卡。"""

    def __init__(self, mode, on_pick):
        super().__init__()
        self.mode = mode
        self.on_pick = on_pick
        self.active = False
        self.busy = False
        self.cursor = "hand"

    def paint(self, gc):
        m = self.mode
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.active:
            fill = T.mix(C.accent, C.surface, 0.90)
            stroke, sw = C.accent_dim, 1.6
        else:
            fill = C.surface2 if self.hover else C.surface
            stroke, sw = (C.line_strong if self.hover else C.line), 1.0
        T.card(gc, x, y, w, h, 14, fill=fill, stroke=stroke, stroke_w=sw,
               top_glow="#1b2836" if self.active else None)

        color = C.accent if self.active else (C.dim if self.hover else C.mute)
        # 图标块
        icon_bg = T.mix(color, C.bg, 0.82) if self.active else C.surface3
        T.card(gc, x + 16, y + 16, 34, 34, 11, fill=icon_bg)
        _mode_icon(gc, m["key"], x + 33, y + 33, color, icon_bg)

        # 名称 + 英文
        T.text(gc, m["name"], x + 62, y + 16, font(17, "semibold"),
               C.text if (self.active or self.hover) else "#d5e2ee")
        nw = T.text_w(gc, m["name"], font(17, "semibold"))
        T.text(gc, m["en"], x + 62 + nw + 8, y + 22, font(10), C.mute)

        # 右上角标签：当前 / 出厂默认
        if self.active:
            label, fg, bg = ("切换中…" if self.busy else "当前"), C.accent, T.mix(C.accent, C.bg, 0.80)
        else:
            label, fg, bg = m["tag"], C.dim, C.surface3
        tw = T.text_w(gc, label, font(10))
        T.pill(gc, x + w - 16 - (tw + 16), y + 14, tw + 16, 19, label, fg, bg, f=font(10))

        # 一句话说明 + 两行细节
        T.text(gc, m["short"], x + 16, y + 58, font(11.5), C.dim, max_w=w - 32)
        _wrap(gc, m["desc"], x + 16, y + 79, w - 32, C.mute, 10.2, max_lines=2)

        # 底部三个小指标
        by = y + h - 40
        T.card(gc, x + 16, by - 8, w - 32, 1, 0, fill=C.line_soft)
        cols = (("CPU", m["cpu"]), ("GPU", m["gpu"]), ("固件风扇", m["fan"]))
        cw = (w - 32) / 3.0
        for i, (k, v) in enumerate(cols):
            bx = x + 16 + cw * i
            T.text(gc, k, bx, by + 2, font(9.5), C.mute)
            T.text(gc, v, bx, by + 17, font(11.5, "semibold"),
                   C.accent if self.active else C.dim)

        if self.hover and not self.active:
            T.text(gc, "点击切换", x + w - 16, by + 17, font(10), C.accent, align="right")

    def on_mouse(self, kind, px, py, evt=None):
        if kind == "up" and self.inside(px, py):
            if self.on_pick:
                self.on_pick(self.mode["id"])
            return True
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.repaint()
        return False

    def tip(self, px, py):
        return self.mode["desc"]


class PerfPage(Page):
    title = "性能模式"
    subtitle = "蓝天 EC 的四档功耗策略：省电 / 静音 / 娱乐 / 性能"

    def build(self):
        self.snap = {}
        self.cards = []
        for m in A.perf_modes():
            card = ModeCard(m, self._pick)
            self.cards.append(card)
            self.add(card)
        self._busy_id = None
        self._msg = ""
        self._msg_until = 0.0

    def layout(self):
        W, H = self.W, self.H
        top = 46
        self._hero = (M, top, W - M * 2, 84)
        gy = top + 84 + 12
        cw = (W - M * 2 - 12) / 2.0
        ch = 166.0
        for i, card in enumerate(self.cards):
            card.place(M + (cw + 12) * (i % 2), gy + (ch + 12) * (i // 2), cw, ch)
        by = gy + ch * 2 + 12
        bh = max(168.0, H - by - 14)
        half = (W - M * 2 - 12) / 2.0
        self._what = (M, by, half, bh)
        self._how = (M + half + 12, by, half, bh)

    # ------------------------------------------------------------------ 数据
    def tick(self, snap):
        self.snap = snap
        perf = snap.get("perf") or {}
        cur = perf.get("mode") if perf.get("known") else None
        for card in self.cards:
            active = cur is not None and card.mode["id"] == int(cur)
            busy = self._busy_id == card.mode["id"]
            if active != card.active or busy != card.busy:
                card.active = active
                card.busy = busy
        self.request_repaint()

    def _perf(self):
        return (self.snap or {}).get("perf") or {}

    def _current(self):
        perf = self._perf()
        if not perf.get("known"):
            return None
        for m in A.perf_modes():
            if m["id"] == perf.get("mode"):
                return m
        return None

    # ------------------------------------------------------------------ 切换
    def _pick(self, mode_id):
        if self._busy_id is not None:
            return
        self._busy_id = mode_id
        self._msg = ""
        self.request_repaint()

        def run():
            try:
                res = A.set_perf_mode(self.app.ctx, mode_id)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            wx.CallAfter(self._done, res)

        threading.Thread(target=run, name="perf-switch", daemon=True).start()

    def _done(self, res):
        self._busy_id = None
        if res.get("ok"):
            self._msg = "已切到「%s」，CPU/GPU 功耗上限和固件风扇曲线同时生效" % res.get("name", "")
        else:
            self._msg = "切换失败：%s" % (res.get("reason") or "EC 没有回应")
        self._msg_until = time.time() + 6.0
        if hasattr(self.app, "toast"):
            self.app.toast(self._msg)
        self.request_repaint()

    # ------------------------------------------------------------------ 绘制
    def paint(self, gc):
        W, H = self.W, self.H
        self._paint_hero(gc, *self._hero)
        for card in self.cards:
            card.paint(gc)
        self._paint_what(gc, *self._what)
        self._paint_how(gc, *self._how)

    def _paint_hero(self, gc, x, y, w, h):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line, top_glow="#1b2836")
        cur = self._current()
        T.card(gc, x + 16, y + 16, 4, h - 32, 2, fill=C.accent)

        if cur:
            name, desc = cur["name"], cur["desc"]
            sub = "CPU 上限「%s」· GPU 上限「%s」· 固件风扇曲线「%s」" % (
                cur["cpu"], cur["gpu"], cur["fan"])
        else:
            name = "未知"
            desc = "这一档只写不读，程序没法回读 EC 当前值。"
            sub = "开机后没在本程序里切过，就不知道 BIOS 里设的是哪一档。"

        T.text(gc, name, x + 30, y + 14, font(20, "semibold"), C.text)
        nw = T.text_w(gc, name, font(20, "semibold"))
        perf = self._perf()
        if perf.get("available"):
            tag, color = "通道就绪", C.ok
        else:
            tag, color = "通道不可用", C.warn
        tw = T.text_w(gc, tag, font(10))
        T.pill(gc, x + 30 + nw + 12, y + 17, tw + 18, 20, tag, color,
               T.mix(color, C.surface, 0.86), f=font(10))

        T.text(gc, sub, x + 30, y + 44, font(11.5), C.dim, max_w=w - 220)
        if self._msg and time.time() < self._msg_until:
            T.text(gc, self._msg, x + 30, y + 63, font(10.5),
                   C.ok if self._current() else C.warn, max_w=w - 60)
        else:
            T.text(gc, desc, x + 30, y + 63, font(10.5), C.mute, max_w=w - 60)

        # 右侧：EC 命令与开关次数，做成两个小指标
        rx = x + w - 16
        items = [("EC 命令", "0x79 / 0x19"), ("上次切换", self._when())]
        for label, val in reversed(items):
            vw = T.text_w(gc, val, font(13, "semibold"))
            T.text(gc, val, rx, y + 46, font(13, "semibold"), C.text, align="right")
            T.text(gc, label, rx, y + 26, font(9.5), C.mute, align="right")
            rx -= max(vw, 70) + 28

    def _when(self):
        ts = self._perf().get("ts") or 0
        if not ts:
            return "还没有"
        try:
            import time as _t

            t = _t.localtime(float(ts))
            today = _t.localtime()[:3] == t[:3]
            return _t.strftime("%H:%M" if today else "%m-%d %H:%M", t)
        except Exception:
            return "--"

    def _paint_what(self, gc, x, y, w, h):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "这一档改了什么", x + 16, y + 12, font(12.5, "semibold"), C.text)
        cur = self._current()
        if not cur:
            rows = [
                ("CPU 功耗上限", "由 EC 决定"),
                ("GPU 功耗上限", "由 EC 决定"),
                ("睿频策略", "由 EC 决定"),
                ("固件风扇曲线", "由 EC 决定"),
                ("典型场景", "由 EC 决定"),
                ("噪音和温度", "由 EC 决定"),
            ]
            color = C.mute
        else:
            turbo = {
                "performance": "放开，长时间保持高频",
                "entertainment": "正常，按需睿频",
                "quiet": "受限，峰值压一半",
                "powersave": "基本关闭，只跑基频",
            }.get(cur["key"], "正常")
            scene = {
                "performance": "游戏、渲染、编译",
                "entertainment": "办公、看片、轻度游戏",
                "quiet": "深夜码字、会议、看片",
                "powersave": "电池模式、纯文档",
            }.get(cur["key"], "日常使用")
            rows = [
                ("CPU 功耗上限", "高（PL1/PL2 全开）" if cur["cpu"] == "高" else
                 ("中，够用但不猛" if cur["cpu"] == "中" else "低，压得住温度")),
                ("GPU 功耗上限", "高" if cur["gpu"] == "高" else
                 ("中" if cur["gpu"] == "中" else "低")),
                ("睿频策略", turbo),
                ("固件风扇曲线", {"积极": "更早拉高转速", "标准": "按出厂曲线走",
                                  "安静": "晚拉、低转速"}.get(cur["fan"], "按出厂曲线走")),
                ("典型场景", scene),
                ("噪音和温度", {"积极": "偏吵，机身偏凉", "标准": "正常",
                                "安静": "安静，机身穿一点"}.get(cur["fan"], "正常")),
            ]
            color = C.dim
        yy = y + 44
        for k, v in rows:
            T.text(gc, k, x + 16, yy, font(11), C.mute)
            T.text(gc, v, x + 118, yy, font(11), color, max_w=w - 140)
            yy += 26
        note = ("这四档不碰我们自己的风扇曲线：风扇页里设的手动/曲线仍然优先。"
                if cur else "先在上面点一档，这里会列出它具体动了哪些东西。")
        T.text(gc, note, x + 16, y + h - 26, font(10), C.mute, max_w=w - 32)

    def _paint_how(self, gc, x, y, w, h):
        T.card(gc, x, y, w, h, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "怎么下发、有什么限制", x + 16, y + 12, font(12.5, "semibold"), C.text)
        perf = self._perf()
        rows = [
            ("通道", "Insyde DCHU（64 位宿主代发）"),
            ("命令", "EC 0x79 · 子功能 0x19 · 参数 = 模式号"),
            ("谁来生效", "EC 固件，秒级生效，不用重启"),
            ("掉电后", "EC 会保持，重启后还是这一档"),
            ("BIOS 默认", "不改。想改开机默认值要进 BIOS 设"),
            ("能否回读", "不能，这条命令只写不读"),
            ("和风扇页", "不冲突。风扇设「自动」就吃固件曲线，设手动/曲线是我们盖住它"),
            ("和谁冲突", "蓝天 Control Center 会抢这条命令，以最后动的为准"),
            ("失败了会怎样", "风扇设置不受影响，功耗策略保持原样"),
            ("需要管理员吗", "不需要，DCHU 驱动自己带权限"),
        ]
        yy = y + 42
        for k, v in rows:
            T.text(gc, k, x + 16, yy, font(10.5), C.mute)
            T.text(gc, v, x + 86, yy, font(10.5), C.dim, max_w=w - 104)
            yy += 21
        if not perf.get("available"):
            reason = perf.get("reason") or "找不到 InsydeDCHU.dll"
            T.text(gc, "通道不可用：" + str(reason), x + 16, y + h - 24, font(10),
                   C.warn, max_w=w - 32)
