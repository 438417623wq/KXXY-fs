# -*- coding: utf-8 -*-
"""风扇页：场景、我的方案、CPU/GPU 两张卡（模式、固定转速、温度曲线）。"""
from __future__ import annotations

import time

from . import theme as T
from . import actions as A
from .charts import CurveEditor, interp
from .kit import Button, Chip, Label, Node, Segmented, Slider
from .shell import Page
from .theme import C, font

M = 18

SCENES = [
    ("quiet", "安静", "几乎听不到声音"),
    ("balanced", "均衡", "平时安静，热了才加速"),
    ("performance", "性能", "早一点加大风量"),
    ("max", "全速", "最凉，但很吵"),
]


def _scene_icon(gc, key, cx, cy, color):
    """用几何图形画场景图标，不依赖字体或图片。"""
    if key == "quiet":
        T.card(gc, cx - 8, cy - 8, 16, 16, 8, fill=color)
        T.card(gc, cx - 2, cy - 11, 14, 14, 7, fill=C.surface)
    elif key == "balanced":
        for dy, off in ((-4.5, 0), (4.5, 6)):
            T.card(gc, cx - 9, cy + dy - 1, 12, 2, 1, fill=color)
            T.card(gc, cx + 1 + off - 3, cy + dy - 3, 6, 6, 3, fill=color)
    elif key == "performance":
        path = gc.CreatePath()
        path.MoveToPoint(cx + 1, cy - 9)
        path.AddLineToPoint(cx - 6, cy + 1)
        path.AddLineToPoint(cx - 0.5, cy + 1)
        path.AddLineToPoint(cx - 2, cy + 9)
        path.AddLineToPoint(cx + 6, cy - 1.5)
        path.AddLineToPoint(cx + 0.5, cy - 1.5)
        path.CloseSubpath()
        gc.SetBrush(T.brush(gc, color))
        gc.FillPath(path)
    else:  # max
        for ang in (0, 120, 240):
            import math
            a = math.radians(ang - 90)
            T.card(gc, cx + math.cos(a) * 6 - 1.4, cy + math.sin(a) * 6 - 1.4, 2.8, 2.8, 1.4, fill=color)
        T.card(gc, cx - 3, cy - 3, 6, 6, 3, fill=color)


class SceneTile(Node):
    def __init__(self, page, key, name, desc, on_click):
        super().__init__()
        self.key = key
        self.name = name
        self.desc = desc
        self.on_click = on_click
        self.active = False
        self.cursor = "hand"

    def paint(self, gc):
        if not self.visible:
            return
        if self.active:
            T.card(gc, self.x, self.y, self.w, self.h, 12,
                   fill=T.mix(C.accent, C.surface, 0.86), stroke=T.mix(C.accent, C.surface, 0.5))
        else:
            fill = C.surface2 if self.hover else C.surface
            T.card(gc, self.x, self.y, self.w, self.h, 12, fill=fill,
                   stroke=C.line_strong if self.hover else C.line)
        color = C.accent if self.active else C.dim
        _scene_icon(gc, self.key, self.x + 22, self.y + self.h / 2.0, color)
        T.text(gc, self.name, self.x + 40, self.y + 11,
               font(12.5, "semibold"), C.accent if self.active else C.text)
        T.text(gc, self.desc, self.x + 40, self.y + 30, font(10.5),
               C.dim if self.active else C.mute, max_w=self.w - 52)

    def on_mouse(self, kind, px, py, evt=None):
        if kind == "up" and self.inside(px, py):
            self.on_click(self.key)
            return True
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover:
                self.hover = h
                self.repaint()
        return False


