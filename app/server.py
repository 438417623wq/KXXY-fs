# -*- coding: utf-8 -*-
"""本机 HTTP 接口：原生唤窗、受鉴权的状态与配置。

敏感操作只允许原生内部调用；HTTP 不提供硬件写入或系统管理入口。
"""
from __future__ import annotations

import copy
import json
import os
import secrets
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from store import validate_safety_patch, validate_ui_patch

from control import autostart
from control import curve as curve_mod
from control import perfmode
from control import profiles
from hardware import conflicts
from version import VERSION
from window import screen_work_area


def _is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class AppContext:
    def __init__(self, root: str, store, backend, guard, engine, light, runner=None, base_dir=None):
        self.root = root
        self.store = store
        self.backend = backend
        self.guard = guard
        self.engine = engine
        self.light = light
        # 性能模式复用灯控那条 64 位 DCHU 通道：两者都是同一条 Insyde 驱动接口，
        # 再单独起一个宿主进程只会白吃几十兆内存。
        # guard.lock 让 0x79 和风扇的 0x99 不会插进同一个 EC 事务；
        # on_applied 让切档后立刻重申风扇占空比（固件会重排风扇表）。
        self.perf = perfmode.PerfMode(
            getattr(light, "bridge", None), store, log=getattr(light, "log", None),
            guard=guard, on_applied=engine.reassert)
        self.runner = runner
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.port = 0          # serve() 起来后填真实端口
        self.want_exit = False
        # 仅驻留内存，不进入 Store、状态快照或 ping；CLI quit 使用独立本机事件。
        self.api_token = secrets.token_urlsafe(32)
        # 回调由 Runner/原生窗口注入，负责投递到主线程，HTTP 线程不执行收尾。
        self.on_show = None
        self.on_exit = None
        self.on_quick_scene = None
        self.exe_path = sys.executable
        self.auto_state = {}
        # 在线提示由原生窗口与兼容 SSE 各自的生命周期维护
        self.client_lock = threading.Lock()
        self.clients = 0
        self.ever_connected = False
        self.last_client_ts = time.time()
        # 开机自启的写入要跟任务计划服务打交道（1-3 秒），放后台线程做，界面不卡
        self.auto_lock = threading.Lock()
        self.auto_job = None

    # ---------- 聚合状态 ----------
    def snapshot(self) -> Dict[str, Any]:
        with self.store.lock:
            cfg = self.store.snapshot()
            profile_view = copy.deepcopy(profiles.list_all(self.store))
            with self.guard.lock:
                guard_view = copy.deepcopy({
                    "unclean": self.guard.unclean,
                    "session_started": self.guard.session_started,
                    "pending": len(self.guard.journal.pending()),
                    "fan_mode": {str(k): v for k, v in self.guard.fan_mode.items()},
                    "ram_touched": {f"0x{k:02X}": v for k, v in self.guard.ram_touched.items()},
                    "entries": self.guard.journal.recent(40),
                })
        snap = self.engine.snapshot()
        fans = {}
        for key, fan in cfg["fans"].items():
            no = int(key)
            fans[key] = {
                "no": no,
                "label": fan.get("label", f"风扇{no}"),
                "mode": fan.get("mode", "auto"),
                "manual_duty": fan.get("manual_duty", 50),
                "curve_key": fan.get("curve_key", "cpu" if no == 1 else "gpu"),
                "temp": snap["temps"].get(no, -1),
                "rpm": snap["rpms"].get(no, -1),
                "duty": snap["duty"].get(no, -1),
            }
        return {
            "backend": {
                "kind": self.backend.kind,
                "label": self.backend.label,
                "info": self.backend.info,
                "is_sim": self.backend.kind == "sim",
            },
            "fans": fans,
            "curve": cfg["curve"],
            "safety": cfg["safety"],
            "light": self.light.status(),
            "perf": self.perf.status(),
            "guard": guard_view,
            "state": {
                "cpu_load": snap["cpu_load"],
                "mem_load": snap["mem_load"],
                "mem_used": snap["mem_used"],
                "mem_total": snap["mem_total"],
                "fuse": snap["fuse"],
                "degraded": snap["degraded"],
                "sensor_ok": snap.get("sensor_ok", True),
                "sensor_note": snap.get("sensor_note", ""),
                "stalled": snap.get("stalled", False),
                "alerts": snap["alerts"],
                "history": snap["history"],
            },
            "presets": {
                k: {"label": v["label"], "desc": v["desc"], "cpu": v["cpu"], "gpu": v["gpu"]}
                for k, v in curve_mod.PRESETS.items()
            },
            "profiles": profile_view,
            "settings": self.settings(),
            "conflicts": conflicts.detect(),
            "ts": int(time.time()),
        }

    # ---------- 设置 ----------
    def settings(self) -> Dict[str, Any]:
        """只读配置快照，不含开机自启的真实状态。"""
        cfg = self.store.snapshot()
        ui = cfg["ui"]
        area = screen_work_area()
        return {
            "stay_resident": ui["stay_resident"],
            "open_window_on_start": ui["open_window_on_start"],
            "restore_on_exit": cfg["safety"]["restore_on_exit"],
            "autostart": ui["autostart"],
            "win_w": ui["win_w"],
            "win_h": ui["win_h"],
            "ui_scale": ui["ui_scale"],
            "screen": {"w": area[2], "h": area[3]} if area else None,
            "version": VERSION,
        }

    def apply_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"stay_resident", "open_window_on_start", "restore_on_exit",
                   "autostart", "win_w", "win_h", "ui_scale"}
        if not isinstance(patch, dict) or patch.keys() - allowed:
            raise ValueError("设置必须是对象且不能包含未知项")
        notes = []
        worker = None
        with self.store.lock:
            ui = validate_ui_patch(self.store.data["ui"],
                                   {k: v for k, v in patch.items() if k != "restore_on_exit"})
            safety = validate_safety_patch(self.store.data["safety"],
                                          {k: v for k, v in patch.items() if k == "restore_on_exit"})
            with self.auto_lock:
                if "autostart" in patch:
                    if not autostart.FROZEN:
                        return {"ok": False, "reason": "请使用安装版 ECPilot.exe 设置开机自启"}
                    if self.auto_job and not self.auto_job.get("done"):
                        return {"ok": False, "reason": "上一次设置还在处理中，请稍后再试"}
                # 完整校验且成功落盘后才原位提交；失败不会改动任何 live 分区。
                self.store.update({"ui": ui, "safety": safety})
                if "autostart" in patch:
                    want = ui["autostart"]
                    self.auto_job = {"want": want, "done": False, "started": time.time(), "result": None}
                    worker = threading.Thread(target=self._autostart_worker, args=(want,),
                                              name="autostart", daemon=True)
        if "stay_resident" in patch:
            notes.append("已改为后台常驻：关窗口不会退出。" if ui["stay_resident"] else
                         "下次关窗口时会还原风扇并退出。")
        if "win_w" in patch or "win_h" in patch:
            notes.append("窗口大小已记下，请在原生窗口应用。")
        if worker is not None:
            worker.start()
            notes.append("正在写入开机自启设置…")
        return {"ok": True, "settings": self.settings(), "pending": worker is not None,
                "autostart": self.auto_view(), "notes": notes}

    def reopen_window(self) -> Dict[str, Any]:
        if not callable(self.on_show):
            return {"ok": False, "reason": "当前没有原生窗口"}
        self.on_show()
        return {"ok": True}

    # ---------- 开机自启：仅供原生界面内部调用 ----------
    def start_autostart(self, want: bool) -> Dict[str, Any]:
        return self.apply_settings({"autostart": want})

    def _autostart_worker(self, want: bool) -> None:
        try:
            res = autostart.enable() if want else autostart.disable()
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if res.get("ok"):
            try:
                with self.store.lock:
                    self.store.update({"ui": {"autostart": want}})
            except Exception:  # noqa: BLE001
                pass
        autostart.status(force=True)      # 顺手刷新缓存，界面下一轮就是真状态
        with self.auto_lock:
            if self.auto_job:
                self.auto_job["done"] = True
                self.auto_job["result"] = res

    def auto_view(self) -> Dict[str, Any]:
        st = autostart.status()
        with self.auto_lock:
            job = dict(self.auto_job) if self.auto_job else None
        if job and not job["done"]:
            st = dict(st)
            st["pending"] = True
            st["enabled"] = bool(job["want"])
            st["method"] = st.get("method") or "task"
            return st
        if job and job.get("result"):
            st = dict(st)
            st["last"] = job["result"]
        self.auto_state = st
        return st

    def query_autostart(self) -> None:
        """原生界面用：问一次系统里的自启状态（可能要 1-2 秒，放后台）。"""
        def _job():
            try:
                self.auto_view()
            except Exception:
                pass

        threading.Thread(target=_job, name="autostart-view", daemon=True).start()


