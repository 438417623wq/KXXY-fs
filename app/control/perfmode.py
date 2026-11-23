# -*- coding: utf-8 -*-
"""性能模式（蓝天 EC 的四档功耗策略）。

这四档就是蓝天 Control Center 里的「省电 / 静音 / 娱乐 / 性能」，
也等于 BIOS 的 Setup Utility → Advanced Chipset Control → Performance Profile。

它们改的不是风扇转速（那是我们自己的曲线管的），而是固件侧的
CPU/GPU 功耗上限、睿频策略和固件自带的风扇曲线：

    0 静音 Quiet          压 CPU/GPU 功耗，风扇尽量小声
    1 省电 Power Saving   功耗压得更狠，续航优先（插电用不到）
    2 性能 Performance    CPU/GPU 满功耗，风扇更积极
    3 娱乐 Entertainment  两边折中，出厂默认就是这档

下发路径
--------
EC 命令 0x79，子功能 0x19，参数 = (0x19 << 24) | 模式号。
这在 tuxedo-keyboard 驱动里写作 CLEVO_CMD_OPT + CLEVO_CMD_OPT_SUB_SET_PERF_PROF；
在 Windows 上则通过 InsydeDCHU.dll 的 SetDCHU_Data(0x79, …) 下发
（蓝天 Control Center 用的就是这条）。DCHU 是 64 位库，所以实际由
hardware/dchu_bridge.py 的 64 位宿主代发。

一条重要限制：**这条命令只写不读**。本机固件没有对应的读取子功能
（GetDCHU_Data_Integer / ReadAppSettings 都拿不到模式号），所以
「现在是哪一档」只能由我们自己记；重启后 BIOS 会保持上次的设置，
但我们并不知道它在 BIOS 里被改成了什么，这时界面显示「未知」。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

#: 四档定义。id 就是写进 EC 的值，顺序按 BIOS 里的排列（省电→静音→娱乐→性能），
#: 但界面会按「常用程度」重新排。
MODES = [
    {
        "id": 3,
        "key": "entertainment",
        "name": "娱乐",
        "en": "Entertainment",
        "tag": "出厂默认",
        "short": "功耗折中，日常用这档",
        "desc": "CPU 与 GPU 都留有余量，风扇不会一直吵。看视频、办公、轻度游戏都合适。",
        "cpu": "中",
        "gpu": "中",
        "fan": "标准",
    },
    {
        "id": 2,
        "key": "performance",
        "name": "性能",
        "en": "Performance",
        "tag": "最猛",
        "short": "CPU/GPU 满功耗，风扇更积极",
        "desc": "放开处理器和显卡的功耗上限，游戏、渲染、编译时帧数和频率最高。代价是风扇更吵、机身更热。",
        "cpu": "高",
        "gpu": "高",
        "fan": "积极",
    },
    {
        "id": 0,
        "key": "quiet",
        "name": "静音",
        "en": "Quiet",
        "tag": "最安静",
        "short": "压功耗换安静，适合夜里",
        "desc": "限制 CPU/GPU 的峰值功耗，风扇转速明显下降。深夜码字、看片用这档基本听不见风声。",
        "cpu": "低",
        "gpu": "低",
        "fan": "安静",
    },
    {
        "id": 1,
        "key": "powersave",
        "name": "省电",
        "en": "Power Saving",
        "tag": "续航优先",
        "short": "关睿频、压功耗，电池能撑更久",
        "desc": "进一步收紧功耗上限并关闭部分睿频，电池模式下最省电。插着电用它，性能会明显变弱。",
        "cpu": "很低",
        "gpu": "低",
        "fan": "安静",
    },
]

_BY_ID = {m["id"]: m for m in MODES}


def mode_of(mode_id: Any) -> Optional[Dict[str, Any]]:
    try:
        return _BY_ID.get(int(mode_id))
    except (TypeError, ValueError):
        return None


class PerfMode:
    """把 DCHU 通道 + 本地记忆包成一个小控制器。

    guard / on_applied 是为了和风扇控制共存：
      · guard.lock —— 0x79 和风扇的 0x99 走的是同一对 EC 端口（0x66/0x62），
        这对端口是有状态的、没有抢占机制，两条线程同时发命令会串字节，
        所以切档必须和风扇写互斥。
      · on_applied —— 切档会重排 EC 的风扇表，固件可能顺手把手动控制收回，
        切换成功后要让引擎立刻重申一次占空比，不能等它 20 秒的 stale 重申。
    """

    def __init__(self, bridge, store, log=None, guard=None, on_applied=None):
        self.bridge = bridge
        self.store = store
        self.log = log or (lambda *a, **k: None)
        self.guard = guard
        self.on_applied = on_applied
        self._last_error = ""
        self._last_rc: Optional[int] = None
        self._switch_lock = threading.Lock()
        # 持久化记录只是上次请求，不是可回读的硬件状态；开机绝不自动重放。
        with self.store.lock:
            self._cfg()["known"] = False

    # ---------------------------------------------------------------- 配置
    def _cfg(self) -> Dict[str, Any]:
        cfg = self.store.data.setdefault("perf", {})
        cfg.setdefault("mode", None)
        cfg.setdefault("known", False)
        cfg.setdefault("ts", 0)
        return cfg

    # ---------------------------------------------------------------- 状态
    @property
    def available(self) -> bool:
        return bool(getattr(self.bridge, "available", False))

    def status(self) -> Dict[str, Any]:
        with self.store.lock:
            cfg = self._cfg()
            cur = cfg.get("mode")
            info = mode_of(cur) if cfg.get("known") else None
            return {
                "available": self.available,
                "reason": getattr(self.bridge, "reason", "") if not self.available else "",
                "known": bool(info),
                "mode": int(cur) if cur is not None else None,
                "name": info["name"] if info else "",
                "ts": int(cfg.get("ts") or 0),
                "last_error": self._last_error,
                "last_rc": self._last_rc,
                "modes": [dict(m) for m in MODES],
            }

    # ---------------------------------------------------------------- 切换
    def _send(self, mode_id: int) -> Optional[int]:
        """调用方持有 store.lock；先取 guard.lock，再进入桥的内部锁。"""
        if self.guard is None:
            self._last_error = "没有硬件写入守卫，拒绝切换性能模式"
            return None
        with self.guard.lock:
            if self.guard.writes_blocked:
                self._last_error = "控制器正在退出或休眠"
                return None
            return self.bridge.set_perf_mode(mode_id)

    def apply(self, mode_id: int) -> Dict[str, Any]:
        info = mode_of(mode_id)
        applied = False
        # 原生 UI 也可能已经持有 store.lock，因此顺序必须始终是 store → switch → guard。
        # 串行范围包括 send、内存状态和落盘，而不只是 EC 命令。
        with self.store.lock, self._switch_lock:
            cfg = self._cfg()
            cfg["known"] = False
            self._last_error = ""
            rc = None
            if info is None:
                reason = f"没有这一档：{mode_id}"
            elif not self.available:
                reason = getattr(self.bridge, "reason", "") or "DCHU 通道不可用"
            else:
                try:
                    rc = self._send(info["id"])
                    if rc is None:
                        reason = self._last_error or getattr(self.bridge, "reason", "") or "EC 没有回应"
                    elif rc != 0:
                        reason = f"EC 返回码 {rc}"
                    else:
                        reason = ""
                        applied = True
                        cfg.update(mode=info["id"], known=True, ts=int(time.time()))
                except Exception as exc:  # noqa: BLE001
                    reason = f"性能模式切换失败：{type(exc).__name__}: {exc}"
            self._last_rc = rc
            try:
                self.store.save()
            except Exception as exc:  # noqa: BLE001
                cfg["known"] = False
                reason = f"保存性能模式状态失败：{type(exc).__name__}: {exc}"
            self._last_error = reason
            if reason:
                result = {"ok": False, "reason": reason}
                if rc is not None:
                    result["rc"] = rc
            else:
                result = {"ok": True, "mode": info["id"], "name": info["name"], "rc": rc}

        if result["ok"]:
            self.log(f"性能模式 → {info['name']}（{info['en']}，EC 0x79/0x19 = {info['id']}）")
        else:
            self.log(result["reason"])
        # 释放本方法的所有锁后再通知引擎，不能持 guard/switch 锁反向等待 store。
        # 即使落盘失败，只要固件确认切档，也需通知引擎重申保护。
        if applied and self.on_applied is not None:
            try:
                self.on_applied()
            except Exception as exc:  # noqa: BLE001
                self.log(f"切档后重申风扇失败（不影响切档）：{type(exc).__name__}: {exc}")
        return result
