# -*- coding: utf-8 -*-
"""键盘灯 64 位桥。

为什么需要桥：
    ECPilot 是 32 位进程（Ntport.dll / ClevoEcInfo.dll 只有 32 位，风扇离不开它），
    而本机唯一能点键盘灯的 InsydeDCHU.dll 是 64 位。位宽不一致，加载必然失败
    （WinError 193）。所以灯控走一条独立通道：32 位主程序 ⇄ 64 位 PowerShell 宿主
    ⇄ InsydeDCHU.dll。

协议（文本行，stdin/stdout）：
    发送 "S <hex>"      -> 写入一个 32 位灯效码            回 "OK <rc>"
    发送 "A r g b"      -> 把颜色写进 BIOS app settings   回 "OK <rc>"
    发送 "M <mode>"     -> 切换性能模式（0 静音 / 1 省电 / 2 性能 / 3 娱乐）
                                                             回 "OK <rc>"
    发送 "P"            -> 心跳                             回 "OK pong"
    发送 "Q"            -> 退出

性能模式走的是 EC 命令 0x79 的子功能 0x19（Clevo 官方 CLEVO_CMD_OPT /
CLEVO_CMD_OPT_SUB_SET_PERF_PROF，与 tuxedo-keyboard 驱动一致），
参数按小端拼成 DWORD：0x19000000 | 模式号。

灯效码格式（三处开源实现互相印证，见 tools/kbled_probe.py 注释）：
    bit31..28 操作码  0xF0=左区（单区键盘必须带，不带前缀的裸色值本机会被忽略）
                      0xF1=中区 / 0xF2=右区（仅三区机型有意义）
                      本机（NH5xRC）实测：三者等价，都是整块键盘
    bit23..16 B，bit15..8 R，bit7..0 G
"""
from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import threading
import time
from typing import Optional

# InsydeDCHU.dll 的常见落地位置（按优先级）
DLL_CANDIDATES = (
    r"C:\Program Files (x86)\ControlCenter\InsydeDCHU.dll",
    r"C:\Program Files\ControlCenter\InsydeDCHU.dll",
    r"C:\Program Files\ClevoRGBControl\Service\InsydeDCHU.dll",
    r"C:\Program Files\ClevoRGBControl\Experimental\InsydeDCHU.dll",
    r"C:\Program Files (x86)\ClevoRGBControl\InsydeDCHU.dll",
)

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$dir = '@DLLDIR@'
$env:PATH = $dir + ';' + $env:PATH
[Environment]::CurrentDirectory = $dir

$src = @'
using System;
using System.Runtime.InteropServices;
public static class KbLed {
    [DllImport("InsydeDCHU.dll", CallingConvention = CallingConvention.StdCall)]
    private static extern int SetDCHU_Data(int cmd, byte[] buf, int len);

    [DllImport("InsydeDCHU.dll", CallingConvention = CallingConvention.StdCall)]
    private static extern int WriteAppSettings(int page, int off, int len, byte[] buf);

    public static int Set(uint code) {
        byte[] b = BitConverter.GetBytes(code);
        return SetDCHU_Data(0x67, b, 4);
    }

    public static int App(int r, int g, int b) {
        byte[] c = new byte[] { (byte)r, (byte)g, (byte)b };
        int rc = WriteAppSettings(2, 0x51, 3, c);
        byte[] m = new byte[] { 8 };
        WriteAppSettings(2, 0x20, 1, m);
        return rc;
    }

    public static int Perf(int mode) {
        byte[] b = new byte[] { (byte)mode, 0, 0, 0x19 };
        return SetDCHU_Data(0x79, b, 4);
    }
}
'@

try {
    Add-Type -TypeDefinition $src -ErrorAction Stop
} catch {
    [Console]::Out.WriteLine('FATAL ' + $_.Exception.Message)
    [Console]::Out.Flush()
    exit 1
}

[Console]::Out.WriteLine('READY ' + ([IntPtr]::Size * 8))
[Console]::Out.Flush()

