# -*- coding: utf-8 -*-
"""托盘菜单自检：确认「性能模式」子菜单能建起来且四项都在。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import wx  # noqa: E402
import wx.adv  # noqa: E402


class Dummy:
    def __init__(self):
        self.root = ROOT
        self.ctx = type("_Ctx", (), {"root": ROOT})()

    def show_window(self):
        pass

    def quick_scene(self, k):
        pass

    def quick_auto(self):
        pass

    def quick_perf(self, m):
        print("quick_perf", m)

    def quit(self):
        pass


def main():
    app = wx.App(False)
    from ui.app_native import Tray

    tray = Tray(Dummy())
    menu = tray.CreatePopupMenu()
    labels = []
    for item in menu.GetMenuItems():
        if item.IsSubMenu():
            sub = item.GetSubMenu()
            labels.append("[%s]" % item.GetItemLabel())
            for si in sub.GetMenuItems():
                labels.append("   - %s" % si.GetItemLabel())
        else:
            labels.append(item.GetItemLabel() or "---")
    print("\n".join(labels))
    app.Destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
