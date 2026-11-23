# -*- coding: utf-8 -*-
"""窗口尺寸计算。

战神这一系列笔记本屏幕差别很大（1366×768 / 1920×1080 / 2560×1440…），
把窗口写死成 1320×900，在小屏或高缩放比的机器上底边会被任务栏顶到屏幕
外面 —— 用户看到的就是「界面底部显示不全」，而且滚动条也在屏幕外。

所以尺寸按三级回落到：
  1. 用户在设置里指定的尺寸（win_w / win_h，0 表示自适应）
  2. 主屏工作区（已扣任务栏）以内，留 24px 边距
  3. 设计基准 1320×900

（浏览器窗口那套实现已随 web 界面一并移除，这里只服务原生 wx 窗口。）
"""
from __future__ import annotations

DESIGN_W, DESIGN_H = 1320, 900
MIN_W, MIN_H = 760, 540
MARGIN = 24


def screen_work_area():
    """主屏可用工作区（DIP，已扣任务栏）：(x, y, w, h)；拿不到返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        try:
            dpi = user32.GetDpiForSystem() or 96   # DPI 感知时这里是物理像素
        except Exception:
            dpi = 96
        scale = dpi / 96.0 if dpi > 96 else 1.0
        return (int(rect.left / scale), int(rect.top / scale),
                int(w / scale), int(h / scale))
    except Exception:
        return None


def window_size(win_w: int = 0, win_h: int = 0):
    """算出窗口尺寸 DIP 和居中位置，返回 (w, h, (x, y) 或 None)。"""
    w = int(win_w) if win_w and int(win_w) >= MIN_W else DESIGN_W
    h = int(win_h) if win_h and int(win_h) >= MIN_H else DESIGN_H

    area = screen_work_area()
    if not area:
        return w, h, None
    ax, ay, aw, ah = area
    # 屏幕装得下就按用户的来，装不下就收到工作区里（这部分是硬上限）
    w = min(w, max(MIN_W, aw - MARGIN))
    h = min(h, max(MIN_H, ah - MARGIN))
    return w, h, (max(0, ax + (aw - w) // 2), max(0, ay + (ah - h) // 2))
