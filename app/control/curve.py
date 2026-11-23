# -*- coding: utf-8 -*-
"""风扇曲线：分段线性插值 + 平滑。

曲线点的温度必须是递增的，占空比 0-100。
固件安全兜底永远在插值之外：温度超过熔断阈值时由 engine 强制 100%。
"""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

Point = Tuple[int, int]

# 出厂风格的默认曲线（参考原厂 EC 的策略：低温低噪，高温线性拉满）
DEFAULT_CURVES: dict = {
    "cpu": [(40, 25), (50, 32), (60, 42), (70, 55), (78, 70), (85, 88), (92, 100)],
    "gpu": [(40, 25), (50, 32), (60, 42), (70, 55), (78, 70), (85, 88), (92, 100)],
}

PRESETS: dict = {
    "quiet": {
        "label": "静音",
        "desc": "40-70 度几乎不吵，代价是重载时温度偏高",
        "cpu": [(40, 0), (50, 0), (55, 22), (65, 30), (75, 42), (82, 62), (88, 100)],
        "gpu": [(40, 0), (50, 0), (55, 22), (65, 30), (75, 42), (82, 62), (88, 100)],
    },
    "balanced": {
        "label": "均衡",
        "desc": "默认推荐，兼顾噪音与温度",
        "cpu": DEFAULT_CURVES["cpu"],
        "gpu": DEFAULT_CURVES["gpu"],
    },
    "performance": {
        "label": "性能",
        "desc": "更早介入，适合游戏/编译",
        "cpu": [(40, 30), (48, 40), (55, 52), (65, 65), (72, 78), (80, 92), (88, 100)],
        "gpu": [(40, 30), (48, 40), (55, 52), (65, 65), (72, 78), (80, 92), (88, 100)],
    },
    "max": {
        "label": "全速",
        "desc": "恒定满转，散热最优、噪音最大",
        "cpu": [(40, 100), (92, 100)],
        "gpu": [(40, 100), (92, 100)],
    },
}


def normalize(points: Iterable[Sequence[int]]) -> List[Point]:
    cleaned = []
    for p in points:
        t = max(30, min(100, int(p[0])))
        d = max(0, min(100, int(p[1])))
        cleaned.append((t, d))
    cleaned.sort(key=lambda x: x[0])
    # 温度重复的只保留最后一个
    out: List[Point] = []
    for t, d in cleaned:
        if out and out[-1][0] == t:
            out[-1] = (t, d)
        else:
            out.append((t, d))
    if len(out) < 2:
        out = [(40, out[0][1] if out else 30), (90, out[-1][1] if out else 100)]
    return out


def interpolate(points: Iterable[Sequence[int]], temp: float) -> int:
    pts = normalize(points)
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, d0), (t1, d1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            if t1 == t0:
                return d1
            ratio = (temp - t0) / (t1 - t0)
            return int(round(d0 + (d1 - d0) * ratio))
    return pts[-1][1]


def smooth(prev: float, target: float, alpha: float = 0.45) -> float:
    """一阶低通，避免转速忽高忽低（风扇啸叫的主要原因）。"""
    return prev + (target - prev) * alpha
