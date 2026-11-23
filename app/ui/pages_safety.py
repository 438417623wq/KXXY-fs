# -*- coding: utf-8 -*-
"""安全页：一键还原、保护策略、写入日志。"""
from __future__ import annotations

import time

from . import theme as T
from . import actions as A
from .kit import Button, Switch, Slider
from .shell import Page
from .theme import C, font

M = 18


class SafetyPage(Page):
    title = "安全"
    subtitle = "先记账、后动手，随时能还原"

    def build(self):
        self.snap = {}
        self.btn_restore = self.add(Button("立即还原全部", self._restore, kind="primary", size=12))
        self.btn_clear = self.add(Button("清空日志", self._clear, kind="ghost", size=11.5))
        self.s_min = self.add(Slider(value=20, min_v=20, max_v=100, on_change=self._on_min))
        self.s_fuse = self.add(Slider(value=92, min_v=55, max_v=100, on_change=self._on_fuse))
        self.sw_restore = self.add(Switch(True, lambda v: None))
        self.sw_restore.enabled = False  # 后台管控退出必须交还，旧配置开关不再允许关闭
        self.sw_low = self.add(Switch(False, lambda v: self._set({"allow_low_duty": v})))

    def layout(self):
        W, H = self.W, self.H
        y = 46
        top_h = 210
        self._restore_rect = (M, y, (W - M * 2 - 12) / 2.0, top_h)
        self._policy_rect = (M + (W - M * 2 - 12) / 2.0 + 12, y, (W - M * 2 - 12) / 2.0, top_h)
        self._log_rect = (M, y + top_h + 12, W - M * 2, max(160.0, H - y - top_h - 12 - 14))

        rx, ry, rw, rh = self._restore_rect
        self.btn_restore.place(rx + 16, ry + rh - 44, 132, 32)
        self.btn_clear.place(rx + 158, ry + rh - 44, 96, 32)

        px, py, pw, ph = self._policy_rect
        self.s_min.place(px + 16, py + 68, pw - 32, 24)
        self.s_fuse.place(px + 16, py + 132, pw - 32, 24)
        self.sw_low.place(px + 16, py + ph - 44, 44, 26)
        self.sw_restore.place(px + pw - 60, py + ph - 44, 44, 26)

    # ---- 数据 ----
    def tick(self, snap):
        self.snap = snap
        safety = snap.get("safety") or {}
        if not self.s_min._drag:
            self.s_min.set(safety.get("min_duty", 20))
        if not self.s_fuse._drag:
            self.s_fuse.set(safety.get("fuse_temp", 92))
        self.sw_restore.value = True
        self.sw_low.value = bool(safety.get("allow_low_duty", False))
        self.request_repaint()

    # ---- 动作 ----
    def _restore(self):
        res = A.restore_all(self.app.ctx)
        done = res.get("done") or []
        if res.get("ok"):
            self.app.toast("已还原 %d 项，风扇交给电脑管" % len(done))
        else:
            self.app.toast("还原未完成：" + "；".join(res.get("errors", []) or [res.get("reason", "请查看日志")]))

    def _clear(self):
        A.clear_journal(self.app.ctx)
        self.app.toast("历史已清理，未还原记录仍保留")

    def _on_min(self, v):
        self._set({"min_duty": int(v)})

    def _on_fuse(self, v):
        self._set({"fuse_temp": int(v)})

    def _set(self, patch):
        try:
            safety = A.set_safety(self.app.ctx, patch)
        except (ValueError, OSError) as exc:
            self.app.toast("安全设置未保存：%s" % exc)
            return False
        self.snap["safety"] = safety
        self.request_repaint()
        return True

    # ---- 绘制 ----
    def paint(self, gc):
        snap = self.snap or {}
        safety = snap.get("safety") or {}
        guard = snap.get("guard") or {}

        # ---------- 还原 ----------
        rx, ry, rw, rh = self._restore_rect
        T.card(gc, rx, ry, rw, rh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "还原", rx + 16, ry + 14, font(12.5, "semibold"), C.text)
        self._paragraph(gc,
                        "还原 = 所有风扇交还固件自动控制 + 所有写过的 EC 寄存器恢复原值。"
                        "一次性、可验证，做完后 BIOS 的温控曲线重新接管。",
                        rx + 16, ry + 40, rw - 32, C.dim, 11)
        stats = [("本次会话", (guard.get("session_started") or "--")[:19].replace("T", " ")),
                 ("未完成写入", str(guard.get("pending", 0))),
                 ("受控寄存器", str(len(guard.get("ram_touched") or {})))]
        sw_ = (rw - 32) / 3.0
        for i, (k, v) in enumerate(stats):
            bx = rx + 16 + i * sw_
            T.card(gc, bx, ry + 100, sw_ - 10, 50, 9, fill=C.surface2, stroke=C.line_soft)
            T.text(gc, k, bx + 10, ry + 107, font(10), C.mute)
            T.text(gc, v, bx + 10, ry + 124, font(12 if i else 10.5, "semibold", mono=bool(i)),
                   C.text, max_w=sw_ - 26)
        self.btn_restore.paint(gc)
        self.btn_clear.paint(gc)

        # ---------- 保护策略 ----------
        px, py, pw, ph = self._policy_rect
        T.card(gc, px, py, pw, ph, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "保护策略", px + 16, py + 14, font(12.5, "semibold"), C.text)
        T.text(gc, "最低占空比", px + 16, py + 44, font(10.5), C.mute)
        T.text(gc, "%d%%" % int(safety.get("min_duty", 20)), px + pw - 16, py + 42,
               font(11.5, "semibold"), C.accent, align="right")
        self.s_min.paint(gc)
        T.text(gc, "熔断温度", px + 16, py + 108, font(10.5), C.mute)
        T.text(gc, "%d°C" % int(safety.get("fuse_temp", 92)), px + pw - 16, py + 106,
               font(11.5, "semibold"), C.danger, align="right")
        self.s_fuse.paint(gc)
        T.text(gc, "允许停转（0%）", px + 68, py + ph - 39, font(11), C.dim)
        self.sw_low.paint(gc)
        T.text(gc, "退出还原（锁定）", px + pw - 68 - T.text_w(gc, "退出还原（锁定）", font(11)),
               py + ph - 39, font(11), C.dim)
        self.sw_restore.paint(gc)

        # ---------- 日志 ----------
        lx, ly, lw, lh = self._log_rect
        T.card(gc, lx, ly, lw, lh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "写入日志", lx + 16, ly + 14, font(12.5, "semibold"), C.text)
        T.text(gc, "先记账、后动手 —— 每一项都能查到原值",
               lx + 16 + T.text_w(gc, "写入日志", font(12.5, "semibold")) + 10,
               ly + 17, font(10.5), C.mute)

        cols = [("时间", 92), ("动作", 0), ("对象", 118), ("原值", 74), ("新值", 74), ("状态", 62)]
        hx = lx + 16
        hy = ly + 44
        T.card(gc, lx + 16, hy + 18, lw - 32, 1, 0, fill=C.line_soft)
        for name, cw in cols:
            if cw:
                T.text(gc, name, hx, hy, font(10), C.mute)
                hx += cw
            else:
                T.text(gc, name, hx, hy, font(10), C.mute)
                hx = lx + lw - 16 - (118 + 74 + 74 + 62)

        entries = list(reversed((guard.get("entries") or [])))[:40]
        if not entries:
            T.text(gc, "还没有写入记录 —— 说明风扇一直由电脑自己管着。",
                   lx + 16, ly + 90, font(11), C.mute)
            return
        yy = ly + 74
        row_h = 24
        for e in entries:
            if yy + row_h > ly + lh - 8:
                break
            when = T.ts_short(e.get("ts"))
            cx = lx + 16
            T.text(gc, when, cx, yy, font(10, mono=True), C.mute)
            cx += 92
            T.text(gc, str(e.get("kind") or e.get("action") or ""), cx, yy,
                   font(10.5), C.dim, max_w=lw - 16 - 92 - 118 - 74 - 74 - 62 - 30)
            cx = lx + lw - 16 - (118 + 74 + 74 + 62)
            T.text(gc, str(e.get("target") or ""), cx, yy,
                   font(10, mono=True), C.mute, max_w=114)
            cx += 118
            T.text(gc, str(e.get("old") if e.get("old") is not None else "—"), cx, yy,
                   font(10, mono=True), C.mute)
            cx += 74
            T.text(gc, str(e.get("new") if e.get("new") is not None else "—"), cx, yy,
                   font(10, mono=True), C.text)
            cx += 74
            status = e.get("status") or "ok"
            ok = status != "failed"
            T.text(gc, {"ok": "完成", "pending": "进行中", "failed": "失败"}.get(status, status),
                   cx, yy, font(10), C.ok if ok else C.danger)
            yy += row_h

    def _paragraph(self, gc, text, x, y, w, color, size):
        f = font(size)
        line = ""
        yy = y
        for ch in text:
            if T.text_w(gc, line + ch, f) > w:
                T.text(gc, line, x, yy, f, color)
                line = ch
                yy += size * 1.65
            else:
                line += ch
        if line:
            T.text(gc, line, x, yy, f, color)
