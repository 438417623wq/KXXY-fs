# -*- coding: utf-8 -*-
"""原生窗口：一进程、一画布、无浏览器。

和网页版的行为对齐：
  * 关窗口 = 收起（常驻后台，托盘还在）；设置里关掉常驻则退出并还原
  * 每秒取一次快照刷新界面；窗口隐藏时降到 3 秒，进一步省电
"""
from __future__ import annotations

import os
import time
import threading

import wx
import wx.adv

from . import theme as T
from . import actions as A
from .pages_diag import DiagPage
from .pages_fans import FansPage
from .pages_light import LightPage
from .pages_overview import OverviewPage
from .pages_perf import PerfPage
from .pages_safety import SafetyPage
from .pages_settings import SettingsPage
from .shell import DESIGN_H, DESIGN_W, Canvas, Shell
from .theme import C, font


def _make_icon(root: str) -> wx.Icon:
    path = os.path.join(root, "assets", "ecpilot.ico")
    if os.path.exists(path):
        try:
            return wx.Icon(path, wx.BITMAP_TYPE_ICO)
        except Exception:
            pass
    bmp = wx.Bitmap(32, 32)
    dc = wx.MemoryDC(bmp)
    dc.SetBackground(wx.Brush(T.col(C.bg)))
    dc.Clear()
    dc.SetBrush(wx.Brush(T.col(C.accent)))
    dc.SetPen(wx.Pen(T.col(C.accent)))
    dc.DrawCircle(16, 16, 10)
    dc.SelectObject(wx.NullBitmap)
    return wx.Icon(bmp)


class Tray(wx.adv.TaskBarIcon):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.SetIcon(_make_icon(app.ctx.root), "EC Pilot · 风扇与灯效")
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, lambda e: app.show_window())

    def CreatePopupMenu(self):
        m = wx.Menu()
        open_item = m.Append(wx.ID_ANY, "打开控制台")
        self.Bind(wx.EVT_MENU, lambda e: self.app.show_window(), open_item)
        m.AppendSeparator()
        for key, name in (("quiet", "安静"), ("balanced", "均衡"),
                          ("performance", "性能"), ("max", "全速")):
            item = m.Append(wx.ID_ANY, "切到「%s」" % name)
            self.Bind(wx.EVT_MENU, lambda e, k=key: self.app.quick_scene(k), item)
        item = m.Append(wx.ID_ANY, "交还给电脑")
        self.Bind(wx.EVT_MENU, lambda e: self.app.quick_auto(), item)
        # 性能模式：四档直接切换，不用打开窗口
        sub = wx.Menu()
        for mode in A.perf_modes():
            it = sub.Append(wx.ID_ANY, "%s（%s）" % (mode["name"], mode["en"]))
            self.Bind(wx.EVT_MENU, lambda e, mid=mode["id"]: self.app.quick_perf(mid), it)
        m.AppendSubMenu(sub, "性能模式")
        m.AppendSeparator()
        item = m.Append(wx.ID_ANY, "还原风扇并退出")
        self.Bind(wx.EVT_MENU, lambda e: self.app.quit(), item)
        return m


