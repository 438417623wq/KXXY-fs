# -*- coding: utf-8 -*-
"""开机自启（登录自启）开关。

为什么要两套办法：
  * 计划任务（首选）：登录即启动、最高权限、不弹 UAC，程序能直接读写 EC。
  * 启动文件夹里的 vbs（兜底）：不需要管理员权限，但每次登录会弹一次 UAC。

为什么用临时 .ps1 文件 + 结果文件，而不是解析 stdout：
    powershell.exe 在非控制台宿主里会把进度记录以 CLIXML 塞进标准输出/错误，
    直接解析会被那一坨 XML 污染，判断成功失败就会出错（实测过一次：
    任务其实已经删掉，却因为读到 CLIXML 报"还在"）。
    所以脚本把结果写进一个临时文件，Python 只读那一个词。

状态查询每次要 1~2 秒（要加载 ScheduledTasks 模块），所以结果缓存几秒 ——
界面是 1 秒一帧，不能每次刷新都去问系统。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from xml.sax.saxutils import escape

TASK_NAME = "EC Pilot"
STARTUP_FILE = "EC Pilot.vbs"
CACHE_TTL = 30.0

FROZEN = bool(getattr(sys, "frozen", False))

_lock = threading.Lock()
_cache = {"ts": 0.0, "value": None}


# ---------------- 基础 ----------------
def exe_path() -> str:
    return os.path.abspath(sys.executable)


def work_dir() -> str:
    return os.path.dirname(exe_path())


def _startup_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def startup_path() -> str:
    return os.path.join(_startup_dir(), STARTUP_FILE)


def _q(text: str) -> str:
    """PowerShell 单引号字符串转义。"""
    return "'" + str(text).replace("'", "''") + "'"


def _trace(msg: str) -> None:
    """写进 app.log，出问题时有据可查（窗口版没有控制台）。"""
    try:
        print(f"  [autostart] {msg}", flush=True)
    except Exception:  # noqa: BLE001
        pass


def _run(body: str, timeout: float = 30.0) -> str:
    """执行一段 PowerShell，返回脚本写出的结果词（失败回 'ERR: ...'）。"""
    tmp = tempfile.mkdtemp(prefix="ecpilot-ps-")
    ps1 = os.path.join(tmp, "run.ps1")
    out = os.path.join(tmp, "result.txt")
    script = (
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$result = 'ERR: 没有结果'\n"
        "try {\n"
        f"{body}\n"
        "} catch {\n"
        "  $result = 'ERR: ' + $_.Exception.Message\n"
        "}\n"
        f"$result | Set-Content -LiteralPath {_q(out)} -Encoding UTF8\n"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    text = "ERR: 未执行"
    try:
        with open(ps1, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write(script)
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", ps1],
            capture_output=True, timeout=timeout, creationflags=flags,
        )
        try:
            with open(out, "r", encoding="utf-8-sig") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
            text = lines[0] if lines else ""
        except OSError:
            text = "ERR: 脚本没有输出"
    except Exception as exc:  # noqa: BLE001
        text = f"ERR: {type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _trace(f"→ {text[:160]}")
    return text


# ---------------- 状态 ----------------
def _task_state() -> str:
    """'Ready'/'Running'/'Disabled'；'' 表示没有任务；'ERR: …' 表示没问出来。"""
    body = (
        f"$t = Get-ScheduledTask -TaskName {_q(TASK_NAME)} -ErrorAction SilentlyContinue\n"
        "if ($t) { $result = [string]$t.State } else { $result = 'NONE' }\n"
    )
    text = _run(body)
    return "" if text == "NONE" else text


def status(force: bool = False) -> dict:
    with _lock:
        now = time.monotonic()
        if not force and _cache["value"] is not None and now - _cache["ts"] < CACHE_TTL:
            return dict(_cache["value"])

    state = _task_state() if FROZEN else ""
    startup = os.path.isfile(startup_path())
    task_ok = bool(state) and not state.startswith("ERR")
    if task_ok:
        method = "task"
    elif startup:
        method = "startup"
    else:
        method = ""
    value = {
        "supported": FROZEN,
        "enabled": bool(method) and state != "Disabled",
        "method": method,
        "task_state": state if task_ok else "",
        "startup_file": startup,
        "target": exe_path(),
        "reason": "" if FROZEN else "用 Python 源码方式运行时无法设置开机自启，请用安装版 ECPilot.exe",
    }
    with _lock:
        _cache["ts"] = time.monotonic()
        _cache["value"] = dict(value)
    return value


def _invalidate() -> None:
    with _lock:
        _cache["value"] = None
        _cache["ts"] = 0.0


# ---------------- 开 / 关 ----------------
def _write_vbs() -> bool:
    """兜底：启动文件夹里放一个 vbs，登录时静默拉起（但不带管理员权限）。"""
    path = startup_path()
    body = (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.Run """{exe_path()}"" --autostart", 0, False\r\n'
    )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="mbcs", newline="") as fh:
            fh.write(body)
        return True
    except Exception:  # noqa: BLE001
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(body)
            return True
        except Exception:  # noqa: BLE001
            return False


