# -*- coding: utf-8 -*-
"""后台风扇控制；锁顺序为 store/control → guard → state。

硬件自动模式不主动接管。手动/曲线只使用新鲜、有效的温度；任何
失温/控制异常转自动，高温保护使用原始读数且跳过平滑与写入间隔。
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

from control import curve as curve_mod
from control.safety import SessionGuard
from hardware.backend import Backend
from hardware.ec import EcTimeout
from hardware.system import SystemSampler


class Engine(threading.Thread):
    def __init__(self, backend: Backend, guard: SessionGuard, config: Dict[str, Any], store):
        super().__init__(name="ec-engine", daemon=True)
        self.backend, self.guard, self.config, self.store = backend, guard, config, store
        self.sysinfo = SystemSampler()
        self.history = deque(maxlen=180)
        self.state = dict(temps={}, raw_temps={}, rpms={}, duty={}, cpu_load=0.0,
                          mem_load=0.0, mem_used=0.0, mem_total=0.0, fuse=False,
                          alerts=[], degraded=False, sensor_ok=False,
                          sensor_note="等待有效温度采样", updated=0)
        self._stop_event = threading.Event()
        self._suspended = threading.Event()
        self._wake = threading.Event()
        self._control_lock = getattr(store, "lock", threading.RLock())
        self._lock = threading.RLock()
        self._smoothed = {}
        self._last_write = {}
        self._last_cmd = {}
        self._temp_hist = {}
        self._sample_time = {}
        self._stall_since = {}
        self._stall_stage = {}
        self._err_streak = 0
        self.fast_hint = None
        self._last_any_write = 0.0

    @staticmethod
    def _valid_temp(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 125

    def sample_once(self, quick=False, skip_rpm=False):
        with self._control_lock:
            if self._stop_event.is_set() or self._suspended.is_set():
                return
            temps, rpms, duties = {}, {}, {}
            notes = []
            with self.guard.lock:
                for key, fan in self.config["fans"].items():
                    no, idx = int(key), int(fan.get("temp_index", key))
                    try:
                        if quick:
                            data = dict(temp=self.backend.bus.read_temp(idx),
                                        rpm=self.state["rpms"].get(no, -1),
                                        duty=self.state["duty"].get(no, -1))
                        elif skip_rpm and fan.get("mode") == "auto" and hasattr(self.backend.bus, "read_temp_raw"):
                            temp, _, b2 = self.backend.bus.read_temp_raw(idx)
                            data = dict(temp=temp, rpm=self.state["rpms"].get(no, -1),
                                        duty=self.backend.bus.from_ec(b2))
                        elif hasattr(self.backend.bus, "read_fan"):
                            data = self.backend.bus.read_fan(no, idx)
                        else:
                            data = dict(temp=self.backend.bus.read_temp(idx),
                                        rpm=self.backend.bus.read_rpm(no),
                                        duty=self.backend.bus.read_duty(no))
                        value = data.get("temp", -1)
                        temps[no] = int(value) if self._valid_temp(value) else -1
                        rpms[no] = data.get("rpm", -1)
                        duties[no] = data.get("duty", -1)
                    except Exception as exc:
                        temps[no] = rpms[no] = duties[no] = -1
                        notes.append(f"风扇{no}采样失败：{exc}")
                    if temps[no] < 0:
                        self._sample_time.pop(no, None)
                        self._temp_hist.pop(no, None)
                        notes.append(f"风扇{no}温度无效，不能继续软件调速")
                    else:
                        self._sample_time[no] = time.monotonic()
            raw = dict(temps)
            for no, val in temps.items():
                if val > 0:
                    buf = self._temp_hist.setdefault(no, deque(maxlen=3))
                    buf.append(val)
                    temps[no] = sorted(buf)[len(buf) // 2]
            try:
                info = self.sysinfo.sample()
            except Exception:
                info = {}
            with self._lock:
                self.state.update(temps=temps, raw_temps=raw, rpms=rpms, duty=duties,
                                  updated=int(time.time()), sensor_ok=bool(temps) and
                                  all(v > 0 for v in temps.values()) and
                                  all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in rpms.values()),
                                  sensor_note="；".join(notes))
                for key in ("cpu_load", "mem_load", "mem_used", "mem_total"):
                    self.state[key] = info.get(key, self.state[key])
                self.history.append(dict(t=int(time.time()), cpu=temps.get(1, -1),
                                         gpu=temps.get(2, -1), cpu_rpm=rpms.get(1, -1),
                                         gpu_rpm=rpms.get(2, -1), cpu_duty=duties.get(1, -1),
                                         gpu_duty=duties.get(2, -1)))

    def _target_for(self, fan_no, temp):
        fan = self.config["fans"][str(fan_no)]
        mode = fan.get("mode", "auto")
        if mode == "auto":
            return None
        if mode == "manual":
            return int(fan.get("manual_duty", 50))
        key = fan.get("curve_key", "cpu" if fan_no == 1 else "gpu")
        return curve_mod.interpolate(self.config["curve"].get(key, []), temp)

    def _fresh(self, no, now):
        raw = self.state["raw_temps"].get(no, -1)
        stamp = self._sample_time.get(no)
        # 软件接管时默认 1s 采样；宽限上限 10s，不能无限沿用旧值。
        ttl = max(3.0, min(10.0, float(self.config["safety"].get("tick", 1)) * 3))
        return self._valid_temp(raw) and stamp is not None and 0 <= now - stamp <= ttl

    def _clear_fan(self, no):
        for cache in (self._last_cmd, self._last_write, self._smoothed,
                      self._stall_since, self._stall_stage):
            cache.pop(no, None)

    def _return_auto(self, no, reason):
        # 先改变软件策略，后续拍继续重试，不能自动重新进入有故障的手动策略。
        fan = self.config["fans"][str(no)]
        changed = fan.get("mode") != "auto"
        fan["mode"] = "auto"
        result = self.guard.fan_auto(no, reason)
        self._clear_fan(no)
        if changed and self.store:
            self.store.save()
        if not result.get("ok"):
            self._push_alert(f"风扇{no}交还失败，将重试：{result.get('reason', '')}")
        return result

    def apply_once(self):
        with self._control_lock:
            if self._stop_event.is_set() or self._suspended.is_set():
                return
            cfg = self.config["safety"]
            fuse_temp = int(cfg["fuse_temp"])
            now, fuse_hit = time.monotonic(), False
            for key, fan in self.config["fans"].items():
                if self._stop_event.is_set() or self._suspended.is_set():
                    break
                no, mode = int(key), fan.get("mode", "auto")
                raw = self.state["raw_temps"].get(no, -1)
                temp = self.state["temps"].get(no, -1)
                if mode == "auto":
                    if self.guard.fan_mode.get(no) in ("manual", "unknown"):
                        self._return_auto(no, "自动模式重试交还固件")
                    self._clear_fan(no)
                    continue
                if not self._fresh(no, now):
                    self._push_alert(f"风扇{no}温度无效或过期，停止软件调速")
                    self.state["degraded"] = True
                    self._return_auto(no, "温度失效保护")
                    continue
                rpm = self.state["rpms"].get(no, -1)
                if type(rpm) not in (int, float) or not math.isfinite(rpm) or rpm < 0:
                    self.state["degraded"] = True
                    self._return_auto(no, "转速传感器失效，禁止软件调速")
                    continue
                target = self._target_for(no, temp)
                emergency = raw >= fuse_temp or temp >= fuse_temp
                near_hot = max(raw, temp) >= fuse_temp - 3
                fuse_hit = fuse_hit or emergency
                stage = self._stall_stage.get(no, 0)
                if emergency or stage == 1:
                    target = 100
                elif near_hot:
                    target = max(target, 90)
                if not cfg["allow_low_duty"]:
                    target = max(target, int(cfg["min_duty"]))
                # 高温和看门狗不准经过平滑，任何安全抬升也不受写入间隔限制。
                floor_raised = (not cfg["allow_low_duty"] and
                                self._last_cmd.get(no, 0) < int(cfg["min_duty"]))
                urgent = emergency or near_hot or stage == 1 or floor_raised
                prev = self._smoothed.get(no, float(target))
                smooth = float(target) if urgent else curve_mod.smooth(prev, float(target), 0.5)
                if abs(smooth - target) <= 1.5:
                    smooth = float(target)
                self._smoothed[no] = smooth
                cmd = int(round(smooth))
                last, stamp = self._last_cmd.get(no), self._last_write.get(no, 0.0)
                changed = last is None or abs(cmd - last) >= int(cfg["hysteresis"])
                stale = now - stamp >= 20
                if ((urgent and cmd != last) or
                        ((changed or stale) and now - stamp >= float(cfg["write_gap"]))):
                    res = self.guard.fan_duty(no, cmd, "紧急保护" if urgent else mode)
                    if res.get("ok"):
                        self._last_cmd[no] = res["duty"]
                        self._last_write[no] = self._last_any_write = now
                        with self._lock:
                            self.state["duty"][no] = res["duty"]
                    else:
                        self.state["degraded"] = True
                        self._push_alert(f"风扇{no}写入失败，转自动：{res.get('reason', '')}")
                        self._return_auto(no, "写入失败保护")
            with self._lock:
                self.state["fuse"] = fuse_hit
            if not self._stop_event.is_set():
                self._watchdog(now)

    def _watchdog(self, now):
        for key, fan in self.config["fans"].items():
            no = int(key)
            if fan.get("mode", "auto") == "auto":
                continue
            rpm = self.state["rpms"].get(no, -1)
            if type(rpm) not in (int, float) or not math.isfinite(rpm) or rpm < 0:
                self.state["degraded"] = True
                self._return_auto(no, "转速传感器失效保护")
                continue
            want = self._last_cmd.get(no, 0)
            if rpm > 0 or want < 50:
                self._stall_since.pop(no, None)
                self._stall_stage.pop(no, None)
                continue
            held = now - self._stall_since.setdefault(no, now)
            stage = self._stall_stage.get(no, 0)
            if stage == 0 and held >= 5:
                res = self.guard.fan_duty(no, 100, "转速看门狗：拉满重试")
                if res.get("ok"):
                    self._stall_stage[no] = 1
                    self._last_cmd[no] = 100
                    self._last_write[no] = self._last_any_write = now
                    self._smoothed[no] = 100.0
                    self._push_alert(f"风扇{no}转速为0，满速重试")
                else:
                    self._return_auto(no, "看门狗写入失败")
            elif stage == 1 and held >= 11:
                self._return_auto(no, "转速仍为0，交还固件")
                self._push_alert(f"风扇{no}转速仍为0，已请求固件接管")

    def _needs_fast(self):
        if any(fan.get("mode", "auto") != "auto" for fan in self.config["fans"].values()):
            return True
        if any(mode in ("manual", "unknown") for mode in self.guard.fan_mode.values()):
            return True
        if self.fast_hint:
            try:
                if self.fast_hint():
                    return True
            except Exception:
                pass
        fuse = int(self.config["safety"].get("fuse_temp", 92))
        if any(self._valid_temp(t) and t >= fuse - 10 for t in self.state["raw_temps"].values()):
            return True
        return time.monotonic() - self._last_any_write < 15

    def run(self):
        self.guard.mark_running()
        tick_no = 0
        while not self._stop_event.is_set():
            self._wake.clear()
            try:
                if self._suspended.is_set():
                    self._wake.wait(1)
                    continue
                fast = self._needs_fast()
                interval = float(self.config["safety"].get("tick" if fast else "idle_tick", 1 if fast else 5))
                self.sample_once(quick=not fast and tick_no % 12 != 0,
                                 skip_rpm=fast and tick_no % 2 == 1)
                self.apply_once()
                self._err_streak = 0
            except Exception as exc:
                self._err_streak += 1
                self._fail_safe(f"引擎异常：{type(exc).__name__}: {exc}")
                interval = 1.0
            tick_no += 1
            self._wake.wait(max(0.5, min(10.0, interval)))

    def _fail_safe(self, msg):
        with self._control_lock:
            self.state["degraded"] = True
            self._push_alert(msg + "（请求交还固件）")
            for key in self.config["fans"]:
                try:
                    self._return_auto(int(key), "引擎异常保护")
                except Exception as exc:
                    self._push_alert(f"还原失败：{exc}")

    def _push_alert(self, msg):
        with self._lock:
            alerts = self.state["alerts"]
            if alerts and alerts[0]["msg"] == msg:
                return
            alerts.insert(0, dict(ts=time.strftime("%H:%M:%S"), msg=msg))
            del alerts[8:]

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def suspend(self):
        # 不等待策略锁；在途写入结束后必须先交还，唤醒后不恢复旧手动模式。
        self._suspended.set()
        self._wake.set()

    def resume(self):
        with self._control_lock:
            self._sample_time.clear()
            self._temp_hist.clear()
            self.reassert()
            self._suspended.clear()
            self._wake.set()

    def snapshot(self):
        with self._lock:
            data = {k: dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v
                    for k, v in self.state.items()}
            updated = self.state["updated"]
            data["stalled"] = bool(updated) and time.time() - updated > 12
            data["history"] = list(self.history)[-90:]
            return data

    def reassert(self):
        with self._control_lock:
            self._last_cmd.clear()
            self._last_write.clear()
            self._wake.set()

    def set_mode(self, fan_no, mode, duty=None):
        with self._control_lock:
            if self._stop_event.is_set() or self._suspended.is_set():
                return dict(ok=False, reason="控制器正在退出或休眠")
            if str(fan_no) not in self.config["fans"] or mode not in ("auto", "manual", "curve"):
                return dict(ok=False, reason="未知风扇或模式")
            if duty is not None and (type(duty) is not int or not 0 <= duty <= 100):
                return dict(ok=False, reason="风量必须为0..100的整数")
            no = int(fan_no)
            fan = self.config["fans"][str(no)]
            fan["mode"] = mode
            if duty is not None:
                fan["manual_duty"] = duty
            # 调整模式不能取消正在进行的失速保护；只有转速恢复或交还固件才解锁。
            for cache in (self._last_cmd, self._last_write, self._smoothed):
                cache.pop(no, None)
            if mode == "auto":
                result = self._return_auto(no, "手动切回自动")
            else:
                # 即时切换同样走失温/高温/看门狗策略，不直接写硬件。
                self.apply_once()
                result = dict(ok=fan["mode"] == mode)
                if not result["ok"]:
                    result["reason"] = "安全保护已阻止软件接管，请检查传感器与日志"
            self.store.save()
            result["mode"] = fan["mode"]
            return result