class APIError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status


class _RequestReader:
    """请求行和请求头共用大小预算及绝对截止时间，避免慢速滴流占住线程。"""
    def __init__(self, handler, stream):
        self.handler = handler
        self.stream = stream
        self.remaining = handler.MAX_HEADER_BYTES

    def readline(self, limit=-1):
        line = bytearray()
        while limit < 0 or len(line) < limit:
            timeout = self.handler._read_deadline - time.monotonic()
            if timeout <= 0:
                raise APIError(408, "读取请求超时")
            self.handler.connection.settimeout(timeout)
            try:
                char = self.stream.read(1)
            except TimeoutError:
                raise APIError(408, "读取请求超时") from None
            if time.monotonic() >= self.handler._read_deadline:
                raise APIError(408, "读取请求超时")
            if not char:
                break
            self.remaining -= len(char)
            if self.remaining < 0:
                raise APIError(431, "请求头过大")
            line.extend(char)
            if char == b"\n":
                break
        return bytes(line)

    def read1(self, size=-1):
        return self.stream.read1(size)


class Handler(BaseHTTPRequestHandler):
    server_version = "ECPilot/1.0"
    protocol_version = "HTTP/1.1"
    ctx: AppContext = None  # type: ignore
    REQUEST_TIMEOUT = 3.0
    MAX_BODY_BYTES = 64 * 1024
    MAX_HEADER_BYTES = 16 * 1024
    GET_PATHS = frozenset({"/api/ping", "/api/state", "/api/stream", "/api/journal",
                           "/api/settings", "/api/driver/status"})
    POST_PATHS = frozenset({"/api/show", "/api/shutdown", "/api/safety", "/api/settings",
                            "/api/profiles/snapshot", "/api/journal/clear"})
    # 不提供 HTTP 开启开关；即使持有 token，也不能访问这些原生专用操作。
    DISABLED_PATHS = frozenset({
        "/api/ecmap", "/api/diag", "/api/sensor/probe",
        "/api/fan/mode", "/api/fan/duty", "/api/curve", "/api/curve/preset",
        "/api/profiles/save", "/api/profiles/apply", "/api/profiles/rename", "/api/profiles/delete",
        "/api/restore", "/api/perf/mode", "/api/kbled/update", "/api/kbled/preset",
        "/api/kbled/trial", "/api/kbled/keep", "/api/kbled/revert", "/api/kbled/restore",
        "/api/kbled/owner/stop", "/api/light/dump", "/api/light/diff", "/api/light/commit",
        "/api/driver/install", "/api/elevate", "/api/conflict/close", "/api/open", "/api/rewindow",
    })

    # ---------------- 基础 ----------------
    def log_message(self, fmt, *args):  # 静音默认访问日志
        return

    def setup(self):
        self.request.settimeout(self.REQUEST_TIMEOUT)
        super().setup()

    def handle_one_request(self):
        self.connection.settimeout(self.REQUEST_TIMEOUT)
        self._read_deadline = time.monotonic() + self.REQUEST_TIMEOUT
        self.requestline, self.request_version, self.command = "", "HTTP/1.1", None
        stream = self.rfile
        self.rfile = _RequestReader(self, stream)
        try:
            super().handle_one_request()
        except APIError as exc:
            self._send_json({"ok": False, "reason": str(exc)}, exc.status)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            self.close_connection = True
        finally:
            self.rfile = stream

    def parse_request(self):
        if not super().parse_request():
            return False
        target = self.raw_requestline.split()[1].decode("iso-8859-1")
        if not target.startswith("/") or target.startswith("//") or target != self.path:
            self._send_json({"ok": False, "reason": "未知接口"}, 404)
            return False
        return True

    def handle_expect_100(self):
        self._send_json({"ok": False, "reason": "不支持 Expect"}, 417)
        return False

    def _send_json(self, payload: Dict[str, Any], code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        # 每个 JSON 请求独立关闭，拒绝未读请求体或流水线造成的边界混淆。
        self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorize(self, method: str) -> str:
        hosts = self.headers.get_all("Host", [])
        port = self.ctx.port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost"})
        if (self.client_address[0] not in ("127.0.0.1", "::1") or len(hosts) != 1
                or hosts[0].lower() not in allowed_hosts):
            raise APIError(403, "仅允许本机 Host")
        if self.headers.get_all("Origin") is not None:
            raise APIError(403, "不允许浏览器跨域请求")
        path = self.path
        if path not in self.GET_PATHS | self.POST_PATHS | self.DISABLED_PATHS:
            raise APIError(404, "未知接口")
        public = (method, path) in (("GET", "/api/ping"), ("POST", "/api/show"))
        if not public:
            auth = self.headers.get_all("Authorization", [])
            token = getattr(self.ctx, "api_token", "")
            value = auth[0] if len(auth) == 1 else ""
            scheme, _, supplied = value.partition(" ")
            if (not token or scheme.lower() != "bearer"
                    or not secrets.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))):
                raise APIError(401, "需要有效的 Bearer token")
        if path in self.DISABLED_PATHS:
            raise APIError(403, "此操作仅供原生界面内部调用")
        allowed = self.GET_PATHS if method == "GET" else self.POST_PATHS
        if path not in allowed:
            raise APIError(405, "此接口不支持该方法")
        return path

    def _content_length(self, required=False) -> int:
        if self.headers.get_all("Transfer-Encoding") is not None:
            raise APIError(400, "不支持 Transfer-Encoding")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            if required:
                raise APIError(411, "需要 Content-Length")
            return 0
        if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                or len(lengths[0]) > 10):
            raise APIError(400, "Content-Length 无效")
        length = int(lengths[0])
        if length > self.MAX_BODY_BYTES:
            raise APIError(413, "请求体过大")
        return length

    def _read_json(self) -> Dict[str, Any]:
        length = self._content_length(required=True)
        if (len(self.headers.get_all("Content-Type", [])) != 1
                or self.headers.get_content_type() != "application/json"
                or self.headers.get_content_charset() not in (None, "utf-8", "utf8")
                or self.headers.get_all("Content-Encoding") is not None):
            raise APIError(415, "仅接受 UTF-8 application/json")
        raw = bytearray()
        while len(raw) < length:
            remaining = self._read_deadline - time.monotonic()
            if remaining <= 0:
                raise APIError(408, "读取请求超时")
            self.connection.settimeout(remaining)
            try:
                chunk = self.rfile.read1(length - len(raw))
            except TimeoutError:
                raise APIError(408, "读取请求超时") from None
            if not chunk:
                raise APIError(400, "请求体不完整")
            raw.extend(chunk)
        self.connection.settimeout(self.REQUEST_TIMEOUT)
        if not raw:
            return {}

        def unique_object(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("JSON 不允许重复字段")
                obj[key] = value
            return obj

        def invalid_constant(value):
            raise ValueError("JSON 不允许非有限数值")

        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                              parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, "JSON 格式无效") from None
        if not isinstance(data, dict):
            raise APIError(400, "JSON 顶层必须是对象")
        return data

    def _error(self, exc):
        if isinstance(exc, APIError):
            self._send_json({"ok": False, "reason": str(exc)}, exc.status)
        elif isinstance(exc, ValueError):
            self._send_json({"ok": False, "reason": str(exc)}, 400)
        else:
            traceback.print_exc()
            self._send_json({"ok": False, "reason": "接口处理失败"}, 500)

    # ---------------- 路由 ----------------
    def do_GET(self):
        try:
            path = self._authorize("GET")
            if self._content_length():
                raise APIError(400, "GET 不接受请求体")
            if path == "/api/ping":
                return self._send_json({"ok": True, "app": "ECPilot"})
            if path == "/api/state":
                return self._send_json(self.ctx.snapshot())
            if path == "/api/stream":
                return self._stream()
            if path == "/api/driver/status":
                return self._send_json(self._driver_status(self.ctx))
            if path == "/api/journal":
                with self.ctx.guard.lock:
                    entries = copy.deepcopy(self.ctx.guard.journal.recent(200))
                return self._send_json({"entries": entries})
            if path == "/api/settings":
                return self._send_json(self._settings_payload())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        try:
            path = self._authorize("POST")
            data = self._read_json()
            return self._send_json(self._dispatch(path, data))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            self._error(exc)

    def do_OPTIONS(self):
        self._send_json({"ok": False, "reason": "不支持跨域预检"}, 403)

    def _dispatch(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.ctx
        if path in self.DISABLED_PATHS:
            raise APIError(403, "此操作仅供原生界面内部调用")
        if path not in self.POST_PATHS:
            raise APIError(404, "未知接口")
        if not isinstance(data, dict):
            raise ValueError("请求数据必须是对象")
        if path == "/api/safety":
            with ctx.store.lock:
                return {"ok": True, "safety": ctx.store.update_safety(data)}
        if path == "/api/settings":
            if "autostart" in data:
                raise APIError(403, "开机自启仅供原生界面内部设置")
            return ctx.apply_settings(data)
        if path == "/api/profiles/snapshot":
            with ctx.store.lock:
                return {"ok": True, "data": copy.deepcopy(profiles.snapshot(ctx.store))}
        if path == "/api/journal/clear":
            with ctx.guard.lock:
                ctx.guard.journal.clear_history()
            return {"ok": True}
        if path in ("/api/show", "/api/shutdown"):
            if data:
                raise ValueError("此接口只接受空对象")
            callback = ctx.on_show if path == "/api/show" else ctx.on_exit
            if not callable(callback):
                raise APIError(503, "原生窗口或退出回调尚未就绪")
            callback()
            return {"ok": True}
        raise APIError(404, "未知接口")

    def _settings_payload(self) -> Dict[str, Any]:
        ctx = self.ctx
        return {
            "ok": True,
            "settings": ctx.settings(),
            "auto": ctx.auto_view(),
            "paths": {
                "data": os.path.join(ctx.base_dir, "data"),
                "exe": autostart.exe_path(),
                "log": os.path.join(ctx.base_dir, "data", "app.log"),
            },
            "admin": _is_admin(),
            "backend": {"kind": ctx.backend.kind, "label": ctx.backend.label},
            "resident": ctx.store.snapshot()["ui"]["stay_resident"],
        }

    @staticmethod
    def _open_path(ctx, which: str) -> Dict[str, Any]:
        data_dir = os.path.join(ctx.base_dir, "data")
        target = {"log": os.path.join(data_dir, "app.log"), "exe": ctx.base_dir}.get(which, data_dir)
        try:
            if which == "log" and not os.path.isfile(target):
                return {"ok": False, "reason": "还没有日志文件"}
            os.startfile(target)  # noqa: S606  (Windows only, 本地打开文件夹)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc)}

    @staticmethod
    def _elevate(ctx) -> Dict[str, Any]:
        """以管理员身份重启自己：先让新进程等旧进程退出，避免抢不到端口。"""
        if not getattr(sys, "frozen", False):
            return {"ok": False, "reason": "请用『以管理员身份启动.bat』启动"}
        if not callable(ctx.on_exit):
            return {"ok": False, "reason": "退出回调尚未就绪"}
        try:
            import ctypes

            if ctypes.windll.shell32.IsUserAnAdmin():
                return {"ok": False, "reason": "已经是管理员权限"}
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
            # 隐藏的 cmd 等 2 秒，让我们先把风扇还原并释放端口
            cmdline = f'/c timeout /t 2 /nobreak >nul & start "" "{exe}" {params}'.strip()
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", cmdline, None, 0)
            if rc <= 32:
                return {"ok": False, "reason": "提权被取消"}
            if callable(ctx.on_exit):
                ctx.on_exit()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc)}

    # ---------------- 驱动安装 ----------------
    @staticmethod
    def _driver_setup_src(ctx) -> str:
        """找到随程序分发 / 打包在一起的官方 NTPort 安装器。"""
        roots = []
        if getattr(sys, "frozen", False):
            roots.append(getattr(sys, "_MEIPASS", "") or "")
            roots.append(os.path.dirname(os.path.abspath(sys.executable)))
        roots.append(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
        roots.append(os.path.abspath(ctx.base_dir))
        for base in roots:
            if not base:
                continue
            path = os.path.join(base, "driver_setup", "NTPortDrvSetup.exe")
            if os.path.isfile(path):
                return path
        return ""

    @staticmethod
    def _driver_installed() -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Services\ZNTPORT") as key:
                winreg.QueryValueEx(key, "ImagePath")
            return True
        except Exception:
            return False

    def _driver_status(self, ctx) -> Dict[str, Any]:
        setup = self._driver_setup_src(ctx)
        return {
            "ok": True,
            "installed": self._driver_installed(),
            "setup": bool(setup),
            "admin": _is_admin(),
        }

    def _driver_install(self, ctx) -> Dict[str, Any]:
        src = self._driver_setup_src(ctx)
        if not src:
            return {"ok": False, "reason": "程序里没找到 NTPort 安装包"}
        try:
            import ctypes
            import shutil

            # 拷到可写目录：onefile 的临时目录会随进程退出被清掉
            dst_dir = os.path.join(ctx.base_dir, "driver_setup")
            if os.path.abspath(os.path.dirname(src)) != os.path.abspath(dst_dir):
                shutil.copytree(os.path.dirname(src), dst_dir, dirs_exist_ok=True)
            target = os.path.join(dst_dir, "NTPortDrvSetup.exe")
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, None, dst_dir, 1)
            if rc <= 32:
                return {"ok": False, "reason": "安装器启动被取消（UAC）"}
            return {"ok": True, "path": dst_dir}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc)}

    # ---------------- 数据流 ----------------
    def _stream(self):
        ctx = self.ctx
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        with ctx.client_lock:
            ctx.clients += 1
            ctx.ever_connected = True
            ctx.last_client_ts = time.time()
        try:
            last = None
            last_sent = 0.0
            while not ctx.want_exit:
                # 只在内容变化时推送；没变化就 5 秒发一次心跳，别让前端以为断了。
                # 原来是无条件 1 秒推一份完整快照（含 90 帧历史），纯属白烧 CPU。
                try:
                    payload = json.dumps(ctx.snapshot(), ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    payload = ""
                now = time.time()
                if payload != last or (now - last_sent) >= 5.0:
                    try:
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    last = payload
                    last_sent = now
                time.sleep(1.0)
        finally:
            with ctx.client_lock:
                ctx.clients = max(0, ctx.clients - 1)
                ctx.last_client_ts = time.time()

    def _ecmap(self):
        dump = self.ctx.light.dump()
        return {
            "ok": bool(dump),
            "dump": dump,
            "note": "只读快照：0x00-0xFF 的 EC RAM 内容",
        }

    def _diag(self):
        ctx = self.ctx
        temps = {}
        with ctx.store.lock:
            safety = copy.deepcopy(ctx.store.data["safety"])
            with ctx.guard.lock:
                rpm_map = ctx.backend.probe_rpm_map()
                for idx in (1, 2, 3, 4):
                    try:
                        temps[idx] = ctx.backend.bus.read_temp(idx)
                    except Exception as exc:  # noqa: BLE001
                        temps[idx] = f"错误：{exc}"
                fan_modes = {str(k): v for k, v in ctx.guard.fan_mode.items()}
        return {
            "ok": True,
            "backend": {"kind": ctx.backend.kind, "info": ctx.backend.info},
            "report": ctx.backend.info,
            "rpm_regs": rpm_map,
            "temps_by_index": temps,
            "config": safety,
            "fan_modes": fan_modes,
        }

    def _sensor_probe(self):
        """逐风扇、逐通道自检：哪条链路拿到了什么，失败原因是什么。"""
        ctx = self.ctx
        with ctx.guard.lock:
            detail = ctx.backend.probe_detail()
            rows = []
            if ctx.backend.kind == "real":
                for idx in (1, 2, 3, 4):
                    t0 = time.perf_counter()
                    try:
                        b1, b2, b3 = ctx.backend.bus.read_temp_raw(idx)
                        rows.append({
                            "fan": idx, "temp": b1, "b1": b2, "b2": b3,
                            "error": "", "ms": round((time.perf_counter() - t0) * 1000, 1),
                        })
                    except Exception as exc:  # noqa: BLE001
                        rows.append({
                            "fan": idx, "temp": -1, "b1": -1, "b2": -1,
                            "error": f"{type(exc).__name__}: {exc}",
                            "ms": round((time.perf_counter() - t0) * 1000, 1),
                        })
            detail["temp_raw"] = rows
            detail["backend"] = {"kind": ctx.backend.kind, "info": ctx.backend.info}
            bus = ctx.backend.bus
            detail["stats"] = getattr(bus, "stats", {})
            detail["last_error"] = getattr(bus, "last_error", "")
        return detail


def serve(ctx: AppContext, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("HTTP 服务只允许绑定 127.0.0.1")
    handler = type("BoundHandler", (Handler,), {"ctx": ctx})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    ctx.port = httpd.server_address[1]   # 重开窗口要用真实端口（可能被挤到 8766）
    return httpd
