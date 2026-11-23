# -*- coding: utf-8 -*-
"""诊断页：运行环境、传感器读数、EC RAM 只读快照。"""
from __future__ import annotations

from . import theme as T
from .kit import Button
from .shell import Page
from .theme import C, font

M = 18


class DiagPage(Page):
    title = "诊断"
    subtitle = "链路通不通、寄存器里是什么，只读不改"

    def build(self):
        self.snap = {}
        self.dump = {}
        self.btn_refresh = self.add(Button("刷新", self._refresh, kind="normal", size=11))

    def layout(self):
        W, H = self.W, self.H
        y = 46
        lw = (W - M * 2) * 0.42
        rw = W - M * 2 - lw - 12
        ch = max(320.0, H - y - 14)
        self._left = (M, y, lw, ch)
        self._right = (M + lw + 12, y, rw, ch)
        self.btn_refresh.place(M + lw + 12 + rw - 16 - 74, y + 12, 74, 26)

    def tick(self, snap):
        self.snap = snap
        if not self.dump:
            self._refresh(quiet=True)
        self.request_repaint()

    def _refresh(self, quiet=False):
        try:
            self.dump = self.app.ctx.light.dump() or {}
        except Exception as exc:  # noqa: BLE001
            self.dump = {}
            if not quiet:
                self.app.toast("读取 EC 失败：%s" % exc)
        if not quiet:
            self.app.toast("已刷新 EC 只读快照")

    def paint(self, gc):
        snap = self.snap or {}
        backend = snap.get("backend") or {}
        info = backend.get("info") or {}
        lx, ly, lw, lh = self._left
        rx, ry, rw, rh = self._right

        # ---------- 运行环境 ----------
        env_h = min(lh * 0.5, 34 + len(info) * 26 + 16)
        T.card(gc, lx, ly, lw, env_h, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "运行环境", lx + 16, ly + 14, font(12.5, "semibold"), C.text)
        tw = T.text_w(gc, backend.get("label") or "未知", font(10))
        T.pill(gc, lx + lw - tw - 28, ly + 11, tw + 16, 20,
               backend.get("label") or "未知",
               C.accent if not backend.get("is_sim") else C.warn,
               C.surface3)
        yy = ly + 42
        for key, val in list(info.items())[:7]:
            T.text(gc, str(key), lx + 16, yy, font(10.5), C.mute)
            T.text(gc, str(val), lx + 16 + 92, yy, font(10.5), C.dim,
                   max_w=lw - 32 - 92)
            yy += 26

        # ---------- 传感器 ----------
        sy = ly + env_h + 12
        sh = 128.0
        T.card(gc, lx, sy, lw, sh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "传感器", lx + 16, sy + 14, font(12.5, "semibold"), C.text)
        fans = snap.get("fans") or {}
        yy = sy + 44
        for no in (1, 2):
            fan = fans.get(str(no)) or {}
            T.text(gc, fan.get("label") or "风扇%d" % no, lx + 16, yy, font(11), C.dim)
            t = fan.get("temp", -1)
            r = fan.get("rpm", -1)
            d = fan.get("duty", -1)
            ok = isinstance(t, (int, float)) and t >= 0
            T.text(gc, ("%d°C · %d RPM · %d%%" % (t, r, d)) if ok else "读不到数据",
                   lx + lw - 16, yy, font(10.5, mono=True),
                   C.text if ok else C.warn, align="right")
            yy += 26
        note = (snap.get("state") or {}).get("sensor_note") or ""
        if note:
            self._wrap(gc, note, lx + 16, yy + 8, lw - 32, C.warn, 10)
        else:
            self._wrap(gc, "两条链路都在正常返回数据：厂商 DLL 与裸端口协议都通。",
                       lx + 16, yy + 8, lw - 32, C.mute, 10)

        # ---------- 键盘灯通道 ----------
        ly2 = sy + sh + 12
        lh2 = max(90.0, ly + lh - ly2)
        T.card(gc, lx, ly2, lw, lh2, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "键盘灯通道", lx + 16, ly2 + 14, font(12.5, "semibold"), C.text)
        light = snap.get("light") or {}
        eff = None
        for (k, label) in ((e.get("key"), e.get("label"))
                           for e in (light.get("effects") or [])):
            if k == light.get("effect"):
                eff = label
                break
        rows = [
            ("通道", "就绪" if light.get("available") else "不可用",
             C.ok if light.get("available") else C.warn),
            ("灯效", eff or "静态", C.dim),
            ("颜色", str(light.get("hex") or "——").upper(), C.dim),
            ("亮度", "%d%%" % int(light.get("brightness") or 0), C.dim),
            ("分区", "整块键盘" if not light.get("zone") else "分区 %d" % int(light.get("zone")),
             C.dim),
            ("已生效", "是" if light.get("applied") else "还没写过", C.dim),
            ("也在控灯", ("%d 个程序" % len(light.get("owners") or []))
             if light.get("owners") else "没有", C.dim),
        ]
        yy = ly2 + 44
        for key, val, color in rows:
            T.text(gc, key, lx + 16, yy, font(10.5), C.mute)
            T.text(gc, str(val), lx + lw - 16, yy, font(10.5, mono=True), color,
                   align="right", max_w=lw - 32)
            yy += 24
        reason = light.get("reason") or light.get("last_error") or ""
        if reason:
            self._wrap(gc, str(reason), lx + 16, yy + 6, lw - 32, C.mute, 10)
        T.text(gc, "这一段只读，改不了任何东西", lx + 16, ly2 + lh2 - 24, font(10), C.mute)

        # ---------- EC RAM ----------
        T.card(gc, rx, ry, rw, rh, 14, fill=C.surface, stroke=C.line)
        T.text(gc, "EC RAM 只读快照", rx + 16, ry + 14, font(12.5, "semibold"), C.text)
        T.text(gc, "0x00 – 0xFF，非 0 的高亮", rx + 16 + 110, ry + 17, font(10.5), C.mute)
        self.btn_refresh.paint(gc)

        dump = self.dump
        if isinstance(dump, dict):
            data = dump.get("data") or dump.get("bytes") or dump
        else:
            data = dump
        cells = []
        if isinstance(data, dict):
            for k, v in data.items():
                try:
                    cells.append((int(str(k), 16) if isinstance(k, str) else int(k), int(v)))
                except Exception:
                    continue
        elif isinstance(data, (list, tuple)):
            cells = list(enumerate([int(x) for x in data]))
        cells.sort()
        if not cells:
            T.text(gc, "暂时读不到 EC —— 通道可能被别的驱动占着，重启一次通常就好。",
                   rx + 16, ry + 60, font(11), C.mute)
            return

        top = ry + 46
        grid_h = rh - 66
        cols, rows = 16, 16
        pad = 16
        gw = rw - pad * 2
        cw = (gw - (cols - 1) * 3) / cols
        chh = min(34.0, (grid_h - (rows - 1) * 3) / rows)
        base = cells[0][0] & 0xF0
        mapping = dict(cells)
        for i in range(256):
            addr = base + i
            col, row = i % 16, i // 16
            x = rx + pad + col * (cw + 3)
            y = top + row * (chh + 3)
            v = mapping.get(addr)
            if v is None:
                T.card(gc, x, y, cw, chh, 3, fill="#131b24")
                continue
            if v:
                heat = min(1.0, v / 255.0)
                fill = T.mix("#1b2b38", C.accent, 0.25 + heat * 0.55)
                fg = "#04121a" if heat > 0.55 else C.text
            else:
                fill, fg = "#151f2a", "#3d4c5c"
            T.card(gc, x, y, cw, chh, 3, fill=fill)
            if chh >= 16:
                T.text(gc, "%02X" % v, x + cw / 2.0, y + (chh - 11) / 2.0,
                       font(8.5, mono=True), fg, align="center")

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
