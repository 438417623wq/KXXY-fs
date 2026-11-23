# -*- coding: utf-8 -*-
"""自定义方案：把「现在这套风扇设置」存下来，以后一键套用。

一个方案里存三样东西：
  · 两个风扇的模式（auto / curve / manual）
  · 固定转速时的风量
  · CPU / GPU 两条温度曲线

为什么不复用 curve.PRESETS：那四个是内置场景（安静/均衡/性能/全速），
内置的可以随版本改进；这里存的是用户自己调出来的，必须原样保留，
而且允许重名覆盖、改名、删除。所以单独存在 config.profiles 里。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

MAX_PROFILES = 12
MAX_NAME = 16


def _store_list(store) -> List[Dict[str, Any]]:
    data = store.data
    items = data.setdefault("profiles", [])
    if not isinstance(items, list):
        items = []
        data["profiles"] = items
    return items


def _clean_name(name: str) -> str:
    name = (name or "").strip().replace("\n", " ")[:MAX_NAME]
    return name or "未命名方案"


def _fan_state(store) -> Dict[str, Dict[str, Any]]:
    out = {}
    for key in ("1", "2"):
        fan = store.data.get("fans", {}).get(key, {})
        out[key] = {
            "mode": str(fan.get("mode", "auto")),
            "manual_duty": int(fan.get("manual_duty", 50)),
        }
    return out


def _curve_state(store) -> Dict[str, List[List[int]]]:
    curve = store.data.get("curve", {})
    return {
        "cpu": [[int(p[0]), int(p[1])] for p in curve.get("cpu", [])],
        "gpu": [[int(p[0]), int(p[1])] for p in curve.get("gpu", [])],
    }


def snapshot(store) -> Dict[str, Any]:
    """当前设置的快照（前端存方案时不需要自己拼）。"""
    return {"fans": _fan_state(store), "curve": _curve_state(store)}


def list_all(store) -> List[Dict[str, Any]]:
    """给界面用：只回展示需要的字段，按创建时间正序。"""
    out = []
    for item in _store_list(store):
        out.append({
            "id": item.get("id", ""),
            "name": item.get("name", "未命名方案"),
            "ts": int(item.get("ts", 0)),
            "fans": item.get("fans", {}),
            "curve": item.get("curve", {}),
        })
    return out


def get(store, pid: str) -> Optional[Dict[str, Any]]:
    for item in _store_list(store):
        if item.get("id") == pid:
            return item
    return None


def save(store, name: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """保存当前设置为一个方案；同名则覆盖（这样"另存"和"更新"是一个动作）。"""
    items = _store_list(store)
    name = _clean_name(name)
    snap = snapshot(store)
    if isinstance(payload, dict):
        if isinstance(payload.get("fans"), dict):
            for key in ("1", "2"):
                src = payload["fans"].get(key) or {}
                if src:
                    snap["fans"][key] = {
                        "mode": str(src.get("mode", snap["fans"][key]["mode"])),
                        "manual_duty": int(src.get("manual_duty", snap["fans"][key]["manual_duty"])),
                    }
        if isinstance(payload.get("curve"), dict):
            for key in ("cpu", "gpu"):
                pts = payload["curve"].get(key)
                if isinstance(pts, list) and len(pts) >= 2:
                    snap["curve"][key] = [[int(p[0]), int(p[1])] for p in pts]

    existing = next((x for x in items if x.get("name") == name), None)
    if existing is not None:
        existing.update({"fans": snap["fans"], "curve": snap["curve"], "ts": int(time.time())})
        store.save()
        return {"ok": True, "replaced": True, "id": existing.get("id"), "profiles": list_all(store)}

    if len(items) >= MAX_PROFILES:
        return {"ok": False, "reason": f"最多存 {MAX_PROFILES} 个方案，先删掉一个再存"}

    item = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "ts": int(time.time()),
        "fans": snap["fans"],
        "curve": snap["curve"],
    }
    items.append(item)
    store.save()
    return {"ok": True, "replaced": False, "id": item["id"], "profiles": list_all(store)}


def apply(store, engine, pid: str) -> Dict[str, Any]:
    """套用方案：先写曲线再切模式，避免切换瞬间用旧曲线。"""
    item = get(store, pid)
    if item is None:
        return {"ok": False, "reason": "方案不存在（可能已被删除）"}

    curve = item.get("curve") or {}
    for key in ("cpu", "gpu"):
        pts = curve.get(key)
        if isinstance(pts, list) and len(pts) >= 2:
            store.data["curve"][key] = [[int(p[0]), int(p[1])] for p in pts]
    store.save()

    fans = item.get("fans") or {}
    for key in ("1", "2"):
        cfg = fans.get(key) or {}
        mode = str(cfg.get("mode", "auto"))
        duty = cfg.get("manual_duty")
        engine.set_mode(int(key), mode, int(duty) if duty is not None else None)
    return {"ok": True, "name": item.get("name", "")}


def rename(store, pid: str, name: str) -> Dict[str, Any]:
    item = get(store, pid)
    if item is None:
        return {"ok": False, "reason": "方案不存在"}
    name = _clean_name(name)
    if any(x is not item and x.get("name") == name for x in _store_list(store)):
        return {"ok": False, "reason": "已经有同名方案了"}
    item["name"] = name
    store.save()
    return {"ok": True, "profiles": list_all(store)}


def delete(store, pid: str) -> Dict[str, Any]:
    items = _store_list(store)
    for i, item in enumerate(items):
        if item.get("id") == pid:
            items.pop(i)
            store.save()
            return {"ok": True, "profiles": list_all(store)}
    return {"ok": False, "reason": "方案不存在"}
