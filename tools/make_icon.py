# -*- coding: utf-8 -*-
"""生成 EC Pilot 的 exe 图标（纯标准库：手写 PNG + ICO，不依赖 Pillow）。

图形：深色圆角底 + 青蓝色四叶风扇 + 中心轴环。
"""
from __future__ import annotations

import math
import os
import struct
import sys
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "ecpilot.ico")

BG = (13, 20, 31)
BG_EDGE = (24, 40, 60)
CYAN = (56, 225, 255)
CYAN_DIM = (22, 122, 158)
WHITE = (236, 249, 255)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _fall(x: float, edge0: float, edge1: float) -> float:
    """x <= edge0 → 1；x >= edge1 → 0（“越靠近越亮”的形状用它）。"""
    if edge1 <= edge0:
        return 1.0 if x < edge0 else 0.0
    return 1.0 - _smoothstep(edge0, edge1, x)


def _in_ellipse(u, v, cx, cy, a, b, deg):
    ca, sa = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    dx, dy = u - cx, v - cy
    p = dx * ca + dy * sa
    q = -dx * sa + dy * ca
    return (p / a) ** 2 + (q / b) ** 2


def _rounded_rect(u, v, r=0.235):
    """返回 1 表示完全在内，0 表示在外（做软边用）。"""
    x = min(u, 1 - u)
    y = min(v, 1 - v)
    if x >= r or y >= r:
        return 1.0 if (x > 0 and y > 0) else 0.0
    d = math.hypot(r - x, r - y)
    return _fall(d, r - 0.016, r)


def shade(u: float, v: float):
    """u,v ∈ [0,1] → (r,g,b,a)。"""
    alpha = _rounded_rect(u, v)
    if alpha <= 0.002:
        return (0, 0, 0, 0)

    # 底：中心略亮的径向渐变
    d = math.hypot(u - 0.5, v - 0.42)
    k = _fall(d, 0.0, 0.78)
    r0 = BG[0] + (BG_EDGE[0] - BG[0]) * k
    g0 = BG[1] + (BG_EDGE[1] - BG[1]) * k
    b0 = BG[2] + (BG_EDGE[2] - BG[2]) * k

    rad = math.hypot(u - 0.5, v - 0.5)

    # 外圈细环
    ring = _fall(abs(rad - 0.408), 0.0, 0.006)

    # 螺旋四叶：极坐标下按半径扭转，叶根窄、叶中宽、叶尖收成尖
    blade = 0.0
    theta = math.atan2(v - 0.5, u - 0.5)
    R0, R1 = 0.096, 0.356
    if R0 <= rad <= R1:
        s = (rad - R0) / (R1 - R0)
        half = 0.545 * math.sin(math.pi * (s ** 0.66))
        for i in range(4):
            centre = i * (math.pi / 2) + 1.05 * (rad - R0) - 0.62
            dd = (theta - centre + math.pi) % (2 * math.pi) - math.pi
            blade = max(blade, _fall(abs(dd), half * 0.72, half))

    # 中心轴：亮环 + 深色内芯
    hub_out = _fall(rad, 0.080, 0.098)
    hub_in = _fall(rad, 0.052, 0.068)

    r, g, b = r0, g0, b0

    if ring > 0:
        r += (CYAN_DIM[0] - r) * ring * 0.8
        g += (CYAN_DIM[1] - g) * ring * 0.8
        b += (CYAN_DIM[2] - b) * ring * 0.8

    if blade > 0:
        t = _fall(rad, 0.094, 0.34)        # 叶根亮、叶尖深，做出层次
        cr = CYAN_DIM[0] + (CYAN[0] - CYAN_DIM[0]) * t
        cg = CYAN_DIM[1] + (CYAN[1] - CYAN_DIM[1]) * t
        cb = CYAN_DIM[2] + (CYAN[2] - CYAN_DIM[2]) * t
        r += (cr - r) * blade
        g += (cg - g) * blade
        b += (cb - b) * blade

    if hub_out > 0:
        r += (WHITE[0] - r) * hub_out
        g += (WHITE[1] - g) * hub_out
        b += (WHITE[2] - b) * hub_out
    if hub_in > 0:
        r += (BG[0] - r) * hub_in
        g += (BG[1] - g) * hub_in
        b += (BG[2] - b) * hub_in

    return (int(max(0, min(255, r))), int(max(0, min(255, g))), int(max(0, min(255, b))),
            int(max(0, min(255, alpha * 255))))


def render(size: int, ss: int = 4) -> bytes:
    """渲染 size×size 的 RGBA 原始像素（ss×ss 超采样抗锯齿）。"""
    buf = bytearray()
    inv = 1.0 / (size * ss)
    for y in range(size):
        for x in range(size):
            ar = ag = ab = aa = 0
            for sy in range(ss):
                for sx in range(ss):
                    u = (x * ss + sx + 0.5) * inv
                    v = (y * ss + sy + 0.5) * inv
                    cr, cg, cb, ca = shade(u, v)
                    ar += cr * ca
                    ag += cg * ca
                    ab += cb * ca
                    aa += ca
            if aa == 0:
                buf += b"\x00\x00\x00\x00"
            else:
                buf += bytes((ar // aa, ag // aa, ab // aa, aa // (ss * ss)))
    return bytes(buf)


def png(size: int, ss: int = 4) -> bytes:
    raw = bytearray()
    px = render(size, ss)
    stride = size * 4
    for y in range(size):
        raw.append(0)
        raw += px[y * stride:(y + 1) * stride]

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def build(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    images = []
    for s in sizes:
        ss = 6 if s <= 32 else (4 if s <= 64 else 2)
        images.append((s, png(s, ss)))
        print(f"  {s}x{s} ok")
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    data = b""
    for s, blob in images:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        data += blob
        offset += len(blob)
    return header + entries + data


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    print("生成图标…")
    blob = build()
    with open(OUT, "wb") as fh:
        fh.write(blob)
    print(f"完成：{OUT}（{len(blob)} 字节）")
    sys.exit(0)
