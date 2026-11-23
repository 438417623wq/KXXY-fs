# -*- coding: utf-8 -*-
"""冲突检测及原生界面的关闭动作；关闭目标只能来自内部实时扫描。"""
from __future__ import annotations

import csv
import ctypes
import io
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

#: 只列会主动控制 EC 的程序，普通监控工具不算。
KNOWN = (
    "brzclevofancontrol", "controlcenter", "clevocontrolcenter", "clevo control center",
    "fancontrol", "notebookfancontrol", "nbfc", "tongfangcontrolcenter", "gamingcenter",
)
LED_KNOWN = (
    "colorfuliedkeyboard", "colorfulledkeyboard", "clevorgbcontrol",
    "obsidian", "led manager", "keyboardled",
)

_cache: Dict[str, Any] = {"ts": None, "items": []}
_TTL = 8.0
_cache_lock = threading.RLock()
_process_allowlist: Dict[int, str] = {}
_service_allowlist: Dict[str, tuple] = {}


def _list_processes() -> List[Dict[str, Any]]:
    """一次 tasklist 同时取进程及服务名；查询失败时不授权任何关闭目标。"""
    try:
        result = subprocess.run(
            ["tasklist", "/SVC", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return []
        text = result.stdout.decode("mbcs" if os.name == "nt" else "utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return []
    items = []
    me = os.getpid()
    keys = KNOWN + LED_KNOWN
    for parts in csv.reader(io.StringIO(text)):
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if pid <= 0 or pid == me:
            continue
        known_process = any(k in name.lower() for k in keys)
        services = parts[2].split(",") if len(parts) == 3 else []
        services = [s.strip() for s in services if s.strip() and s.strip().upper() not in ("N/A", "暂缺")]
        matched = [s for s in services if known_process or any(k in s.lower() for k in keys)]
        if matched:
            items.extend({"name": name, "pid": pid, "service": service} for service in matched)
        elif known_process:
            items.append({"name": name, "pid": pid})
    return items


def query(force: bool = False) -> List[Dict[str, Any]]:
    """共享 TTL 快照；返回副本，不把内部关闭 allowlist 暴露给调用方。"""
    with _cache_lock:
        now = time.monotonic()
        if force or _cache["ts"] is None or now - _cache["ts"] >= _TTL:
            items = _list_processes()
            _process_allowlist.clear()
            _service_allowlist.clear()
            for item in items:
                if item.get("service"):
                    name = item["service"]
                    _service_allowlist[name.casefold()] = (name, item["pid"])
                else:
                    _process_allowlist[item["pid"]] = item["name"]
            _cache["items"] = [dict(item) for item in items]
            _cache["ts"] = time.monotonic()
        return [dict(item) for item in _cache["items"]]


def detect(force: bool = False) -> List[Dict[str, Any]]:
    return query(force)


def close(pid: int = 0, service: str = "") -> Dict[str, Any]:
    """原生 UI 保留 close(pid)；服务也可显式传 service，名称必须由内部扫描确认。"""
    if os.name != "nt":
        return {"ok": False, "reason": "仅支持 Windows"}
    if type(pid) is not int or pid < 0 or not isinstance(service, str) or (not pid and not service):
        return {"ok": False, "reason": "关闭目标无效"}
    if pid == os.getpid():
        return {"ok": False, "reason": "拒绝结束自己"}
    with _cache_lock:
        query(force=True)
        target = None
        if service:
            target = _service_allowlist.get(service.casefold())
            if target is None or (pid and target[1] != pid):
                return {"ok": False, "reason": "服务不在内部扫描的关闭白名单中"}
        else:
            matches = [record for record in _service_allowlist.values() if record[1] == pid]
            if len(matches) == 1:
                target = matches[0]
            elif matches:
                return {"ok": False, "reason": "该进程包含多个冲突服务，请指定已扫描的服务名"}
            elif pid not in _process_allowlist:
                return {"ok": False, "reason": "进程不在内部扫描的关闭白名单中"}

    try:
        if target is not None:
            # 不拼接 PowerShell/cmd 脚本，也不把调用方字符串直接当命令参数。
            result = subprocess.run(
                ["sc.exe", "stop", target[0]], shell=False,
                capture_output=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {"ok": result.returncode == 0,
                    "reason": "" if result.returncode == 0 else "停止服务失败（可能需要管理员权限）"}
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x0001, False, pid)
        if not handle:
            return {"ok": False, "reason": "打开进程失败（可能需要管理员权限）"}
        try:
            if not k32.TerminateProcess(handle, 1):
                return {"ok": False, "reason": "结束进程失败"}
            return {"ok": True}
        finally:
            k32.CloseHandle(handle)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"关闭失败：{type(exc).__name__}: {exc}"}
    finally:
        with _cache_lock:
            _cache["ts"] = None