class ProfileTile(Node):
    def __init__(self, page, prof, on_apply, on_delete):
        super().__init__()
        self.prof = prof
        self.on_apply = on_apply
        self.on_delete = on_delete
        self.cursor = "hand"
        self.del_hover = False

    def _del_rect(self):
        return self.x + self.w - 26, self.y + self.h / 2.0 - 8, 16, 16

    def paint(self, gc):
        if not self.visible:
            return
        T.card(gc, self.x, self.y, self.w, self.h, 11,
               fill=C.surface2 if self.hover else C.surface,
               stroke=C.line_strong if self.hover else C.line)
        T.card(gc, self.x + 10, self.y + self.h / 2.0 - 7, 3, 14, 1.5, fill=C.accent_dim)
        T.text(gc, self.prof.get("name", "方案"), self.x + 22, self.y + 9,
               font(11.5, "semibold"), C.text, max_w=self.w - 50)
        fans = self.prof.get("fans") or {}
        bits = []
        for no in ("1", "2"):
            m = (fans.get(no) or {}).get("mode")
            bits.append({"auto": "自动", "manual": "固定", "curve": "曲线"}.get(m, "—"))
        T.text(gc, " / ".join(bits), self.x + 22, self.y + 28, font(10), C.mute)
        dx, dy, dw, dh = self._del_rect()
        if self.del_hover:
            T.card(gc, dx, dy, dw, dh, 8, fill="#3a1e22")
        T.text(gc, "×", dx + dw / 2.0, dy + 1, font(13), C.danger if self.del_hover else C.mute,
               align="center")

    def on_mouse(self, kind, px, py, evt=None):
        dx, dy, dw, dh = self._del_rect()
        over_del = dx <= px <= dx + dw and dy <= py <= dy + dh
        if kind == "move":
            h = self.inside(px, py)
            if h != self.hover or over_del != self.del_hover:
                self.hover, self.del_hover = h, over_del
                self.repaint()
            return False
        if kind == "up" and self.inside(px, py):
            if over_del:
                self.on_delete(self.prof.get("id"))
            else:
                self.on_apply(self.prof.get("id"))
            return True
        return False

    def tip(self, px, py):
        return "点一下套用这个方案" if not self.del_hover else "删除这个方案"


class FanCard(Node):
    """一张风扇卡：模式三选一 + 固定转速 + 温度曲线。"""

    def __init__(self, page, no, label):
        super().__init__()
        self.page_ref = page
        self.no = no
        self.label_text = label
        self.mode = "auto"
        self.temp = -1
        self.rpm = -1
        self.duty = -1
        self._last_duty_push = 0.0
        self.curve_key = "cpu" if no == 1 else "gpu"

        self.seg = Segmented([("自动", "auto"), ("固定", "manual"), ("曲线", "curve")],
                             index=0, on_change=self._on_mode)
        self.slider = Slider(value=50, on_change=self._on_duty, min_v=0, max_v=100)
        self.editor = CurveEditor(
            points=[], on_change=lambda pts: self._on_curve(pts),
            accent="#38e1ff" if no == 1 else "#7c5cff")
        self.chips = [
            Chip("更安静", lambda: self._nudge(0.86)),
            Chip("更凉快", lambda: self._nudge(1.16)),
            Chip("恢复默认", lambda: self._reset_curve()),
        ]
        self.children = [self.seg, self.slider, self.editor] + self.chips
        for c in self.children:
            c.page = page

    # ---- 交互 ----
    def _on_mode(self, mode):
        A.set_mode(self.page_ref.app.ctx, self.no, mode,
                   int(self.slider.value) if mode == "manual" else None)
        self.page_ref.app.toast("风扇%d 已切到%s" % (
            self.no, {"auto": "电脑自己管", "manual": "固定转速", "curve": "温度曲线"}.get(mode, mode)))

    def _on_duty(self, duty):
        now = time.time()
        if now - self._last_duty_push < 0.12:
            return
        self._last_duty_push = now
        A.set_mode(self.page_ref.app.ctx, self.no, "manual", duty)

    def _nudge(self, k):
        pts = [[p[0], max(0.0, min(100.0, p[1] * k))] for p in self.editor.points]
        self.editor.set_points(pts, notify=True)
        self.page_ref.app.toast("曲线已%s" % ("压低" if k < 1 else "提高"))

    def _reset_curve(self):
        from control.curve import DEFAULT_CURVES
        self.editor.set_points(DEFAULT_CURVES.get(self.curve_key, []), notify=True)
        self.page_ref.app.toast("已恢复默认曲线")

    def _on_curve(self, pts):
        A.set_curve(self.page_ref.app.ctx, self.curve_key, pts)

    # ---- 布局 ----
    def layout(self):
        pad = 16
        x, y, w, h = self.x, self.y, self.w, self.h
        cx = x + pad
        cw = w - pad * 2
        yy = y + 14
        self.seg.place(cx + cw - 186, yy + 2, 186, 26)
        yy += 38
        self.slider.place(cx, yy + 16, cw, 24)
        yy += 46
        # 曲线标题行：三个 chip 右对齐，左边留给标题和摘要
        widths = [74, 74, 84]
        total = sum(widths) + 6 * (len(widths) - 1)
        chip_x = cx + cw - total
        for c, wd in zip(self.chips, widths):
            c.place(chip_x, yy, wd, 26)
            chip_x += wd + 6
        yy += 36
        eh = max(120.0, y + h - pad - yy)
        self.editor.place(cx, yy, cw, eh)

    # ---- 绘制 ----
    def paint(self, gc):
        if not self.visible:
            return
        T.card(gc, self.x, self.y, self.w, self.h, 14, fill=C.surface, stroke=C.line,
               top_glow="#1a2532")
        pad = 16
        cx = self.x + pad
        T.text(gc, self.label_text, cx, self.y + 14, font(14, "semibold"), C.text)
        # 数值
        mv = "%d°C" % self.temp if self.temp >= 0 else "--"
        rv = "%d" % self.rpm if self.rpm >= 0 else "--"
        dv = "%d%%" % self.duty if self.duty >= 0 else "--"
        tw = T.text_w(gc, self.label_text, font(14, "semibold"))
        bx = cx + tw + 14
        for txt, color, size in ((mv, "#38e1ff" if self.no == 1 else "#7c5cff", 13),
                                 (rv + " RPM", C.dim, 11.5),
                                 (dv, C.dim, 11.5)):
            T.text(gc, txt, bx, self.y + 16, font(size), color)
            bx += T.text_w(gc, txt, font(size)) + 14

        # 滑块标签
        sy = self.slider.y
        T.text(gc, "固定转速", cx, sy - 16, font(11), C.dim)
        T.text(gc, "%d%%" % int(self.slider.value), cx + self.slider.w, sy - 16,
               font(11.5, "semibold"), C.accent, align="right")

        # 曲线标题 + 摘要（与 chips 同一行，靠左）
        cy = self.chips[0].y
        T.text(gc, "温度曲线", cx, cy + 6, font(11), C.dim)
        tx = cx + T.text_w(gc, "温度曲线", font(11)) + 8
        limit = self.chips[0].x - 10
        parts = []
        pts = self.editor.points
        if pts:
            lo = min(p[1] for p in pts)
            hi = max(p[1] for p in pts)
            parts.append("%d–%d%%" % (lo, hi))
            if self.temp >= 0:
                parts.append("现在 %d° → %d%%" % (self.temp, int(round(interp(pts, self.temp)))))
        for text in parts:
            wv = T.text_w(gc, text, font(10.5))
            if tx + wv > limit:
                break
            T.text(gc, text, tx, cy + 6, font(10.5), C.mute if text is parts[0] else C.dim)
            tx += wv + 10

        for c in self.children:
            c.paint(gc)

    # ---- 命中 ----
    def hit(self, px, py):
        if not self.inside(px, py):
            return None
        for c in reversed(self.children):
            got = c.hit(px, py)
            if got:
                return got
        return self

    def on_mouse(self, kind, px, py, evt=None):
        return False

    def tip(self, px, py):
        return None


