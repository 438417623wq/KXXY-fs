# -*- coding: utf-8 -*-
"""配置持久化（data/config.json）。"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from typing import Any, Dict

from control.curve import DEFAULT_CURVES

DEFAULT_CONFIG: Dict[str, Any] = {
    "fans": {
        "1": {"label": "CPU 风扇", "temp_index": 1, "mode": "auto", "manual_duty": 50, "curve_key": "cpu"},
        "2": {"label": "GPU 风扇", "temp_index": 2, "mode": "auto", "manual_duty": 50, "curve_key": "gpu"},
    },
    "curve": {
        "cpu": [list(p) for p in DEFAULT_CURVES["cpu"]],
        "gpu": [list(p) for p in DEFAULT_CURVES["gpu"]],
    },
    #: 用户自己存的方案（control/profiles.py 管理）
    "profiles": [],
    #: 性能模式（蓝天 EC 四档）：mode 是 EC 里的模式号，known 表示是不是我们自己设的。
    #: 这条 EC 命令只写不读，所以没设过就只能显示「未知」。
    "perf": {"mode": None, "known": False, "ts": 0},
    "safety": {
        "min_duty": 20,
        "fuse_temp": 92,
        "allow_low_duty": False,
        "restore_on_exit": True,
        "tick": 1.0,
        "idle_tick": 5.0,
        "hysteresis": 3,
        "write_gap": 2.0,
    },
    "light": {
        "enabled": False,
        "brightness": 70,
        "color": [255, 80, 0],
        "zone": 0,
        "effect": "static",
        "speed": 50,
        "restore_on_resume": True,
        "saved_hex": "#ff5000",
        "prev_color": [0, 0, 0],
    },
    "ui": {
        "theme": "dark",
        "stay_resident": True,
        "open_window_on_start": False,
        "autostart": True,
        # 窗口尺寸：0 = 按屏幕工作区自适应（多机型就靠这个兜底）
        "win_w": 0,
        "win_h": 0,
        # 界面缩放：0 = 自动（按窗口高度挑最合适的档），否则 0.7 ~ 1.2
        "ui_scale": 0,
    },
}


SAFETY_RANGES = {
    "min_duty": (20, 100),
    "fuse_temp": (55, 100),
    "hysteresis": (1, 20),
    "tick": (0.5, 5.0),
    "idle_tick": (1.0, 10.0),
    "write_gap": (0.0, 5.0),
}


def _number(name, value, low, high, integer=False):
    kinds = (int,) if integer else (int, float)
    # 排除 bool、字符串以及 NaN/Infinity；不把越界输入悄悄截断。
    if type(value) not in kinds or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low}..{high} 范围内的{'整数' if integer else '数值'}")


def validate_safety_patch(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """校验完整副本；返回新字典，永不修改调用方或 live 配置。"""
    if not isinstance(current, dict) or not isinstance(patch, dict):
        raise ValueError("安全配置必须是对象")
    candidate = copy.deepcopy(DEFAULT_CONFIG["safety"])
    candidate.update(copy.deepcopy(current))
    candidate.update(copy.deepcopy(patch))
    if candidate.keys() - DEFAULT_CONFIG["safety"].keys():
        raise ValueError("包含未知安全配置项")
    for key, (low, high) in SAFETY_RANGES.items():
        _number(key, candidate[key], low, high, key in ("min_duty", "fuse_temp", "hysteresis"))
    for key in ("allow_low_duty", "restore_on_exit"):
        if type(candidate[key]) is not bool:
            raise ValueError(f"{key} 必须是 bool")
    return candidate


def validate_ui_patch(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(current, dict) or not isinstance(patch, dict):
        raise ValueError("界面配置必须是对象")
    candidate = copy.deepcopy(DEFAULT_CONFIG["ui"])
    candidate.update(copy.deepcopy(current))
    candidate.update(copy.deepcopy(patch))
    if candidate.keys() - DEFAULT_CONFIG["ui"].keys():
        raise ValueError("包含未知界面配置项")
    for key in ("stay_resident", "open_window_on_start", "autostart"):
        if type(candidate[key]) is not bool:
            raise ValueError(f"{key} 必须是 bool")
    for key in ("win_w", "win_h"):
        _number(key, candidate[key], 0, 16384, integer=True)
    scale = candidate["ui_scale"]
    _number("ui_scale", scale, 0, 1.25)
    if scale != 0 and scale < 0.6:
        raise ValueError("ui_scale 必须为 0 或 0.6..1.25")
    if candidate["theme"] not in ("dark", "light"):
        raise ValueError("未知主题")
    return candidate


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(override, dict):
        raise ValueError("配置必须是对象")
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _validate_fans(fans):
    if not isinstance(fans, dict) or not fans:
        raise ValueError("风扇配置必须是非空对象")
    for key, fan in fans.items():
        if key not in ("1", "2", "3", "4") or not isinstance(fan, dict):
            raise ValueError("风扇编号或配置无效")
        if fan.get("mode") not in ("auto", "manual", "curve"):
            raise ValueError("未知风扇模式")
        _number("manual_duty", fan.get("manual_duty"), 0, 100, integer=True)
        _number("temp_index", fan.get("temp_index"), 1, 4, integer=True)
        if fan.get("curve_key") not in ("cpu", "gpu") or not isinstance(fan.get("label"), str):
            raise ValueError("风扇曲线或名称无效")


def _validate_curves(curves):
    if not isinstance(curves, dict):
        raise ValueError("曲线配置必须是对象")
    for key in ("cpu", "gpu"):
        points = curves.get(key)
        if not isinstance(points, list) or not 2 <= len(points) <= 64:
            raise ValueError("曲线必须有 2..64 个点")
        previous = -1
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("曲线点必须是温度与占空比")
            _number("曲线温度", point[0], 30, 100, integer=True)
            _number("曲线占空比", point[1], 0, 100, integer=True)
            if point[0] <= previous:
                raise ValueError("曲线温度必须严格递增")
            previous = point[0]


def _validate_loaded(config):
    for key, default in DEFAULT_CONFIG.items():
        if not isinstance(config[key], type(default)):
            raise ValueError(f"{key} 配置类型无效")
    config["safety"] = validate_safety_patch({}, config["safety"])
    config["ui"] = validate_ui_patch({}, config["ui"])
    _validate_fans(config["fans"])
    _validate_curves(config["curve"])
    light = config["light"]
    for key in ("enabled", "restore_on_resume"):
        if type(light[key]) is not bool:
            raise ValueError(f"light.{key} 必须是 bool")
    for key, low, high in (("brightness", 0, 100), ("speed", 1, 100), ("zone", 0, 3)):
        _number(key, light[key], low, high, integer=True)
    for key in ("color", "prev_color"):
        if not isinstance(light[key], list) or len(light[key]) != 3:
            raise ValueError("灯光颜色必须有三个分量")
        for channel in light[key]:
            _number(key, channel, 0, 255, integer=True)
    if light["effect"] not in ("static", "breath", "rainbow", "cycle", "wave", "heartbeat"):
        raise ValueError("未知灯效")
    hex_color = light["saved_hex"]
    if (not isinstance(hex_color, str) or len(hex_color) != 7 or not hex_color.startswith("#")
            or any(c not in "0123456789abcdefABCDEF" for c in hex_color[1:])):
        raise ValueError("灯光颜色格式无效")
    perf = config["perf"]
    if type(perf["known"]) is not bool:
        raise ValueError("perf.known 必须是 bool")
    if perf["mode"] is not None:
        _number("perf.mode", perf["mode"], 0, 3, integer=True)
    elif perf["known"]:
        raise ValueError("已知性能模式不能为空")
    _number("perf.ts", perf["ts"], 0, 2 ** 53, integer=True)
    if len(config["profiles"]) > 12:
        raise ValueError("方案数量超过上限")
    for item in config["profiles"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("name"), str):
            raise ValueError("方案格式无效")
        _number("profiles.ts", item.get("ts"), 0, 2 ** 53, integer=True)
        _validate_fans(_deep_merge(DEFAULT_CONFIG["fans"], item.get("fans")))
        _validate_curves(_deep_merge(DEFAULT_CONFIG["curve"], item.get("curve")))
    return config


def _replace_in_place(target, source):
    # engine/guard 持有根字典；同时保留已有分区和风扇条目的引用。
    for key in list(target):
        if key not in source:
            del target[key]
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _replace_in_place(target[key], value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key][:] = copy.deepcopy(value)
        else:
            target[key] = copy.deepcopy(value)


class Store:
    def __init__(self, path: str):
        self.path = path
        # 所有配置事务先取 store.lock，需访问硬件时再取 guard.lock。
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return _validate_loaded(_deep_merge(DEFAULT_CONFIG, json.load(fh)))
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            # 坏配置不能留下半套策略：风扇自动、熔断 92°C、最低风量 20%。
            return copy.deepcopy(DEFAULT_CONFIG)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.data)

    def _write(self, data: Dict[str, Any]) -> None:
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=os.path.dirname(os.path.abspath(self.path)),
                prefix="." + os.path.basename(self.path) + ".", suffix=".tmp", delete=False,
            ) as fh:
                tmp = fh.name
                json.dump(data, fh, ensure_ascii=False, indent=2, allow_nan=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass

    def save(self) -> None:
        with self.lock:
            self._write(self.data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """仅提交 ui/safety 分区补丁；校验或落盘失败时 live 保持不变。"""
        if not isinstance(patch, dict) or patch.keys() - {"ui", "safety"}:
            raise ValueError("update 仅接受 ui/safety 配置分区")
        with self.lock:
            candidate = copy.deepcopy(self.data)
            for key, validator in (("ui", validate_ui_patch), ("safety", validate_safety_patch)):
                if key in patch:
                    candidate[key] = validator(candidate[key], patch[key])
            self._write(candidate)
            for key in patch:
                _replace_in_place(self.data[key], candidate[key])
            return copy.deepcopy(candidate)

    def update_safety(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        return self.update({"safety": patch})["safety"]

    def reset(self) -> None:
        with self.lock:
            candidate = copy.deepcopy(DEFAULT_CONFIG)
            self._write(candidate)
            _replace_in_place(self.data, candidate)
