# -*- coding: utf-8 -*-
"""键盘灯页：颜色、亮度、灯效。"""
from __future__ import annotations

from . import theme as T
from . import actions as A
from .kit import Button, Chip, Label, Node, Select, Slider, Switch
from .shell import Page
from .theme import C, font

M = 18


class Swatch(Node):
    def __init__(self, hexv, name, on_pick):
        super().__init__()
        self.hexv = hexv
        self.name = name
        self.on_pick = on_pick
        self.active = False
        self.cursor = "hand"

    def paint(self, gc):
        if not self.visible:
            return
        r = 8
        if self.hexv == "#000000":
            T.card(gc, self.x, self.y, self.w, self.h, r, fill="#0a1017", stroke=C.line)
            T.text(gc, "关", self.x + self.w / 2.0, self.y + self.h / 2.0 - 7,
                   font(10.5), C.mute, align="center")
        else:
            T.card(gc, self.x, self.y, self.w, self.h, r, fill=self.hexv)
        if self.active:
            T.card(gc, self.x - 3, self.y - 3, self.w + 6, self.h + 6, r + 3,
                   stroke=C.accent, stroke_w=2)
        elif self.hover:
            T.card(gc, self.x, self.y, self.w, self.h, r, stroke="#ffffff", stroke_w=1.5, alpha=140)

    def on_mouse(self, kind, px, py, evt=None):
        if kind == "up" and self.inside(px, py):
            self.on_pick(self.hexv, self.name)
            return True
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.repaint()
        return False

    def tip(self, px, py):
        return self.name


