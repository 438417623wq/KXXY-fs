# -*- coding: utf-8 -*-
"""键盘灯控制：DCHU 单区/三区 RGB、静态与软件灯效、试灯和唤醒恢复。

配置事务先取 store.lock，再取 guard.lock；桥的内部锁始终在 guard 之后。
灯效和唤醒监听各只有一个 worker，关闭事件只置位，不复用或清除。
"""
from __future__ import annotations

import colorsys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from hardware import conflicts
from hardware.dchu_bridge import DchuBridge

PRESETS: List[Tuple[str, str]] = [
    ("白", "#ffffff"), ("暖白", "#ffe9c4"), ("橙", "#ff7a00"),
    ("琥珀", "#ffb020"), ("红", "#ff2d2d"), ("品红", "#ff2dcb"),
    ("紫", "#8b5cf6"), ("蓝", "#2d6bff"), ("青", "#22d3ee"),
    ("绿", "#22c55e"), ("冰蓝", "#bfe9ff"), ("关灯", "#000000"),
]
EFFECTS: List[Tuple[str, str]] = [
    ("static", "静态"), ("breath", "呼吸"), ("rainbow", "彩虹流转"),
    ("cycle", "色彩轮播"), ("wave", "单色波浪"), ("heartbeat", "心跳"),
]
ZONES_SUPPORTED = False
ZONE_NOTE = (
    "本机是单区 RGB 键盘：左/中/右区实测与「整个键盘」效果相同（三条命令都是整块变色），"
    "只有三区机型（P7xx/P8xx 等）才会真正分区。"
)
LED_OWNERS = conflicts.LED_KNOWN


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) != 6:
        return (0, 0, 0)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _scale(rgb: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    f = max(0.0, min(1.0, factor))
    return tuple(max(0, min(255, int(round(c * f)))) for c in rgb)


class LightController:
    def __init__(self, guard, store, log=None, bridge=None, bridge_owned: bool = True):
        self.guard, self.store = guard, store
        self.log = log or (lambda *a, **k: None)
        self.bridge = bridge if bridge is not None else DchuBridge(log=self.log, single_zone=not ZONES_SUPPORTED)
        # main 可显式托管共享桥；False 时 close/空闲/唤醒均不替 main 停桥。
        self.bridge_owned = bridge_owned
        self._lock = threading.RLock()
        self._closed_evt = threading.Event()
        self._effect_stop_evt = threading.Event()
        self._resume_stop_evt = threading.Event()
        self._effect_wake = threading.Event()
        self._resume_wake = threading.Event()
        self._resume_pending = threading.Event()
        self._effect_thread: Optional[threading.Thread] = None
        self._resume_thread: Optional[threading.Thread] = None
        self._bridge_closed = False
        self._last_error = ""
        self._applied: Optional[Tuple[int, int, int]] = None
        self._frame = 0
        # 试灯截止时间也由唯一灯效 worker 处理，不为每次试灯建立 Timer。
        self._trial_backup: Optional[Dict[str, Any]] = None
        self._trial_rgb: Optional[Tuple[int, int, int]] = None
        self._trial_until = 0.0
        self._mono_ts, self._wall_ts = time.monotonic(), time.time()
        self._last_activity = time.monotonic()
        self._dump_thread: Optional[threading.Thread] = None
        self._ecmap_dead = False

    # ------------------------------------------------------------------ 配置
    @property
    def cfg(self) -> Dict[str, Any]:
        with self.store.lock:
            light = self.store.data["light"]
            light.setdefault("enabled", False)
            light.setdefault("brightness", 70)
            light.setdefault("color", [255, 80, 0])
            light.setdefault("zone", 0)
            light.setdefault("effect", "static")
            light.setdefault("speed", 50)
            light.setdefault("restore_on_resume", True)
            light.setdefault("saved_hex", "#000000")
            light.setdefault("prev_color", [0, 0, 0])
            return light

    def _save(self) -> bool:
        try:
            self.store.save()
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"保存灯效失败：{type(exc).__name__}: {exc}"
            return False

    def _blocked_reason(self) -> str:
        if self._closed_evt.is_set():
            return "灯控已关闭"
        if self.guard.writes_blocked:
            return "控制器正在退出或休眠"
        return ""

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        with self.store.lock, self._lock:
            if self._closed_evt.is_set() or self._resume_thread is not None:
                return
            self._resume_thread = threading.Thread(
                target=self._watch_resume, name="kbled-resume", daemon=True)
            self._resume_thread.start()

    def _warm(self) -> None:
        with self.store.lock:
            if self._closed_evt.is_set() or not self.cfg["enabled"]:
                return
            if self.cfg["effect"] == "static":
                self._push_static()
            else:
                self._restart_effect_loop()

    def on_resume(self) -> None:
        """仅投递合并请求；main 自行处理 guard/engine，灯控不碰风扇。"""
        with self._lock:
            if not self._closed_evt.is_set():
                self._resume_pending.set()
                self._resume_wake.set()

    def close(self) -> None:
        # 先封禁排队中的操作，再等待在途事务结束，不能让迟到 warm/resume 重开桥。
        self._closed_evt.set()
        self._effect_stop_evt.set()
        self._resume_stop_evt.set()
        self._effect_wake.set()
        self._resume_wake.set()
        with self.store.lock:
            self._trial_backup = None
            self._trial_until = 0.0
            with self.guard.lock, self._lock:
                self._applied = None
                if self.bridge_owned and not self._bridge_closed:
                    try:
                        self.bridge.stop()
                        self._bridge_closed = True
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = f"关闭灯桥失败：{exc}"
                workers = (self._effect_thread, self._resume_thread, self._dump_thread)
        # 不能拿着 store/guard/light 锁 join 等锁中的 worker。
        deadline = time.monotonic() + 2.0
        for worker in workers:
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=max(0.0, deadline - time.monotonic()))

    # ------------------------------------------------------------------ 状态
    def status(self) -> Dict[str, Any]:
        with self.store.lock:
            cfg = self.cfg
            color = tuple(cfg["color"])
            eff = self._effective_rgb()
            with self._lock:
                state = {
                    "available": self.bridge.available,
                    "reason": self.bridge.reason,
                    "enabled": bool(cfg["enabled"]),
                    "brightness": int(cfg["brightness"]),
                    "color": list(color), "hex": rgb_to_hex(color),
                    "zone": int(cfg["zone"]), "zones_supported": ZONES_SUPPORTED,
                    "zone_note": ZONE_NOTE, "effect": str(cfg["effect"]),
                    "speed": int(cfg["speed"]), "effective": list(eff),
                    "effective_hex": rgb_to_hex(eff), "applied": self._applied is not None,
                    "last_error": self._last_error or self.bridge.reason,
                    "prev_hex": rgb_to_hex(tuple(cfg["prev_color"])),
                    "restore_on_resume": bool(cfg["restore_on_resume"]),
                    "presets": [{"name": n, "hex": h} for n, h in PRESETS],
                    "effects": [{"key": k, "label": v} for k, v in EFFECTS],
                    "trial": self._trial_backup is not None,
                }
        state["owners"] = self.owners()
        return state

    @staticmethod
    def owners(force: bool = False) -> List[Dict[str, Any]]:
        return [item for item in conflicts.query(force=force)
                if any(key in (item["name"] + " " + item.get("service", "")).lower()
                       for key in LED_OWNERS)]

    def _effective_rgb(self) -> Tuple[int, int, int]:
        with self.store.lock:
            cfg = self.cfg
            if not cfg["enabled"]:
                return (0, 0, 0)
            base = tuple(int(c) for c in cfg["color"])
            level = float(cfg["brightness"]) / 100.0
            if cfg["effect"] != "static":
                return self._effect_color(base, level)
            return _scale(base, level)

    def _effect_color(self, base: Tuple[int, int, int], level: float) -> Tuple[int, int, int]:
        t = time.time()
        speed = max(1, min(100, int(self.cfg["speed"]))) / 100.0
        effect = self.cfg["effect"]
        if effect == "breath":
            k = 0.5 + 0.5 * _sin(t * (0.35 + speed * 1.4))
            return _scale(base, level * (0.12 + 0.88 * k))
        if effect == "heartbeat":
            phase = (t * (0.6 + speed)) % 1.0
            k = max(_pulse(phase, 0.10), 0.55 * _pulse(phase, 0.30))
            return _scale(base, level * (0.10 + 0.90 * k))
        if effect == "wave":
            k = 0.5 + 0.5 * _sin(t * (0.5 + speed * 2.0))
            hue = (t * 0.05) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
            return _scale((int(r * 255), int(g * 255), int(b * 255)), level * (0.2 + 0.8 * k))
        if effect in ("rainbow", "cycle"):
            step = (t * (0.05 + speed * 0.35)) % 1.0
            if effect == "cycle":
                step = int(step * 6) / 6.0
            r, g, b = colorsys.hsv_to_rgb(step, 1.0, 1.0)
            return _scale((int(r * 255), int(g * 255), int(b * 255)), level)
        return _scale(base, level)

    # ------------------------------------------------------------------ 写入
    def _push(self, rgb: Tuple[int, int, int], persist: bool = False) -> bool:
        with self.store.lock:
            zone = int(self.cfg["zone"])
            with self.guard.lock, self._lock:
                reason = self._blocked_reason()
                if reason:
                    self._applied, self._last_error = None, reason
                    return False
                self._last_activity = time.monotonic()
                try:
                    ok = self.bridge.set_color(*rgb, zone=zone)
                    # set_color 期间可能收到关闭/休眠，不能再补一条持久化写入。
                    reason = self._blocked_reason()
                    if ok and persist and not reason:
                        ok = self.bridge.save_color(*rgb)
                        reason = self._blocked_reason()
                except Exception as exc:  # noqa: BLE001
                    ok, reason = False, f"写入灯效失败：{type(exc).__name__}: {exc}"
                ok = bool(ok and not reason)
                self._applied = rgb if ok else None
                self._last_error = "" if ok else reason or self.bridge.reason or "写入失败"
                return ok

    def _push_static(self) -> bool:
        with self.store.lock:
            return self._push(self._effective_rgb(), persist=True)

    def _restart_effect_loop(self) -> None:
        # 保留旧入口，但不再反复 stop/join/clear。静态时 worker 停在 wake.wait，
        # 下一次动画直接唤醒同一个 worker，不会出现多代线程同时写灯。
        with self.store.lock:
            active = self._trial_backup is not None or (self.cfg["enabled"] and self.cfg["effect"] != "static")
            with self._lock:
                if self._closed_evt.is_set():
                    return
                if active and self._effect_thread is None:
                    self._effect_thread = threading.Thread(
                        target=self._effect_loop, args=(self._effect_stop_evt,), name="kbled-fx", daemon=True)
                    self._effect_thread.start()
                self._effect_wake.set()

    def _effect_loop(self, stop_evt: threading.Event) -> None:
        while not stop_evt.is_set():
            self._effect_wake.clear()
            delay = None
            try:
                with self.store.lock:
                    if self._closed_evt.is_set() or stop_evt.is_set():
                        break
                    if self._trial_backup is not None:
                        delay = max(0.0, self._trial_until - time.monotonic())
                        if delay == 0:
                            self._trial_done()
                            delay = 0.05
                    elif self.cfg["enabled"] and self.cfg["effect"] != "static":
                        self._push(self._effective_rgb())
                        self._frame += 1
                        delay = 0.05
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                delay = 1.0
            self._effect_wake.wait(delay)

    # ------------------------------------------------------------------ 对外修改
    def update(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self.store.lock:
            with self.guard.lock:
                reason = self._blocked_reason()
                if reason:
                    return {"ok": False, "reason": reason}
            cfg = self.cfg
            if "enabled" in data:
                cfg["enabled"] = bool(data["enabled"])
            if "brightness" in data:
                cfg["brightness"] = max(0, min(100, int(data["brightness"])))
            if "color" in data:
                rgb = data["color"]
                if isinstance(rgb, str):
                    rgb = hex_to_rgb(rgb)
                if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
                    cfg["prev_color"] = list(cfg["color"]) if cfg["effect"] == "static" else list(cfg["prev_color"])
                    cfg["color"] = [max(0, min(255, int(c))) for c in rgb]
                    cfg["saved_hex"] = rgb_to_hex(cfg["color"])
            if "hex" in data:
                rgb = hex_to_rgb(str(data["hex"]))
                cfg["prev_color"] = list(cfg["color"])
                cfg["color"] = list(rgb)
                cfg["saved_hex"] = rgb_to_hex(rgb)
            if "zone" in data:
                cfg["zone"] = max(0, min(3, int(data["zone"])))
            if "effect" in data:
                cfg["effect"] = str(data["effect"])
            if "speed" in data:
                cfg["speed"] = max(1, min(100, int(data["speed"])))
            if "restore_on_resume" in data:
                cfg["restore_on_resume"] = bool(data["restore_on_resume"])
            self._trial_backup = None
            self._trial_until = 0.0
            saved = self._save()
            ok = saved and self._push(self._effective_rgb(), persist=not cfg["enabled"] or cfg["effect"] == "static")
            self._restart_effect_loop()
        return {"ok": bool(ok), "status": self.status()}

    def set_preset(self, name: str) -> Dict[str, Any]:
        for label, hexv in PRESETS:
            if label == name or hexv == name:
                return self.update({"hex": hexv, "effect": "static", "enabled": hexv != "#000000"})
        return {"ok": False, "reason": "未知预设"}

    # ------------------------------------------------------------------ 试灯
    def trial(self, rgb: Tuple[int, int, int], seconds: float = 8.0) -> Dict[str, Any]:
        with self.store.lock:
            with self.guard.lock:
                reason = self._blocked_reason()
                if reason:
                    return {"ok": False, "reason": reason}
            if self._trial_backup is None:
                self._trial_backup = {key: self.cfg[key] for key in ("color", "enabled", "brightness", "effect")}
            self._trial_rgb = tuple(max(0, min(255, int(c))) for c in rgb)
            self._trial_until = time.monotonic() + max(1.0, seconds)
            ok = self._push(_scale(self._trial_rgb, float(self.cfg["brightness"]) / 100.0))
            self._restart_effect_loop()
            return {"ok": ok, "auto_revert_in": seconds}

    def _trial_done(self) -> None:
        with self.store.lock:
            if self._trial_backup is None:
                return
            self._trial_backup = None
            self._trial_until = 0.0
            if self._closed_evt.is_set():
                return
            if not self.cfg["enabled"] or self.cfg["effect"] == "static":
                self._push_static()
            self._restart_effect_loop()

    def keep_trial(self) -> Dict[str, Any]:
        with self.store.lock:
            rgb = self._trial_rgb or tuple(self.cfg["color"])
            result = self.update({"color": list(rgb), "enabled": True, "effect": "static"})
            return {"ok": result["ok"], "hex": rgb_to_hex(rgb)}

    # ------------------------------------------------------------------ 还原
    def revert(self) -> Dict[str, Any]:
        with self.store.lock:
            prev = tuple(int(c) for c in self.cfg["prev_color"])
            result = self.update({"color": list(prev), "effect": "static", "enabled": prev != (0, 0, 0)})
            return {"ok": result["ok"], "hex": rgb_to_hex(prev)}

    def restore_saved(self) -> Dict[str, Any]:
        with self.store.lock:
            rgb = hex_to_rgb(self.cfg["saved_hex"])
            if rgb == (0, 0, 0):
                return {"ok": False, "reason": "上次保存的是关灯状态"}
            return self.update({"color": list(rgb), "enabled": True, "effect": "static"})

    # ------------------------------------------------------------------ 唤醒监听
    def _refresh_on_resume(self) -> bool:
        with self.store.lock:
            with self.guard.lock, self._lock:
                if self._closed_evt.is_set():
                    return True
                if self.guard.writes_blocked:
                    return False
                self._applied = None
                if self.bridge_owned:
                    self.bridge.stop()
                if self._blocked_reason():
                    return False
            if self.cfg["restore_on_resume"] and self.cfg["enabled"]:
                if self.cfg["effect"] == "static":
                    self._push_static()
                else:
                    self._restart_effect_loop()
            return True

    def _release_idle_bridge(self) -> None:
        with self.store.lock:
            enabled = bool(self.cfg["enabled"])
            with self.guard.lock, self._lock:
                if (self.bridge_owned and not self._blocked_reason() and not enabled
                        and self._trial_backup is None and self.bridge.running
                        and time.monotonic() - self._last_activity > 600):
                    self.bridge.stop()
                    self._applied = None
                    self.log("灯效空闲，已释放 64 位宿主")

    def _watch_resume(self) -> None:
        try:
            self._warm()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
        while not self._resume_stop_evt.is_set():
            with self.store.lock:
                enabled = bool(self.cfg["enabled"])
            self._resume_wake.wait(5.0 if enabled else 30.0)
            self._resume_wake.clear()
            if self._resume_stop_evt.is_set():
                break
            try:
                mono, wall = time.monotonic(), time.time()
                drift = (wall - self._wall_ts) - (mono - self._mono_ts)
                self._mono_ts, self._wall_ts = mono, wall
                with self._lock:
                    pending = self._resume_pending.is_set() or drift > 20
                    self._resume_pending.clear()
                if pending and not self._refresh_on_resume():
                    self._resume_pending.set()
                self._release_idle_bridge()
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)

    # ------------------------------------------------------------------ 遗留：只读诊断
    def _raw_dump(self) -> List[Optional[int]]:
        result: Dict[str, Any] = {}

        def run():
            try:
                with self.guard.lock:
                    if not self._blocked_reason():
                        result["dump"] = self.guard.bus.read_block(0x00, 0x100)
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)

        with self._lock:
            if self._closed_evt.is_set() or self._ecmap_dead or (self._dump_thread and self._dump_thread.is_alive()):
                return []
            th = threading.Thread(target=run, name="kbled-dump", daemon=True)
            self._dump_thread = th
            th.start()
        th.join(timeout=4.0)
        if th.is_alive() or "error" in result:
            self._ecmap_dead = True
            return []
        return result.get("dump") or []

    def dump(self) -> List[Optional[int]]:
        return self._raw_dump()

    @staticmethod
    def diff(before: List[Optional[int]], after: List[Optional[int]]) -> List[Dict[str, Any]]:
        out = []
        for addr in range(min(len(before), len(after))):
            a, b = before[addr], after[addr]
            if a is None or b is None or a == b:
                continue
            out.append({"addr": addr, "addr_hex": f"0x{addr:02X}", "before": a, "after": b})
        return out

    def commit_addr(self, addr: int, note: str = "") -> Dict[str, Any]:
        return {"ok": False, "reason": "本机已走 DCHU 灯控通道，不再需要手动绑定 EC 寄存器"}


def _sin(x: float) -> float:
    import math

    return math.sin(x * 6.283185307179586)


def _pulse(phase: float, center: float) -> float:
    """窄脉冲：在 center 附近快速起落。"""
    d = abs(phase - center)
    d = min(d, 1.0 - d)
    if d > 0.12:
        return 0.0
    return max(0.0, 1.0 - d / 0.12)