def _task_xml() -> str:
    """纯生成任务定义；UserId 在注册脚本内填为当前交互用户。"""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        '<RegistrationInfo><Description>EC Pilot fan control</Description></RegistrationInfo>'
        '<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>USER</UserId></LogonTrigger></Triggers>'
        '<Principals><Principal id="Author"><UserId>USER</UserId>'
        '<LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel>'
        '</Principal></Principals>'
        '<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
        '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
        '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
        '<Enabled>true</Enabled><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>'
        '<Actions Context="Author"><Exec>'
        f'<Command>{escape(exe_path())}</Command><Arguments>--autostart</Arguments>'
        f'<WorkingDirectory>{escape(work_dir())}</WorkingDirectory>'
        '</Exec></Actions></Task>'
    )


def enable() -> dict:
    if not FROZEN:
        return {"ok": False, "reason": "请使用安装版 ECPilot.exe 设置开机自启"}

    body = (
        f"$xml = [xml]{_q(_task_xml())}\n"
        "$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name\n"
        "$xml.Task.Principals.Principal.UserId = $user\n"
        "$xml.Task.Triggers.LogonTrigger.UserId = $user\n"
        f"Register-ScheduledTask -TaskName {_q(TASK_NAME)} -Xml $xml.OuterXml -Force | Out-Null\n"
        f"$t = Get-ScheduledTask -TaskName {_q(TASK_NAME)} -ErrorAction SilentlyContinue\n"
        "$result = if ($t) { 'TASK:' + $t.State } else { 'NONE' }\n"
    )
    text = _run(body)
    _invalidate()
    if text.startswith("TASK:"):
        return {"ok": True, "method": "task",
                "detail": "已加入计划任务（登录后自动以最高权限启动，不弹 UAC）"}

    # 计划任务建不了（多半是没管理员权限）→ 退到启动文件夹
    if _write_vbs():
        _invalidate()
        return {
            "ok": True,
            "method": "startup",
            "detail": "已放入启动文件夹，但登录时会弹一次 UAC（想要免弹窗：右键 安装.bat 以管理员身份运行一次）",
        }
    return {"ok": False, "reason": (text or "设置失败")[:200]}


def disable() -> dict:
    if not FROZEN:
        return {"ok": False, "reason": "请使用安装版 ECPilot.exe 设置开机自启"}

    body = (
        f"Unregister-ScheduledTask -TaskName {_q(TASK_NAME)} -Confirm:$false -ErrorAction SilentlyContinue\n"
        f"$t = Get-ScheduledTask -TaskName {_q(TASK_NAME)} -ErrorAction SilentlyContinue\n"
        "$result = if ($t) { 'STILL:' + $t.State } else { 'GONE' }\n"
    )
    text = _run(body)
    removed = []
    for path in (startup_path(), os.path.join(_startup_dir(), "EC Pilot.lnk")):
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(os.path.basename(path))
        except Exception:  # noqa: BLE001
            pass
    _invalidate()
    if text.startswith("STILL"):
        return {"ok": False, "reason": "计划任务还在（%s），可能需要管理员权限" % text[6:]}
    if text.startswith("ERR"):
        return {"ok": False, "reason": text[5:] or "取消失败"}
    return {"ok": True,
            "detail": "已取消开机自启" + ("（" + "、".join(removed) + " 已删除）" if removed else "")}
