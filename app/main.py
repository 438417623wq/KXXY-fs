# -*- coding: utf-8 -*-
"""EC Pilot 启动入口。

职责：环境自检 → 建后端 → 起引擎 → 起本地服务 → 打开窗口 → 退出时还原。

打包成 exe 后有两种“根目录”：
  * RES_ROOT  —— 只读资源（drivers、driver_setup），onefile 时在 _MEIPASS 临时目录
  * BASE_DIR  —— 可写目录（data/），放 exe 旁边，保证日志与还原记录跨次启动不丢
"""
from __future__ import annotations

import argparse
import atexit
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

FROZEN = bool(getattr(sys, "frozen", False))


def _res_root() -> str:
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable_base() -> str:
    """优先用 exe 所在目录；不可写时退到 %LOCALAPPDATA%\\ECPilot。"""
    candidates = []
    if FROZEN:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(exe_dir)
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        candidates.append(os.path.join(local, "ECPilot"))
    else:
        candidates.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            probe = os.path.join(base, ".ecpilot-write-test")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return base
        except OSError:
            continue
    return candidates[-1]


ROOT = _res_root()
BASE_DIR = _writable_base()
APP_DIR = os.path.join(ROOT, "app")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ.setdefault("ECPILOT_DATA", DATA_DIR)   # 界面层写 crash.log 用


_STDOUT_IS_LOG = False


class RotatingStream:
    """stdout/异常和普通日志共用同一滚动句柄，连续运行也有磁盘上限。"""
    def __init__(self, path):
        import logging
        from logging.handlers import RotatingFileHandler
        self._logging = logging
        self.handler = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        self.encoding = "utf-8"

    def write(self, text):
        if text.strip():
            for offset in range(0, len(text), 16384):
                record = self._logging.LogRecord("ecpilot", 20, "", 0, text[offset:offset + 16384].rstrip(), (), None)
                self.handler.handle(record)
        return len(text)

    def flush(self):
        self.handler.flush()

    def isatty(self):
        return False

    def fileno(self):
        return self.handler.stream.fileno()


_STREAM = None


def _log_stream():
    global _STREAM
    if _STREAM is None:
        _STREAM = RotatingStream(os.path.join(DATA_DIR, "app.log"))
    return _STREAM


def _bind_streams() -> None:
    """无控制台（windowed exe）时 stdout/stderr 是 None，重定向到日志文件。"""
    global _STDOUT_IS_LOG
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        fh = _log_stream()
    except OSError:
        fh = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = fh
        _STDOUT_IS_LOG = True
    if sys.stderr is None:
        sys.stderr = fh


_bind_streams()

if not FROZEN and APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from control import autostart               # noqa: E402
from control.engine import Engine            # noqa: E402
from control.light import LightController    # noqa: E402
from control.safety import SessionGuard      # noqa: E402
from hardware.backend import create_backend  # noqa: E402
from server import AppContext, serve         # noqa: E402
from store import Store                      # noqa: E402

DEFAULT_PORT = 8765


def probe_backend(root: str, timeout: float = 5.0):
    """探测硬件；端口驱动卡死时不能让整个程序卡在启动阶段。

    真机上出现过这种情况：进程被强制结束在 EC 事务中间，端口驱动的请求
    挂住不返回，之后任何 Inp/Outp 都无限阻塞。这里用带超时的探测，超时就
    退回模拟后端，让界面照常打开并明确告诉用户需要重启。
    """
    from hardware.backend import create_backend, make_sim_backend

    box: dict = {}

    def run() -> None:
        try:
            box["backend"] = create_backend(root, allow_sim=True)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    th = threading.Thread(target=run, name="backend-probe", daemon=True)
    th.start()
    th.join(timeout)
    if "backend" in box:
        return box["backend"]
    return make_sim_backend(
        "EC 端口无响应（端口驱动被卡住，通常是强制结束进程导致的）。"
        "当前风扇控制状态无法确认；本次只提供隔离模拟界面，请检查散热并重启电脑。"
    )


def log(msg: str) -> None:
    """控制台有就打印，同时始终落一份日志（GUI 模式下没有控制台可看）。"""
    global _LOG_FH
    line = f"  {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    # 打包版没有控制台，print 已经写进同一个 app.log 了，再写一遍就是双份日志
    if _STDOUT_IS_LOG:
        return
    try:
        if _LOG_FH is None:
            _LOG_FH = _log_stream()
        _LOG_FH.write(line + "\n")
    except Exception:
        pass