$running = $true
while ($running -and (($line = [Console]::In.ReadLine()) -ne $null)) {
    $line = $line.Trim()
    if ($line.Length -eq 0) { continue }
    $p = $line.Split(' ')
    try {
        switch ($p[0]) {
            'S' {
                $code = [uint32]::Parse($p[1], [System.Globalization.NumberStyles]::HexNumber)
                $rc = [KbLed]::Set($code)
                [Console]::Out.WriteLine('OK ' + $rc)
            }
            'A' {
                $rc = [KbLed]::App([int]$p[1], [int]$p[2], [int]$p[3])
                [Console]::Out.WriteLine('OK ' + $rc)
            }
            'M' {
                $rc = [KbLed]::Perf([int]$p[1])
                [Console]::Out.WriteLine('OK ' + $rc)
            }
            'P' {
                [Console]::Out.WriteLine('OK pong')
            }
            'Q' {
                $running = $false
                [Console]::Out.WriteLine('OK bye')
            }
            default {
                [Console]::Out.WriteLine('ERR unknown')
            }
        }
    } catch {
        [Console]::Out.WriteLine('ERR ' + $_.Exception.Message)
    }
    [Console]::Out.Flush()
}
"""


def _ps_exe() -> str:
    """拿 64 位 PowerShell 的路径：32 位进程里 System32 会被重定向到 SysWOW64。"""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    is_wow64 = ctypes.sizeof(ctypes.c_void_p) == 4
    cands = []
    if is_wow64:
        cands.append(os.path.join(windir, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe"))
    cands.append(os.path.join(windir, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"))
    for path in cands:
        if os.path.isfile(path):
            return path
    return "powershell.exe"


def find_dll() -> str:
    for path in DLL_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


class DchuBridge:
    """长驻的 64 位灯控宿主；超时即废弃整代连接，绝不复用迟到响应。"""

    def __init__(self, dll_path: str = "", log=None, single_zone: bool = False):
        self.dll_path = dll_path or find_dll()
        self.log = log or (lambda *a, **k: None)
        #: 单区键盘：写「整个键盘」时只发 0xF0 一条就够了
        #: （三区机型要依次发 0xF0/0xF1/0xF2，否则另外两区颜色会留旧值）
        self.single_zone = single_zone
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._lines: Optional[queue.Queue] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._ready = False
        self._bits = 0
        self._disabled = False
        self._reason = ""
        self._start_ts = 0.0

    # ------------------------------------------------------------------ 生命周期
    @property
    def available(self) -> bool:
        return bool(self.dll_path) and not self._disabled

    @property
    def running(self) -> bool:
        """64 位宿主是否还活着（用于空闲回收，省内存）。"""
        return bool(self._proc and self._proc.poll() is None and self._ready)

    @property
    def reason(self) -> str:
        if not self.dll_path:
            return "没有找到 InsydeDCHU.dll（需要安装过蓝天 Control Center 或 ClevoRGBControl）"
        return self._reason

    def start(self, timeout: float = 8.0) -> bool:
        with self._lock:
            if not self.available:
                return False
            if self.running:
                return True
            if not self._kill_locked():
                return False
            script = PS_SCRIPT.replace("@DLLDIR@", os.path.dirname(self.dll_path).replace("'", "''"))
            import base64

            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            try:
                proc = subprocess.Popen(
                    [_ps_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-EncodedCommand", encoded],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startup,
                    bufsize=1,
                    universal_newlines=True,
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception as exc:  # noqa: BLE001
                self._reason = f"启动 64 位宿主失败：{exc}"
                return False

            # 队列是这一代宿主的私有对象；旧 reader 的迟到行和 EOF 只能落入旧队列。
            lines = queue.Queue()
            self._proc, self._lines = proc, lines
            self._reader_thread = threading.Thread(
                target=self._reader_loop, args=(proc, lines), name="dchu-reader", daemon=True)
            self._reader_thread.start()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = self._read_line(lines, deadline - time.monotonic())
                if line is None:
                    break
                parts = line.split()
                if parts and parts[0] == "READY":
                    try:
                        self._bits = int(parts[1]) if len(parts) == 2 else 0
                    except ValueError:
                        self._bits = 0
                    if self._bits != 64:
                        self._reason = f"宿主不是 64 位（{self._bits} 位），无法加载 InsydeDCHU.dll"
                        self._disabled = True
                        self._kill_locked()
                        return False
                    self._ready = True
                    self._reason = ""
                    self._start_ts = time.time()
                    self.log(f"键盘灯桥就绪（{self._bits} 位，DLL={self.dll_path}）")
                    return True
                if line.startswith("FATAL"):
                    self._reason = f"DLL 加载失败：{line[6:].strip()}"
                    self._disabled = True
                    self._kill_locked()
                    return False
            self._reason = "64 位宿主启动超时或提前退出"
            self._kill_locked()
            return False

    def stop(self) -> None:
        with self._lock:
            self._kill_locked()

    def _kill_locked(self) -> bool:
        proc = self._proc
        self._ready = False
        self._bits = 0
        self._lines = None
        if proc is None:
            return True
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            # 未确认回收就不能再开第二个 EC 宿主；保留句柄供 stop 再次回收。
            self._disabled = True
            self._reason = f"{self._reason}；宿主回收失败，桥已停用：{exc}".lstrip("；")
            return False
        self._proc = None
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        reader, self._reader_thread = self._reader_thread, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.2)
        return True

    # ------------------------------------------------------------------ IO
    @staticmethod
    def _read_line(lines: queue.Queue, timeout: float) -> Optional[str]:
        try:
            line = lines.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        return line.strip() if line is not None else None

    @staticmethod
    def _reader_loop(proc: subprocess.Popen, lines: queue.Queue) -> None:
        """每代一个 reader；不读取随重连改变的 self._lines。"""
        try:
            for line in proc.stdout:
                lines.put(line)
        except Exception:
            pass
        finally:
            lines.put(None)

    def _call(self, cmd: str, timeout: float = 2.0) -> Optional[str]:
        with self._lock:
            if not self.available:
                return None
            # 检查、启动和请求是一笔事务，不能被 stop/另一条命令插进中间。
            if not self.running and not self.start():
                return None
            proc, lines = self._proc, self._lines
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
            except Exception as exc:  # noqa: BLE001
                self._reason = f"写入宿主失败：{exc}"
                self._kill_locked()
                return None
            line = self._read_line(lines, timeout)
            if line is None:
                self._reason = "宿主响应超时或连接中断；本次结果未知"
                self._kill_locked()
                return None
            if line.startswith("OK "):
                self._reason = ""
                return line
            if line.startswith("ERR "):
                self._reason = line[4:].strip()
                return None
            self._reason = f"宿主协议响应无效：{line}"
            self._kill_locked()
            return None

    def _call_rc(self, cmd: str, timeout: float = 2.0) -> Optional[int]:
        with self._lock:
            line = self._call(cmd, timeout)
            if line is None:
                return None
            parts = line.split()
            try:
                if len(parts) != 2:
                    raise ValueError("返回码数量无效")
                rc = int(parts[1])
            except ValueError:
                self._reason = f"宿主返回码无效：{line}"
                self._kill_locked()
                return None
            if rc != 0:
                self._reason = f"EC 返回码 {rc}"
            return rc

    # ------------------------------------------------------------------ 对外
    def send_code(self, code: int) -> bool:
        return self._call_rc(f"S {code & 0xFFFFFFFF:08X}") == 0

    def set_color(self, r: int, g: int, b: int, zone: int = 0) -> bool:
        """zone: 0=整个键盘（三区依次写），1..3=只写某一区。

        注意（2026-10-31 实测）：本机是单区键盘，0xF0/0xF1/0xF2 三条命令
        写下去整块键盘都是同一种颜色 —— 固件里只有一个颜色寄存器。
        所以在这台机器上选「左区/中区/右区」看不出差别，是硬件决定的；
        换到真正的三区机型（P7xx/P8xx 等）才有效果。
        """
        if zone == 0 and self.single_zone:
            targets = (1,)          # 单区键盘：一条就够，别白写两遍
        else:
            targets = (1, 2, 3) if zone == 0 else (zone,)
        with self._lock:
            for z in targets:
                code = ((0xF0 + z - 1) << 24) | ((b & 0xFF) << 16) | ((r & 0xFF) << 8) | (g & 0xFF)
                if not self.send_code(code):
                    return False
        return True

    def save_color(self, r: int, g: int, b: int) -> bool:
        return self._call_rc(f"A {int(r)} {int(g)} {int(b)}") == 0

    def set_perf_mode(self, mode: int) -> Optional[int]:
        """切换性能模式，返回 EC 的返回码；通道不通时返回 None。

        模式号：0 静音 / 1 省电 / 2 性能 / 3 娱乐（与 BIOS 里的四档一致）。
        注意：这条 EC 命令**只写不读**——本机固件没有对应的读取子功能，
        所以「当前是哪一档」由我们自己记，而不是回读硬件。
        """
        return self._call_rc(f"M {int(mode) & 0xFF}", timeout=5.0)

    def ping(self) -> bool:
        return self._call("P", timeout=1.0) == "OK pong"
