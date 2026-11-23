# -*- coding: utf-8 -*-
"""设置页：窗口大小、启动与常驻、关于与维护。"""
from __future__ import annotations

import os
import subprocess

from . import theme as T
from .kit import Button, Chip, Node, Select, Switch
from .shell import DESIGN_H, DESIGN_W, Page
from .theme import C, font

M = 18

SIZES = [
    ("自适应（推荐）", "auto"),
    ("1280 × 800", (1280, 800)),
    ("1366 × 768", (1366, 768)),
    ("1440 × 900", (1440, 900)),
    ("1600 × 900", (1600, 900)),
    ("1920 × 1080", (1920, 1080)),
]


class SettingsPage(Page):
    title = "设置"
    subtitle = "开机自启、窗口大小、维护"

    def build(self):
        self.snap = {}
        self.size_sel = Select(SIZES, index=0, on_change=self._on_size, w=170)
        self.add(self.size_sel)
        self.sw_auto = self.add(Switch(True, self._on_autostart))
        self.sw_open = self.add(Switch(False, lambda v: self._patch({"open_window_on_start": v})))
        self.sw_resident = self.add(Switch(True, lambda v: self._patch({"stay_resident": v})))
        self.sw_restore = self.add(Switch(True, lambda v: None))
        self.sw_restore.enabled = False
        self.btn_fit = self.add(Button("最大化", self._maximize, kind="ghost", size=11))
        self.btn_data = self.add(Button("打开数据目录", lambda: self._open("data"), size=11))
        self.btn_log = self.add(Button("查看日志", lambda: self._open("log"), size=11))
        self.btn_quit = self.add(Button("还原并退出", self._quit, kind="danger", size=11))
        self.btn_tray = self.add(Button("缩到托盘试一下", self._to_tray, kind="ghost", size=11))
        self.auto_state = ""

    def layout(self):
        W, H = self.W, self.H
        y = 46
        lw = (W - M * 2) * 0.52
        rw = W - M * 2 - lw - 12
        h1, h2, h3 = 200, 262, 178
        self._fit = (M, y, lw, h1)
        self._boot = (M, y + h1 + 12, lw, h2)
        self._tray = (M, y + h1 + h2 + 24, lw, h3)
        self._about = (M + lw + 12, y, rw, 392)
        self._tips = (M + lw + 12, y + 404, rw, max(200.0, H - y - 14 - 404))

        fx, fy, fw, fh = self._fit
        self.size_sel.place(fx + 16, fy + 74, 170, 32)
        self.btn_fit.place(fx + 196, fy + 74, 74, 32)

        bx, by, bw, bh = self._boot
        self.sw_auto.place(bx + bw - 60, by + 52, 44, 26)
        self.sw_open.place(bx + bw - 60, by + 96, 44, 26)
        self.sw_resident.place(bx + bw - 60, by + 140, 44, 26)
        self.sw_restore.place(bx + bw - 60, by + 184, 44, 26)

        tx, ty, tw_, th = self._tray
        self.btn_tray.place(tx + 16, ty + th - 48, 128, 32)

        ax, ay, aw, ah = self._about
        self.btn_data.place(ax + 16, ay + ah - 48, 104, 32)
        self.btn_log.place(ax + 128, ay + ah - 48, 84, 32)
        self.btn_quit.place(ax + aw - 16 - 96, ay + ah - 48, 96, 32)

    # ---- 数据 ----
    def tick(self, snap):
        self.snap = snap
        st = snap.get("settings") or {}
        auto = getattr(self.app.ctx, "auto_state", None) or {}
        self.sw_auto.value = bool(auto.get("enabled", st.get("autostart", True)))
        self.sw_open.value = bool(st.get("open_window_on_start"))
        self.sw_resident.value = bool(st.get("stay_resident"))
        self.sw_restore.value = True
        self.request_repaint()

    def on_show(self):
        self.auto_state = "查询中…"
        self.app.ctx.query_autostart()

    # ---- 动作 ----
    def _patch(self, patch):
        try:
            res = self.app.ctx.apply_settings(patch)
        except (ValueError, OSError) as exc:
            res = {"ok": False, "reason": str(exc)}
        if not res.get("ok"):
            self.app.toast(res.get("reason") or "设置未保存")
        else:
            notes = res.get("notes") or []
            if notes:
                self.app.toast(notes[0])
        return res

    def _on_autostart(self, on):
        res = self._patch({"autostart": on})
        self.auto_state = "设置中…" if res.get("ok") else "设置失败"
        self.request_repaint()

    def _on_size(self, value):
        if value == "auto":
            self.app.reset_window_size()
            self.app.toast("窗口已按屏幕自适应")
            return
        w, h = value
        self.app.set_window_size(w, max(600, h - 40))
        self.app.toast("窗口已设为 %d × %d" % (w, h))

    def _maximize(self):
        self.app.Maximize(True)

    def _to_tray(self):
        # 收进托盘后窗口是不可见的，提示只能靠托盘图标本身
        self.app._to_tray()

    def _open(self, which):
        ctx = self.app.ctx
        root = os.path.dirname(ctx.store.path)
        path = ctx.store.path if which == "log" else root
        if which == "log":
            path = os.path.join(os.path.dirname(ctx.store.path), "app.log")
        try:
            if os.path.exists(path):
                if path.lower().endswith(".log"):
                    subprocess.Popen(["notepad.exe", path], close_fds=True)
                else:
                    subprocess.Popen(["explorer.exe", path], close_fds=True)
        except Exception as exc:  # noqa: BLE001
            self.app.toast("打不开：%s" % exc)

    def _quit(self):
        self.app.quit()

    # ---- 绘制 ----
    def paint(self, gc):
        snap = self.snap or {}
        st = snap.get("settings") or {}

        # ---------- 屏幕适配 ----------
        fx, fy, fw, fh = self._fit
        T.card(gc, fx, fy, fw, fh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "屏幕适配", fx + 16, fy + 14, font(12.5, "semibold"), C.text)
        T.text(gc, "窗口大小", fx + 16, fy + 52, font(11), C.dim)
        self.size_sel.paint(gc)
        self.btn_fit.paint(gc)
        area = st.get("screen") or {}
        win = self.app.GetSize()
        T.text(gc, "屏幕可用 %s×%s · 当前窗口 %d×%d" % (
            area.get("w", "--"), area.get("h", "--"), win.width, win.height),
               fx + 16, fy + 120, font(10.5), C.mute)
        self._wrap(gc,
                   "界面按 1320×792 排版，窗口大小不同会整体等比缩放，"
                   "所以任何分辨率都能一屏看完，不会出现底部被裁掉。",
                   fx + 16, fy + 146, fw - 32, C.mute, 10.5)

        # ---------- 启动与常驻 ----------
        bx, by, bw, bh = self._boot
        T.card(gc, bx, by, bw, bh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "启动与常驻", bx + 16, by + 14, font(12.5, "semibold"), C.text)
        auto = getattr(self.app.ctx, "auto_state", None) or {}
        if auto.get("pending"):
            label, acol = "设置中…", C.warn
        elif auto.get("enabled"):
            label, acol = "已开启", C.ok
        elif auto.get("supported") is False:
            label, acol = "安装版可用", C.mute
        elif auto:
            label, acol = "未开启", C.mute
        else:
            label, acol = "查询中…", C.mute
        tw = T.text_w(gc, label, font(10))
        T.pill(gc, bx + bw - tw - 78, by + 11, tw + 16, 20, label, acol, C.surface3)

        rows = [("开机自启", "登录 Windows 后自动在后台启动，不弹 UAC", self.sw_auto, by + 52),
                ("启动时打开窗口", "开机自启时直接把界面弹出来", self.sw_open, by + 96),
                ("关窗口后继续后台运行", "关掉界面不退出，风扇照常按设置跑", self.sw_resident, by + 140),
                ("退出时自动还原", "彻底退出前把风扇交还固件，保持原厂状态", self.sw_restore, by + 184)]
        for title, desc, sw, yy in rows:
            T.text(gc, title, bx + 16, yy + 1, font(11.5), C.text)
            T.text(gc, desc, bx + 16, yy + 19, font(10), C.mute, max_w=bw - 100)
            sw.paint(gc)

        # ---------- 托盘 ----------
        tx, ty, tw_, th = self._tray
        T.card(gc, tx, ty, tw_, th, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "托盘", tx + 16, ty + 14, font(12.5, "semibold"), C.text)
        tray_rows = [("双击图标", "把窗口叫出来或者收回去"),
                     ("右键菜单", "打开控制台、交还风扇自动、退出")]
        yy = ty + 44
        for k, v in tray_rows:
            T.text(gc, k, tx + 16, yy, font(10.5), C.mute)
            T.text(gc, v, tx + 96, yy, font(10.5), C.dim, max_w=tw_ - 116)
            yy += 24
        self.btn_tray.paint(gc)

        # ---------- 关于 ----------
        ax, ay, aw, ah = self._about
        T.card(gc, ax, ay, aw, ah, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "关于与维护", ax + 16, ay + 14, font(12.5, "semibold"), C.text)
        backend = snap.get("backend") or {}
        guard = snap.get("guard") or {}
        kv = [
            ("版本", "v" + str((st.get("version") or "1.4.0"))),
            ("后端", backend.get("label") or "未知"),
            ("运行方式", "原生界面（无浏览器内核）"),
            ("程序", os.path.basename(getattr(self.app.ctx, "exe_path", "") or "ECPilot.exe")),
            ("内存占用", self._mem_text()),
            ("写入日志", "%d 条" % len(guard.get("entries") or [])),
            ("数据目录", getattr(self.app.ctx, "root", "")),
        ]
        yy = ay + 44
        for k, v in kv:
            T.text(gc, k, ax + 16, yy, font(10.5), C.mute)
            T.text(gc, str(v), ax + 76, yy, font(10.5), C.dim, max_w=aw - 100)
            yy += 26
        T.card(gc, ax + 16, yy + 8, aw - 32, 1, 0, fill=C.line_soft)
        yy += 24
        for line in ("这一版把界面换成了系统原生渲染：不再启动 Edge/Chrome 进程，",
                     "整程序只有一个进程，空闲时几乎不占内存和 CPU。",
                     "托盘常驻时刷新降到 3 秒一次，更省电。"):
            T.text(gc, line, ax + 16, yy, font(10.5), C.mute, max_w=aw - 32)
            yy += 19
        self.btn_data.paint(gc)
        self.btn_log.paint(gc)
        self.btn_quit.paint(gc)

        # ---------- 小贴士 ----------
        px, py_, pw, ph = self._tips
        T.card(gc, px, py_, pw, ph, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "遇到这些情况看这里", px + 16, py_ + 14, font(12.5, "semibold"), C.text)
        tips = [
            ("关了窗口还在跑？", "右下角托盘图标双击就能叫回来，不是卡住了。"),
            ("想彻底退出", "托盘右键「退出」，或者这页点「还原并退出」。"),
            ("屏幕太小或太大", "屏幕适配里挑一个尺寸，或者直接点「最大化」。"),
            ("风扇不听话", "先去诊断页看传感器读数，读不到多半是驱动没装好。"),
            ("想交还固件", "托盘右键「交还风扇自动」，风扇页切「自动」也一样。"),
        ]
        yy = py_ + 46
        for k, v in tips:
            T.text(gc, k, px + 16, yy, font(10.5, "semibold"), C.dim)
            T.text(gc, v, px + 16, yy + 18, font(10), C.mute, max_w=pw - 32)
            yy += 44

    @staticmethod
    def _mem_text():
        try:
            import ctypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t),
                            ("e", ctypes.c_size_t), ("f", ctypes.c_size_t)]

            p = PMC()
            p.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
            return "%.0f MB（单进程）" % (p.WorkingSetSize / 1048576.0)
        except Exception:
            return "—"

    def _wrap(self, gc, text, x, y, w, color, size):
        f = font(size)
        line = ""
        yy = y
        for ch in text:
            if T.text_w(gc, line + ch, f) > w:
                T.text(gc, line, x, yy, f, color)
                line = ch
                yy += size * 1.6
            else:
                line += ch
        if line:
            T.text(gc, line, x, yy, f, color)