class NativeApp(wx.Frame):
    def __init__(self, ctx, runner, start_hidden: bool = False, start_view: str = ""):
        area = wx.Display(0).GetClientArea()
        w = min(DESIGN_W + 40, max(880, area.width - 60))
        h = min(DESIGN_H + 70, max(560, area.height - 60))
        super().__init__(None, title="EC Pilot · 风扇与灯效控制台", size=(w, h))
        self.ctx = ctx
        self.runner = runner
        self.last_snap = {}
        self._toast = None
        self._toast_until = 0.0
        self._allow_close = False
        self._quitting = False
        self._snapshot_busy = False
        self._hidden = False
        self._tick_iv = 1000

        self.SetBackgroundColour(T.col(C.bg))
        try:
            self.SetIcon(_make_icon(ctx.root))
        except Exception:
            pass

        self.canvas = Canvas(self, self)
        self.shell = Shell(ctx, self)
        self.shell.register("overview", OverviewPage(self))
        self.shell.register("perf", PerfPage(self))
        self.shell.register("fans", FansPage(self))
        self.shell.register("light", LightPage(self))
        self.shell.register("safety", SafetyPage(self))
        self.shell.register("diag", DiagPage(self))
        self.shell.register("settings", SettingsPage(self))
        if start_view in self.shell.pages:
            self.shell.view = start_view

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.canvas, 1, wx.EXPAND)
        self.SetSizer(sizer)
        self.Centre()

        self.tray = Tray(self)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_ICONIZE, self._on_iconize)

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.tick(), self.timer)
        self.timer.Start(self._tick_iv)

        # 让 HTTP 那边的 /api/show 也能把窗口叫出来
        ctx.on_show = lambda: wx.CallAfter(self.show_window)
        ctx.on_exit = lambda: wx.CallAfter(self.quit)
        ctx.on_quick_scene = lambda k: wx.CallAfter(self.quick_scene, k)
        if runner.instance is not None:
            runner.instance.watch(ctx.on_show, ctx.on_exit)
        for event_name, callback in (("EVT_POWER_SUSPENDING", self._on_suspend),
                                     ("EVT_POWER_RESUME", self._on_resume)):
            event = getattr(wx, event_name, None)
            if event is not None:
                self.Bind(event, callback)
        if getattr(wx, "EVT_END_SESSION", None) is not None:
            wx.GetApp().Bind(wx.EVT_END_SESSION, self._on_end_session)

        wx.CallAfter(self.tick)
        if start_hidden:
            self.Hide()
            self._hidden = True
            ctx.window_visible = False
            self.set_interval(5000)

    # ------------------------------------------------------------ 生命周期
    def _on_suspend(self, evt):
        self.runner.suspend()
        evt.Skip()

    def _on_resume(self, evt):
        self.runner.resume()
        evt.Skip()

    def _on_end_session(self, evt):
        # 系统注销不能依赖 UI 主循环还会运行；执行有上限的收尾。
        self.runner.shutdown()
        evt.Skip()

    def show_window(self):
        if self._quitting:
            return
        self.ctx.window_visible = True
        self.Show()
        self.Iconize(False)
        self.Raise()
        self._hidden = False
        self.set_interval(1000)
        self.request_repaint()

    def _on_iconize(self, evt):
        # 最小化就收进托盘，别在任务栏占地方
        if evt.IsIconized():
            wx.CallLater(60, self._to_tray)
        evt.Skip()

    def _to_tray(self):
        if not self.IsShown():
            return
        self.Hide()
        self._hidden = True
        self.ctx.window_visible = False
        self.set_interval(5000)

    def _on_close(self, evt):
        if self._quitting:
            if evt.CanVeto():
                evt.Veto()
            return
        if self._allow_close:
            self.Destroy()
            return
        stay = bool(self.ctx.store.data.get("ui", {}).get("stay_resident", True))
        if stay:
            self._to_tray()
            self.toast("已收起到托盘，风扇还在按你的设置跑")
        else:
            self.quit()

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        self.ctx.window_visible = False
        self._allow_close = True
        try:
            self.timer.Stop()
        except Exception:
            pass
        if self.tray:
            self.tray.RemoveIcon()
            self.tray.Destroy()
            self.tray = None
        def finish():
            try:
                result = self.runner.shutdown()
            except Exception as exc:
                result = {"ok": False, "errors": [str(exc)]}
            wx.CallAfter(self._finish_quit, result)
        threading.Thread(target=finish, name="native-shutdown", daemon=True).start()

    def _finish_quit(self, result):
        if not result or not result.get("ok"):
            wx.MessageBox("未确认风扇已交还固件。恢复记录已保留，请检查散热并建议重启电脑。\n" +
                          "\n".join((result or {}).get("errors", [])), "退出还原未完成", wx.OK | wx.ICON_WARNING)
        self.Destroy()

    def set_window_size(self, width, height):
        self.SetSize((int(width), int(height)))
        self.ctx.apply_settings({"win_w": int(width), "win_h": int(height)})

    def reset_window_size(self):
        area = wx.Display(0).GetClientArea()
        self.set_window_size(min(DESIGN_W + 40, area.width - 60), min(DESIGN_H + 70, area.height - 60))
        self.ctx.apply_settings({"win_w": 0, "win_h": 0})
        self.Centre()

    def set_interval(self, ms):
        if ms == self._tick_iv:
            return
        self._tick_iv = ms
        self.timer.Start(ms)

    # ------------------------------------------------------------ 数据刷新
    def tick(self):
        if self._quitting or self._hidden or self._snapshot_busy:
            return
        self._snapshot_busy = True
        def fetch():
            snap = None
            try:
                snap = self.ctx.snapshot()
                snap["version"] = getattr(self.ctx, "version", None) or _version()
            except Exception:
                import traceback
                traceback.print_exc()
            wx.CallAfter(self._accept_snapshot, snap)
        # 最多一个快照任务；EC 卡住不会冻结窗口，也不会累积查询线程。
        threading.Thread(target=fetch, name="ui-snapshot", daemon=True).start()

    def _accept_snapshot(self, snap):
        self._snapshot_busy = False
        if self._quitting:
            return
        if snap is not None:
            self.last_snap = snap
            page = self.shell.current()
            if page:
                page.tick(snap)
        if self._toast and time.time() > self._toast_until:
            self._toast = None
            self.request_repaint()

    def request_repaint(self):
        if self.canvas:
            self.canvas.Refresh(False)

    def relayout(self):
        lw, lh = self.canvas.logical_size()
        self.shell.relayout(lw, lh)

    # ------------------------------------------------------------ 绘制入口
    def paint(self, gc):
        try:
            self.shell.paint(gc)
        except Exception:
            import traceback
            traceback.print_exc()
        if self._toast:
            self._paint_toast(gc)

    def _paint_toast(self, gc):
        text = self._toast
        f = font(11.5)
        tw = T.text_w(gc, text, f)
        w, h = tw + 28, 32
        x = (self.shell.W - w) / 2.0
        y = self.shell.H - h - 22
        T.card(gc, x + 2, y + 4, w, h, 16, fill="#000000", alpha=90)
        T.card(gc, x, y, w, h, 16, fill="#1d2a38", stroke=C.line_strong)
        T.text(gc, text, x + w / 2.0, y + (h - 15) / 2.0, f, C.text, align="center")

    def toast(self, text, seconds=2.4):
        self._toast = text
        self._toast_until = time.time() + seconds
        self.request_repaint()
        wx.CallLater(int(seconds * 1000) + 60, self._expire_toast)

    def _expire_toast(self):
        if time.time() >= self._toast_until:
            self._toast = None
            self.request_repaint()

    # ------------------------------------------------------------ 事件转发
    def mouse(self, kind, x, y, evt=None):
        return self.shell.mouse(kind, x, y, evt)

    def cursor_at(self, x, y):
        return self.shell.cursor_at(x, y)

    def tip_at(self, x, y):
        return self.shell.tip_at(x, y)

    def set_tip(self, tip):
        self.shell.set_tip(tip)

    # ------------------------------------------------------------ 通用交互
    def ask_name(self, title, default, on_ok):
        dlg = wx.TextEntryDialog(self, "给这个方案起个名字：", title, default)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                on_ok(dlg.GetValue().strip())
        finally:
            dlg.Destroy()

    def quick_scene(self, key):
        if A.apply_preset(self.ctx, key):
            name = {"quiet": "安静", "balanced": "均衡",
                    "performance": "性能", "max": "全速"}.get(key, key)
            self.toast("已切到「%s」" % name)

    def quick_auto(self):
        res = A.back_to_auto(self.ctx)
        self.toast("已交还给电脑" if res.get("ok") else "交还未完成，请查看安全日志")

    def quick_perf(self, mode_id):
        """托盘里直接切性能模式。EC 那条命令要等 64 位宿主，放线程里做。"""
        import threading

        def run():
            try:
                res = A.set_perf_mode(self.ctx, mode_id)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            if res.get("ok"):
                msg = "性能模式 → %s" % res.get("name", "")
            else:
                msg = "性能模式切换失败：%s" % (res.get("reason") or "EC 没有回应")
            wx.CallAfter(self.toast, msg)

        threading.Thread(target=run, name="perf-tray", daemon=True).start()


def _version():
    from version import VERSION
    return VERSION