_LOG_FH = None


def fatal(title: str, text: str) -> None:
    """无控制台的 exe 出错时，至少弹一个框，而不是静默消失。"""
    log(f"[X] {title} {text}")
    if FROZEN and sys.stderr is None:
        return
    if not FROZEN:
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def self_elevate() -> bool:
    """不是管理员就用 UAC 重新拉起自己（只对打包后的 exe 生效）。

    EC 的端口 I/O 需要管理员权限，否则写风扇会被驱动直接拒绝。
    以前只在界面上提示一句，用户容易忽略；打包版直接提权更省事。
    """
    if not FROZEN or is_admin() or os.environ.get("ECPILOT_ELEVATED") == "1":
        return False
    try:
        import ctypes

        params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, os.path.dirname(sys.executable), 1)
        return rc > 32
    except Exception:
        return False


def ensure_32bit() -> None:
    """EC 驱动库是 32 位，64 位解释器一律交给内置运行时重跑（打包版天然 32 位）。"""
    if sys.maxsize <= 2 ** 32 or FROZEN:
        return
    py32 = os.path.join(ROOT, "runtime", "py32", "python.exe")
    if os.path.exists(py32) and os.environ.get("ECPILOT_RELAUNCHED") != "1":
        env = dict(os.environ, ECPILOT_RELAUNCHED="1")
        subprocess.Popen([py32, os.path.abspath(__file__)] + sys.argv[1:], env=env)
        sys.exit(0)
    log("[!] 当前是 64 位解释器且找不到内置 32 位运行时，硬件功能不可用。")


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.8) as resp:
            return b"ECPilot" in resp.read()
    except Exception:
        return False


def show_existing(port: int) -> bool:
    """让已经在跑的实例把窗口叫出来（原生界面才有这个接口）。"""
    for offset in range(0, 10):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port + offset}/api/show", method="POST",
                data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if b'"ok": true' in resp.read().replace(b"\n", b""):
                    return True
        except Exception:
            continue
    return False


class DisabledBridge:
    """模拟/降级界面绝不通过灯效或性能页绕回真实硬件。"""
    available = False
    running = False
    reason = "模拟或降级模式不提供真实灯效/性能控制"

    def start(self):
        return False

    def stop(self):
        pass

    def set_color(self, *args, **kwargs):
        return False

    def save_color(self, *args, **kwargs):
        return False

    def set_perf_mode(self, mode):
        return None


