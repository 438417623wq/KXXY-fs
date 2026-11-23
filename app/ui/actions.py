# -*- coding: utf-8 -*-
"""原生界面的内部动作；配置事务统一先取 store.lock，再进入 guard。"""
from __future__ import annotations

import copy

from control import curve as curve_mod
from control import profiles


def set_mode(ctx, fan: int, mode: str, duty=None):
    with ctx.store.lock:
        # 配置、保护判断及实际下发均由引擎负责，不覆盖失败或保护后的结果。
        return ctx.engine.set_mode(fan, mode, duty)


def set_duty(ctx, fan: int, duty: int):
    return set_mode(ctx, fan, "manual", duty)


def set_curve(ctx, key: str, points):
    if key not in ("cpu", "gpu"):
        raise ValueError("未知风扇曲线")
    pts = curve_mod.normalize(points or [])
    with ctx.store.lock:
        ctx.store.data["curve"][key] = [list(p) for p in pts]
        ctx.store.save()
    return pts


def apply_preset(ctx, name: str):
    preset = curve_mod.PRESETS.get(name)
    if not preset:
        return False
    with ctx.store.lock:
        for key in ("cpu", "gpu"):
            ctx.store.data["curve"][key] = [list(p) for p in preset[key]]
        ctx.store.save()
        results = [ctx.engine.set_mode(no, "curve") for no in (1, 2)]
        return all(res.get("ok") for res in results)


def back_to_auto(ctx):
    with ctx.store.lock:
        results = [ctx.engine.set_mode(int(no), "auto") for no in ctx.store.data["fans"]]
        return {"ok": all(r.get("ok") for r in results),
                "errors": [r.get("reason", "交还失败") for r in results if not r.get("ok")]}


def save_profile(ctx, name: str):
    with ctx.store.lock:
        return profiles.save(ctx.store, name)


def apply_profile(ctx, pid: str):
    with ctx.store.lock:
        return profiles.apply(ctx.store, ctx.engine, pid)


def rename_profile(ctx, pid: str, name: str):
    with ctx.store.lock:
        return profiles.rename(ctx.store, pid, name)


def delete_profile(ctx, pid: str):
    with ctx.store.lock:
        return profiles.delete(ctx.store, pid)


def profile_list(ctx):
    with ctx.store.lock:
        return copy.deepcopy(profiles.list_all(ctx.store))


def restore_all(ctx, fans=None):
    with ctx.store.lock:
        res = ctx.guard.restore_all(fans)
        for key, fan in ctx.store.data["fans"].items():
            if fans is None or int(key) in fans:
                fan["mode"] = "auto"
        ctx.store.save()
        for no in (fans or list(range(1, 5))):
            ctx.engine._last_cmd.pop(no, None)
        return res


def set_safety(ctx, patch: dict):
    with ctx.store.lock:
        return ctx.store.update_safety(patch)


def clear_journal(ctx):
    with ctx.guard.lock:
        ctx.guard.journal.clear_history()


def light_update(ctx, patch: dict):
    with ctx.store.lock:
        return ctx.light.update(patch)


def light_preset(ctx, name: str):
    with ctx.store.lock:
        return ctx.light.set_preset(name)


def light_apply_color(ctx, rgb):
    return light_update(ctx, {"color": list(rgb)})


def light_off(ctx):
    return light_update(ctx, {"enabled": False})


def light_on(ctx):
    return light_update(ctx, {"enabled": True})


def perf_status(ctx):
    with ctx.store.lock:
        return ctx.perf.status()


def perf_modes():
    """四档定义（界面画卡片要用）。"""
    from control.perfmode import MODES

    return [dict(m) for m in MODES]


def set_perf_mode(ctx, mode: int):
    with ctx.store.lock:
        return ctx.perf.apply(int(mode))