class FansPage(Page):
    title = "风扇"
    subtitle = "选个场景，或者自己调"

    def build(self):
        self.snap = {}
        self.scenes = [self.add(SceneTile(self, k, n, d, self._pick_scene)) for k, n, d in SCENES]
        self.profiles: list[Node] = []
        self.cards = [self.add(FanCard(self, 1, "CPU 风扇")), self.add(FanCard(self, 2, "GPU 风扇"))]
        self.btn_save = self.add(Button("存为方案", self._save_profile, kind="normal", size=11.5))
        self.btn_auto = self.add(Button("交还电脑", self._back_auto, kind="ghost", size=11.5))
        self.empty_hint = Label("还没有方案 —— 调好之后点「存为方案」，下次一键套用。",
                                size=11, color=C.mute)
        self.add(self.empty_hint)
        self._curve_sig = {}

    # ---- 布局 ----
    def layout(self):
        W, H = self.W, self.H
        y = 46
        cw = (W - M * 2 - 12 * 3) / 4.0
        for i, s in enumerate(self.scenes):
            s.place(M + i * (cw + 12), y, cw, 64)
        y += 64 + 12

        self.btn_save.place(M, y, 96, 30)
        self.btn_auto.place(M + 104, y, 88, 30)
        self.btn_save.visible = self.btn_auto.visible = True
        self.empty_hint.place(M + 208, y + 6, W - M * 2 - 208, 18)

        self._layout_profiles(M + 208 + 190, y, W - M - (M + 208 + 190))
        y += 30 + 12

        cwid = (W - M * 2 - 12) / 2.0
        ch = max(360.0, H - y - 14)
        self.cards[0].place(M, y, cwid, ch)
        self.cards[1].place(M + cwid + 12, y, cwid, ch)
        for c in self.cards:
            c.layout()

    def _layout_profiles(self, x, y, w):
        n = len(self.profiles)
        self.empty_hint.visible = n == 0
        if not n:
            return
        tile_w = 148
        gap = 8
        fit = max(1, int((w + gap) // (tile_w + gap)))
        for i, tile in enumerate(self.profiles[:fit]):
            tile.place(x + i * (tile_w + gap), y, tile_w, 30)
            tile.visible = True
        for tile in self.profiles[fit:]:
            tile.visible = False

    # ---- 数据 ----
    def tick(self, snap):
        self.snap = snap
        fans = snap.get("fans") or {}
        curve = snap.get("curve") or {}
        for card in self.cards:
            fan = fans.get(str(card.no)) or {}
            card.temp = fan.get("temp", -1)
            card.rpm = fan.get("rpm", -1)
            card.duty = fan.get("duty", -1)
            mode = fan.get("mode", "auto")
            idx = {"auto": 0, "manual": 1, "curve": 2}.get(mode, 0)
            if idx != card.seg.index:
                card.seg.index = idx
            if mode == "manual":
                card.slider.set(fan.get("manual_duty", 50))
            pts = curve.get(card.curve_key) or []
            sig = tuple((round(p[0], 1), round(p[1], 1)) for p in pts)
            if sig and sig != self._curve_sig.get(card.curve_key):
                self._curve_sig[card.curve_key] = sig
                if card.editor._grab < 0:
                    card.editor.set_points(pts)

        # 场景高亮：曲线是否等于某个预设
        active = None
        for key in ("quiet", "balanced", "performance", "max"):
            if self._curve_matches(curve, snap.get("presets") or {}, key):
                active = key
                break
        for s in self.scenes:
            s.active = s.key == active

        self._sync_profiles(snap.get("profiles") or [])
        self.request_repaint()

    def _curve_matches(self, curve, presets, key):
        preset = presets.get(key) or {}
        for side in ("cpu", "gpu"):
            a = curve.get(side) or []
            b = preset.get(side) or []
            if len(a) != len(b):
                return False
            for p, q in zip(a, b):
                if abs(p[0] - q[0]) > 0.01 or abs(p[1] - q[1]) > 0.01:
                    return False
        return bool(curve.get("cpu"))

    def _sync_profiles(self, items):
        sig = [(p.get("id"), p.get("name")) for p in items]
        old = [(p.prof.get("id"), p.prof.get("name")) for p in self.profiles]
        if sig == old:
            return
        for tile in self.profiles:
            self.nodes.remove(tile)
        self.profiles = [ProfileTile(self, p, self._apply_profile, self._delete_profile)
                         for p in items]
        for tile in self.profiles:
            tile.page = self
            self.nodes.append(tile)

    # ---- 动作 ----
    def _pick_scene(self, key):
        if A.apply_preset(self.app.ctx, key):
            name = {"quiet": "安静", "balanced": "均衡", "performance": "性能", "max": "全速"}[key]
            self.app.toast("已切到「%s」，两个风扇按曲线走" % name)
            self._curve_sig = {}

    def _back_auto(self):
        res = A.back_to_auto(self.app.ctx)
        self.app.toast("已交还给电脑，BIOS 温控接管" if res.get("ok") else "交还未完成，请查看安全日志")

    def _save_profile(self):
        self.app.ask_name("把当前设置存成方案", "", self._do_save)

    def _do_save(self, name):
        if not name:
            return
        res = A.save_profile(self.app.ctx, name)
        self.app.toast(("已保存方案「%s」" % name) if res.get("ok", True) else
                       (res.get("reason") or "保存失败"))
        self._sync_profiles(A.profile_list(self.app.ctx))
        self.layout()

    def _apply_profile(self, pid):
        res = A.apply_profile(self.app.ctx, pid)
        self.app.toast("已套用方案" if res.get("ok") else (res.get("reason") or "套用失败"))
        self._curve_sig = {}

    def _delete_profile(self, pid):
        A.delete_profile(self.app.ctx, pid)
        self._sync_profiles(A.profile_list(self.app.ctx))
        self.layout()
        self.app.toast("方案已删除")

    def on_show(self):
        self._sync_profiles(A.profile_list(self.app.ctx))