class SingleInstance:
    """机器范围的实例租约。硬件控制权不依赖 HTTP 端口是否被占用。"""
    def __init__(self, sim=False, kernel=None):
        import ctypes
        from ctypes import wintypes
        self.kernel = kernel or ctypes.WinDLL("kernel32", use_last_error=True)
        self._last_error = ctypes.get_last_error
        self.name = "Global\\ECPilot." + ("Simulation" if sim else "Hardware") + ".v2"
        self.handles = []
        self.thread = None
        self.stop_event = threading.Event()
        if kernel is None:
            for name, args, result in (
                ("CreateMutexW", [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
                ("CreateEventW", [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
                ("OpenEventW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
                ("SetEvent", [wintypes.HANDLE], wintypes.BOOL),
                ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
                ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ):
                fn = getattr(self.kernel, name)
                fn.argtypes, fn.restype = args, result

    def acquire(self):
        handle = self.kernel.CreateMutexW(None, False, self.name)
        error = self._last_error()
        if not handle:
            if error == 5:
                return False  # 其他用户/权限层已有实例，不能越过它再碰 EC。
            raise OSError(error, "无法建立硬件单实例保护")
        if error == 183:
            self.kernel.CloseHandle(handle)
            return False
        self.handles.append(handle)
        for suffix in (".show", ".exit"):
            event = self.kernel.CreateEventW(None, False, False, self.name + suffix)
            if not event:
                self.close()
                raise OSError(self._last_error(), "无法建立本机实例通知")
            self.handles.append(event)
        return True

    def notify(self, action):
        handle = self.kernel.OpenEventW(2, False, self.name + "." + action)
        if not handle:
            return False
        try:
            return bool(self.kernel.SetEvent(handle))
        finally:
            self.kernel.CloseHandle(handle)

    def watch(self, show, quit):
        if self.thread is not None:
            return
        def run():
            while not self.stop_event.is_set():
                if self.kernel.WaitForSingleObject(self.handles[1], 250) == 0:
                    show()
                if self.kernel.WaitForSingleObject(self.handles[2], 0) == 0:
                    quit()
        self.thread = threading.Thread(target=run, name="instance-events", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)
        if self.thread and self.thread.is_alive():
            return  # 不关闭仍被等待的 HANDLE；进程结束时内核释放。
        for handle in reversed(self.handles):
            self.kernel.CloseHandle(handle)
        self.handles.clear()


class Runner:
    def __init__(self, ctx: AppContext, httpd, engine: Engine, guard: SessionGuard):
        self.ctx = ctx
        self.httpd = httpd
        self.engine = engine
        self.guard = guard
        self._stopped = False
        self._shutdown_lock = threading.Lock()
        self._shutdown_done = threading.Event()
        self._power_lock = threading.Lock()
        self._power_state_lock = threading.Lock()
        self._power_epoch = 0
        self._wake = threading.Event()
        self.result = None
        self.instance = None

    def _restore_bounded(self):
        # Python 无法取消永久阻塞的内核 I/O：限时等待并保留未清账会话，不能假报已恢复。
        if not self.guard.lock.acquire(timeout=3.0):
            return dict(ok=False, done=[], errors=["硬件事务仍阻塞，未确认还原；保留恢复记录，建议重启电脑"])
        try:
            return self.guard.restore_all()
        except Exception as exc:
            return dict(ok=False, done=[], errors=[str(exc)])
        finally:
            self.guard.lock.release()

    def shutdown(self):
        self.guard.begin_shutdown()
        self.engine.stop()
        if not self._shutdown_lock.acquire(blocking=False):
            return self.result or dict(ok=False, errors=["正在还原中"])
        try:
            if self._stopped:
                return self.result
            self._stopped = True
            self.ctx.want_exit = True
            log("正在封禁写入并交还固件…")
            if self.engine.ident is not None and self.engine is not threading.current_thread():
                self.engine.join(timeout=3.0)
            # 不允许保留无人看管的手动风量；退出始终还原风扇。
            box = {}
            def restore():
                box["result"] = self._restore_bounded()
            worker = threading.Thread(target=restore, name="final-restore", daemon=True)
            worker.start()
            worker.join(5.0)
            self.result = box.get("result", dict(ok=False, done=[], errors=["还原调用超时，不能确认固件接管；恢复记录未清除，建议重启电脑"]))
            if self.result.get("ok"):
                if self.ctx.store.lock.acquire(timeout=1.0):
                    try:
                        for fan in self.ctx.store.data["fans"].values():
                            fan["mode"] = "auto"
                        self.ctx.store.save()
                        if not self.guard.close_clean():
                            self.result = dict(ok=False, errors=["仍有未恢复记录，不能确认安全退出"])
                    except Exception as exc:
                        self.result = dict(ok=False, errors=[f"保存退出状态失败：{exc}"])
                    finally:
                        self.ctx.store.lock.release()
                else:
                    self.result = dict(ok=False, errors=["保存退出状态锁超时，不能确认配置已转自动"])
            # 灯控关闭也不能无限等待已卡住的驱动锁。
            closer = threading.Thread(target=self.ctx.light.close, name="light-close", daemon=True)
            closer.start()
            closer.join(2.5)
            if self.httpd is not None:
                threading.Thread(target=self.httpd.shutdown, name="http-stop", daemon=True).start()
            for err in self.result.get("errors", []):
                log("[!] " + err)
            log("风扇已交还固件" if self.result.get("ok") else "未确认完成还原，未清除恢复记录")
            return self.result
        finally:
            self._shutdown_done.set()
            self._shutdown_lock.release()

    def suspend(self):
        with self._power_state_lock:
            self._power_epoch += 1
            self.guard.begin_suspend()
            self.engine.suspend()
        # 电源事件线程只投递；用户界面不等待 EC。在途 I/O 完成后恢复才可能成功。
        if not self._power_lock.acquire(blocking=False):
            return
        def work():
            try:
                res = self._restore_bounded()
                if not res.get("ok"):
                    log("休眠前还原未确认：" + "；".join(res.get("errors", [])))
            finally:
                self._power_lock.release()
        threading.Thread(target=work, name="suspend-restore", daemon=True).start()

    def resume(self):
        if self._stopped:
            return
        with self._power_state_lock:
            epoch = self._power_epoch
        def work():
            if not self._power_lock.acquire(timeout=4.0):
                log("唤醒后硬件仍阻塞，继续封禁软件接管")
                return
            try:
                if not self.ctx.store.lock.acquire(timeout=3.0):
                    log("唤醒后控制事务仍阻塞，继续封禁软件接管")
                    return
                try:
                    if self._stopped:
                        return
                    for fan in self.ctx.store.data["fans"].values():
                        fan["mode"] = "auto"
                    self.ctx.store.data["perf"]["known"] = False
                    self.ctx.store.save()
                    with self._power_state_lock:
                        if self._stopped or epoch != self._power_epoch:
                            return  # 旧唤醒任务不得撤销更晚发生的休眠封禁。
                        self.engine.resume()
                        self.guard.end_suspend()
                        self.ctx.light.on_resume()
                finally:
                    self.ctx.store.lock.release()
            finally:
                self._power_lock.release()
        threading.Thread(target=work, name="resume-control", daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="EC Pilot")
    parser.add_argument("--sim", action="store_true", help="强制模拟模式")
    parser.add_argument("--no-window", action="store_true", help="不自动打开窗口")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--trace-threads", type=int, default=0,
                        help="调试用：每 N 秒把所有线程的调用栈打到日志里")
    parser.add_argument("--autostart", action="store_true",
                        help="由开机自启任务启动：是否弹界面由设置页决定")
    parser.add_argument("--view", default="", help="打开窗口时直接进某一页（如 settings）")
    parser.add_argument("--quit", action="store_true",
                        help="让正在运行的实例退出（还原风扇后结束）")
    args = parser.parse_args()

    if os.environ.get("ECPILOT_DEBUG_STACK"):
        import faulthandler

        faulthandler.dump_traceback_later(8, repeat=True, file=sys.stderr)

    log("")
    log("EC Pilot · 风扇与灯效控制台")
    log("-" * 52)
    log(f"资源目录：{ROOT}")
    log(f"数据目录：{DATA_DIR}")
    log(f"管理员权限：{'是' if is_admin() else '否'}")

    if not 1 <= args.port <= 65526:
        parser.error("port 必须在 1..65526 之间")
    instance = SingleInstance(sim=args.sim)
    if args.quit:
        ok = instance.notify("exit")
        log("已通知原生实例还原并退出" if ok else "没有可访问的实例；管理员实例请用管理员权限通知")
        return 0 if ok else 1

    if not args.sim and not is_admin() and self_elevate():
        log("已请求管理员权限重新启动。")
        return 0

    if not args.sim:
        ensure_32bit()

    try:
        if not instance.acquire():
            if not args.autostart and not args.no_window:
                if not instance.notify("show"):
                    log("已有实例但无权唤窗，请使用同一用户和权限运行。")
            log("已有硬件控制实例，未创建第二个控制器。")
            return 0
    except OSError as exc:
        fatal("单实例保护失败", str(exc))
        return 1
    atexit.register(instance.close)

    control_data = os.path.join(DATA_DIR, "simulation") if args.sim else DATA_DIR
    store = Store(os.path.join(control_data, "config.json"))
    if args.sim:
        from hardware.backend import Backend, SimBus

        backend = Backend("sim", SimBus(), {"runtime": "模拟", "driver": "未使用（--sim）"})
    else:
        backend = probe_backend(ROOT)

    backend.info["权限"] = "管理员" if is_admin() else "普通用户（EC 写入可能被拒绝）"
    log(f"后端：{backend.label}")
    for key, value in backend.info.items():
        log(f"  · {key}: {value}")

    if backend.kind != "real" and not args.sim:
        # 探测降级不允许拿模拟写入去清真实设备的恢复记录。
        control_data = os.path.join(DATA_DIR, "simulation")
        store = Store(os.path.join(control_data, "config.json"))
    guard = SessionGuard(control_data, store.data)
    guard.bind(backend.bus)
    light = LightController(guard, store, log=log,
                            bridge=None if backend.kind == "real" else DisabledBridge())

    if guard.unclean and backend.kind == "real":
        log("检测到上次会话未正常结束，正在自动还原…")
        guard.begin_suspend()
        recovery = {}
        def recover():
            try:
                recovery["result"] = guard.restore_all()
            except Exception as exc:
                recovery["result"] = dict(ok=False, errors=[str(exc)])
        recover_thread = threading.Thread(target=recover, name="startup-restore", daemon=True)
        recover_thread.start()
        recover_thread.join(5.0)
        res = recovery.get("result", dict(ok=False, errors=["启动还原超时，请重启电脑"]))
        log("还原结果：" + ("、".join(res.get("done") or []) or "未确认完成"))
        # 旧会话恢复失败也不准再自动进入历史手动档；保留未恢复记录供后续重试。
        for fan in store.data["fans"].values():
            fan["mode"] = "auto"
        store.save()
        if res.get("ok"):
            guard.close_clean()
            guard.end_suspend()
        else:
            backend.info["恢复保护"] = "上次还原未确认，软件写入已封禁，请重启电脑"
            log("[!] 上次还原尚未确认，本次仅以自动策略运行。")

    engine = Engine(backend, guard, store.data, store)
    ctx = AppContext(ROOT, store, backend, guard, engine, light, base_dir=BASE_DIR)
    runner = Runner(ctx, None, engine, guard)  # type: ignore[arg-type]
    ctx.runner = runner
    runner.instance = instance
    # 开机自启的真实状态要问系统（约 2 秒），提前在后台问一次，别让设置页等
    threading.Thread(target=autostart.status, kwargs={"force": True}, daemon=True).start()

    port = args.port
    httpd = None
    for offset in range(0, 10):
        try:
            httpd = serve(ctx, port=port + offset)
            port = port + offset
            break
        except OSError:
            continue
    if httpd is None:
        fatal("EC Pilot 启动失败", "本地端口被占用，无法启动服务。请关闭占用 8765-8774 端口的程序后重试。")
        return 1
    runner.httpd = httpd

    engine.start()
    light.start()
    # 有界面在看的时候才需要 1 秒一帧；没人看就降到 idle_tick（默认 5 秒）
    engine.fast_hint = lambda: ctx.clients > 0 or bool(getattr(ctx, "window_visible", False))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log(f"服务：http://127.0.0.1:{port}/")

    if args.trace_threads > 0:
        import faulthandler

        faulthandler.dump_traceback_later(args.trace_threads, repeat=True, file=sys.stderr)
        log(f"已开启线程栈追踪，每 {args.trace_threads} 秒一次")

    atexit.register(runner.shutdown)

    def _sig(*_):
        runner.shutdown()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _sig)
        except Exception:
            pass

    stay_resident = bool(store.data.get("ui", {}).get("stay_resident", True))

    show_window = not args.no_window
    if show_window and args.autostart and not bool(
            store.data.get("ui", {}).get("open_window_on_start", False)):
        show_window = False
        log("由开机自启启动：按设置不弹界面（双击桌面图标可打开）。")

    if not show_window and stay_resident:
        log("常驻模式：关掉界面不会退出，随时双击程序可重新打开。")

    return run_native(ctx, runner, show_window, args.view)


def enable_dpi_aware() -> None:
    """必须在创建 wx.App 之前调用。

    不声明 DPI 感知时，Windows 会把窗口当位图拉伸（125% 缩放就是 1.25 倍），
    而 GDI+ 又按物理像素画 —— 结果内容比窗口大一圈，底部被裁。
    """
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v2
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def run_native(ctx, runner, show_window: bool, view: str = "") -> int:
    """原生界面：一个进程、一块画布，不启动浏览器。"""
    enable_dpi_aware()
    import wx  # 只有原生模式才需要

    from ui.app_native import NativeApp

    app = wx.App(False)
    ctx.window_visible = bool(show_window)  # SSE 连接数与窗口可见性分开维护
    frame = NativeApp(ctx, runner, start_hidden=not show_window, start_view=view)
    if show_window:
        frame.Show()
        frame.Raise()
        log("原生界面已打开（单进程，无浏览器内核）。")
    else:
        log("按设置不弹界面，已收在系统托盘。")
    try:
        app.MainLoop()
    except KeyboardInterrupt:
        pass
    finally:
        runner.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