class LightPage(Page):
    title = "键盘灯"
    subtitle = "真彩灯效，改完立刻生效"

    def build(self):
        self.snap = {}
        self.swatches: list[Swatch] = []
        self.presets = []
        self.effects = []
        self._note_lines = 1
        self._light = {}
        self.bright = Slider(value=80, min_v=0, max_v=100, on_change=self._on_bright)
        self.speed = Slider(value=50, min_v=1, max_v=100, on_change=self._on_speed)
        self.zone = Select([], index=0, on_change=self._on_zone, w=170)
        self.on_switch = Switch(True, self._on_enabled)
        self.add(self.bright, self.speed, self.zone, self.on_switch)
        self.btns = []

    # ---- 布局 ----
    def layout(self):
        W, H = self.W, self.H
        y = 46
        lw = (W - M * 2) * 0.54
        rw = W - M * 2 - lw - 12
        ch = max(300.0, H - y - 14)

        # 左：颜色
        self._lrect = (M, y, lw, ch)
        sx = M + 16
        gap = 10
        cols = 4
        rows = max(1, (len(self.swatches) + cols - 1) // cols) if self.swatches else 3
        sw = (lw - 32 - gap * (cols - 1)) / float(cols)
        sh = min(sw * 0.62, 64.0)
        for i, s in enumerate(self.swatches):
            col, row = i % cols, i // cols
            s.place(sx + col * (sw + gap), y + 44 + row * (sh + gap), sw, sh)

        # 左：当前颜色 + 亮度（紧跟色块）
        by = y + 44 + rows * (sh + gap) + 16
        self._cur_y = by
        self.bright.place(sx, by + 106, lw - 32, 26)

        # 右：灯效
        self._rrect = (M + lw + 12, y, rw, ch)
        rx = M + lw + 12 + 16
        rwid = rw - 32
        cy = y + 44
        self.effect_rects = []
        cw = (rwid - 2 * 6) / 3.0
        for i, (key, label) in enumerate(self.effects):
            col, row = i % 3, i // 3
            self.effect_rects.append((rx + col * (cw + 6), cy + row * 32, cw, 26, key, label))
        cy += 2 * 32 + 12
        self._right_y = cy
        self.speed.place(rx, cy + 20, rwid, 26)
        cy += 76
        self._switch_y = cy
        self.on_switch.place(rx + rwid - 44, cy - 3, 44, 26)
        cy += 38
        self._zone_y = cy
        self.zone.place(rx + rwid - 170, cy - 4, 170, 30)

    # ---- 数据 ----
    def tick(self, snap):
        self.snap = snap
        light = snap.get("light") or {}
        if not self.swatches:
            for item in light.get("presets") or []:
                self.swatches.append(self.add(Swatch(item.get("hex"), item.get("name"),
                                                    self._pick_color)))
            self.effects = [(e.get("key"), e.get("label")) for e in (light.get("effects") or [])]
            self.presets = light.get("presets") or []
            self.zone.options = [("整块键盘（本机）", 0), ("左区", 1), ("中区", 2), ("右区", 3)]
            self.zone.index = int(light.get("zone") or 0)
            self.layout()
        cur = light.get("hex")
        for s in self.swatches:
            s.active = (s.hexv or "").lower() == (cur or "").lower()
        if not self.bright._drag:
            self.bright.set(light.get("brightness", 80))
        if not self.speed._drag:
            self.speed.set(light.get("speed", 50))
        if self.on_switch.value != bool(light.get("enabled")):
            self.on_switch.value = bool(light.get("enabled"))
        self._light = light
        self.request_repaint()

    # ---- 动作 ----
    def _pick_color(self, hexv, name):
        A.light_update(self.app.ctx, {"hex": hexv})
        self.app.toast("已换成「%s」" % name)

    def _on_bright(self, v):
        A.light_update(self.app.ctx, {"brightness": int(v)})

    def _on_speed(self, v):
        A.light_update(self.app.ctx, {"speed": int(v)})

    def _on_zone(self, zone):
        A.light_update(self.app.ctx, {"zone": int(zone)})

    def _on_enabled(self, on):
        A.light_update(self.app.ctx, {"enabled": bool(on)})
        self.app.toast("键盘灯已打开" if on else "键盘灯已关闭")

    def _set_effect(self, key):
        A.light_update(self.app.ctx, {"effect": key})
        self.app.toast("灯效已切换")

    def mouse(self, kind, x, y, evt=None):
        for (ex, ey, ew, eh, key, label) in getattr(self, "effect_rects", []):
            if ex <= x <= ex + ew and ey <= y <= ey + eh:
                if kind == "up":
                    self._set_effect(key)
                    return True
                if kind == "move":
                    self.cursor = "hand"
                    return False
        return super().mouse(kind, x, y, evt)

    # ---- 绘制 ----
    def paint(self, gc):
        light = getattr(self, "_light", {}) or {}
        lx, ly, lw, lh = self._lrect
        rx, ry, rw, rh = self._rrect

        # ---------- 左：颜色 ----------
        T.card(gc, lx, ly, lw, lh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "颜色", lx + 16, ly + 14, font(12.5, "semibold"), C.text)
        T.text(gc, "点一下立刻换上", lx + 16 + T.text_w(gc, "颜色", font(12.5, "semibold")) + 8,
               ly + 17, font(10.5), C.mute)
        for s in self.swatches:
            s.paint(gc)

        # 当前颜色 + 亮度
        by = self._cur_y
        T.card(gc, lx + 16, by, lw - 32, 1, 0, fill=C.line_soft)
        cur = light.get("effective_hex") or "#000000"
        T.card(gc, lx + 16, by + 16, 58, 58, 12, fill=cur, stroke=C.line_soft)
        T.text(gc, "当前生效", lx + 86, by + 22, font(11), C.mute)
        T.text(gc, str(light.get("hex") or "——").upper(), lx + 86, by + 40,
               font(14, "semibold", mono=True), C.text)
        rgb = list(light.get("color") or [0, 0, 0])[:3]
        T.text(gc, "RGB %d, %d, %d" % tuple(rgb), lx + 86, by + 62, font(10), C.mute)
        T.text(gc, "亮度 %d%%" % int(light.get("brightness", 0)),
               lx + lw - 16, by + 22, font(11), C.dim, align="right")
        self.bright.paint(gc)
        T.text(gc, "0", lx + 16, by + 136, font(9.5), C.mute)
        T.text(gc, "100", lx + lw - 16, by + 136, font(9.5), C.mute, align="right")
        self._paint_preview(gc, lx, by, lw, ly, lh, light)

        # ---------- 右：灯效 ----------
        T.card(gc, rx, ry, rw, rh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "灯效", rx + 16, ry + 14, font(12.5, "semibold"), C.text)
        cur_eff = light.get("effect") or "static"
        for (x, y, w, h, key, label) in self.effect_rects:
            on = key == cur_eff
            T.card(gc, x, y, w, h, 8,
                   fill=T.mix(C.accent, C.surface, 0.84) if on else C.surface2,
                   stroke=T.mix(C.accent, C.surface, 0.5) if on else C.line)
            T.text(gc, label, x + w / 2.0, y + (h - 15) / 2.0, font(11.5),
                   C.accent if on else C.dim, align="center")

        y = self._right_y
        T.text(gc, "速度", rx + 16, y, font(11), C.dim)
        self.speed.paint(gc)

        # 开关
        T.text(gc, "键盘灯开关", rx + 16, self._switch_y + 4, font(11.5), C.text)
        self.on_switch.paint(gc)

        # 分区
        T.text(gc, "分区", rx + 16, self._zone_y + 4, font(11.5), C.text)
        self.zone.paint(gc)
        note = light.get("zone_note") or "本机固件整块键盘一起控制，没有左右中分区。"
        self._wrap(gc, note, rx + 16, self._zone_y + 38, rw - 32, C.mute, 10.5)

        # 灯效说明：把右卡下半段填满，别留一大片空白
        dy = self._zone_y + 38 + max(1, int(self._note_lines)) * 17 + 14
        if dy < ry + rh - 70:
            T.card(gc, rx + 16, dy, rw - 32, 1, 0, fill=C.line_soft)
            T.text(gc, "这个灯效做什么", rx + 16, dy + 14, font(10.5), C.dim)
            desc = {
                "静态": "整块键盘恒亮同一个颜色，最省电，也最不打扰。",
                "呼吸": "亮度从暗到亮缓慢起伏，像深呼吸。",
                "彩虹流转": "颜色沿着键盘从左到右循环流动。",
                "色彩轮播": "整块键盘按色环依次换色。",
                "单色波浪": "同一个颜色像波浪一样扫过键盘。",
                "心跳": "两下快闪后停顿，像心跳的节奏。",
            }.get(self._cur_effect_label(), "选一个灯效，改完立刻生效。")
            self._wrap(gc, desc, rx + 16, dy + 34, rw - 32, C.mute, 10.5)

        # 底部提示
        T.text(gc, "改完自动保存，重启电脑后灯还是这个颜色",
               rx + 16, ry + rh - 24, font(10), C.mute)

    def _cur_effect_label(self):
        key = (getattr(self, "_light", {}) or {}).get("effect")
        for (k, label) in self.effects:
            if k == key:
                return label
        return "静态"

    def _paint_preview(self, gc, lx, by, lw, ly, lh, light):
        """色块下方的迷你键盘预览：一眼看出当前颜色、亮度和分区。"""
        top = by + 176
        bottom = ly + lh - 16
        if bottom - top < 90:
            return
        eff = light.get("effective_hex") or "#000000"
        on = bool(light.get("enabled"))
        zone = int(light.get("zone") or 0)
        T.text(gc, "预览", lx + 16, top, font(11), C.mute)
        T.text(gc, "整块键盘" if not zone else "分区亮灯", lx + lw - 16, top, font(10),
               C.dim, align="right")

        rows = [[1] * 13, [1] * 13, [1] * 12, [1.2, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1, 4.2]]
        gap = 4.0
        grid_w = lw - 32
        row_h = min(46.0, (bottom - top - 30 - gap * (len(rows) - 1)) / len(rows))
        if row_h < 8:
            return
        cy = top + 26
        for slots in rows:
            unit = (grid_w - gap * (len(slots) - 1)) / float(sum(slots))
            cx = lx + 16
            for s in slots:
                w = unit * s
                lit = on and (zone == 0 or self._zone_of(cx + w / 2.0, lx + 16, grid_w) == zone)
                if not on:
                    fill, edge = "#0b131b", C.line
                else:
                    fill = eff if lit else T.mix(eff, "#0a0f15", 0.82)
                    edge = T.mix(eff, "#ffffff", 0.6) if lit else C.line
                T.card(gc, cx, cy, w, row_h, min(6.0, row_h / 3.0), fill=fill, stroke=edge)
                cx += w + gap
            cy += row_h + gap
        T.text(gc, "亮度 %d%% · %s" % (int(light.get("brightness", 0)), self._cur_effect_label()),
               lx + 16, cy + 2, font(10), C.mute)

    @staticmethod
    def _zone_of(x, x0, w):
        t = (x - x0) / float(w or 1)
        return 1 if t < 1 / 3.0 else (2 if t < 2 / 3.0 else 3)

    def _wrap(self, gc, text, x, y, w, color, size):
        f = font(size)
        line = ""
        yy = y
        lines = 0
        for ch in text:
            if T.text_w(gc, line + ch, f) > w:
                T.text(gc, line, x, yy, f, color)
                lines += 1
                line = ch
                yy += size * 1.55
            else:
                line += ch
        if line:
            T.text(gc, line, x, yy, f, color)
            lines += 1
        self._note_lines = lines
        return lines

    def on_show(self):
        light = self.snap.get("light") or getattr(self, "_light", {}) or {}
        owners = light.get("owners") or []
        if light.get("available") is False:
            self.app.toast("没找到键盘灯通道，基础模式仍可用")
        elif owners:
            self.app.toast("检测到 %d 个程序也在控灯" % len(owners))
