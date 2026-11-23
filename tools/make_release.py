# -*- coding: utf-8 -*-
"""把 dist/ECPilot.exe 组装成一个可以直接发给别人用的发布包。

产物：
  dist/ECPilot-<版本>/             ← 绿色版目录（双击 ECPilot.exe 即可用）
      ECPilot.exe
      使用说明.txt
      安装.bat / setup.ps1         ← 装到 %LOCALAPPDATA%，建快捷方式 + 开机自启
      卸载.bat
      开机自启-关闭.bat
  dist/ECPilot-<版本>.zip          ← 上面整个目录压一个包
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")


def _version() -> str:
    path = os.path.join(ROOT, "app", "version.py")
    with open(path, "r", encoding="utf-8") as fh:
        found = re.search(r'VERSION\s*=\s*"([^"]+)"', fh.read())
    return found.group(1) if found else "0.0.0"


VERSION = _version()

LAUNCHER = r"""@echo off
chcp 936 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Mode {mode}
echo.
pause
"""

SETUP_PS1 = r"""param([ValidateSet("install", "uninstall", "autostart-off")]$Mode = "install")

$ErrorActionPreference = "Continue"
$src        = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest       = Join-Path $env:LOCALAPPDATA "ECPilot"
$exe        = Join-Path $dest "ECPilot.exe"
$taskName   = "EC Pilot"
$desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "EC Pilot.lnk"
$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "EC Pilot.lnk"
$startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "EC Pilot.vbs"

function Remove-Autostart {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($t) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }
    if (Test-Path $startupLnk) { Remove-Item $startupLnk -Force -ErrorAction SilentlyContinue }
    if (Test-Path $startupVbs) { Remove-Item $startupVbs -Force -ErrorAction SilentlyContinue }
}

function New-Autostart {
    $act = New-ScheduledTaskAction -Execute $exe -Argument "--autostart" -WorkingDirectory $dest
    $trg = New-ScheduledTaskTrigger -AtLogOn
    $prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $act -Trigger $trg -Principal $prn -Settings $settings -Force `
        -Description "EC Pilot 风扇与灯效控制台（后台常驻）" | Out-Null
}

function Save-Shortcut([string]$path) {
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($path)
    $s.TargetPath = $exe
    $s.WorkingDirectory = $dest
    $s.Description = "EC Pilot - 风扇与灯效控制台"
    $s.Save()
}

switch ($Mode) {
    "install" {
        Write-Host "=========================================="
        Write-Host "   EC Pilot 安装"
        Write-Host "   目标目录：$dest"
        Write-Host "=========================================="
        Write-Host ""

        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Copy-Item (Join-Path $src "ECPilot.exe") $exe -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $exe)) {
            Write-Host "[X] 复制失败：无法写入 $dest"
            Write-Host "    请检查磁盘空间或安全软件拦截。"
            return
        }
        $doc = Join-Path $src "使用说明.txt"
        if (Test-Path $doc) { Copy-Item $doc (Join-Path $dest "使用说明.txt") -Force -ErrorAction SilentlyContinue }
        Write-Host "[1/4] 复制程序 ... 完成"

        Save-Shortcut $desktopLnk
        Write-Host "[2/4] 桌面快捷方式 ... 完成"

        $ok = $false
        try { New-Autostart; $ok = $true } catch { $ok = $false }
        if ($ok) {
            Write-Host "[3/4] 开机自启 ... 已设置（登录后自动以最高权限启动，不弹 UAC）"
        } else {
            # 没有管理员权限时退而求其次：放进启动文件夹（登录会弹一次 UAC）
            try {
                Save-Shortcut $startupLnk
                Write-Host "[3/4] 开机自启 ... 已放入启动文件夹（没有管理员权限，登录时会弹一次 UAC）"
                Write-Host "        想免弹窗：右键 安装.bat → 以管理员身份运行，再来一次"
            } catch {
                Write-Host "[3/4] 开机自启 ... 跳过（权限不足）"
            }
        }

        # 通过计划任务启动，这样进程挂在任务计划服务下，和安装窗口无关
        $started = $false
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            try { Start-ScheduledTask -TaskName $taskName; $started = $true } catch { $started = $false }
        }
        if (-not $started) { Start-Process -FilePath $exe -WorkingDirectory $dest }
        # 等后台实例起来，再点一次：程序发现已有实例，会直接把界面弹出来
        for ($i = 0; $i -lt 20; $i++) {
            try {
                $r = Invoke-WebRequest "http://127.0.0.1:8765/api/ping" -UseBasicParsing -TimeoutSec 1
                if ($r.StatusCode -eq 200) { break }
            } catch { }
            Start-Sleep -Milliseconds 400
        }
        Start-Process -FilePath $exe -WorkingDirectory $dest
        Write-Host "[4/4] 已启动，界面会自动打开"
        Write-Host ""
        Write-Host "安装完成。"
        Write-Host "  · 关掉界面不会退出，程序在后台常驻并继续管风扇。"
        Write-Host "  · 想看风扇状态，双击桌面上的 “EC Pilot” 即可。"
        Write-Host "  · 开机自启 / 常驻方式，在程序里「设置」页拨开关就行。"
        Write-Host "  · 不想要了，运行同目录的 “卸载.bat”。"
    }

    "autostart-off" {
        Remove-Autostart
        Write-Host "已关闭开机自启（程序本身没卸载，仍可手动打开）。"
    }

    "uninstall" {
        Write-Host "正在停止 EC Pilot 并把风扇交还固件 ..."
        if (Test-Path $exe) {
            Start-Process -FilePath $exe -ArgumentList "--quit" -WindowStyle Hidden
            Start-Sleep -Seconds 4
        }
        Remove-Autostart
        if (Test-Path $desktopLnk) { Remove-Item $desktopLnk -Force -ErrorAction SilentlyContinue }

        Write-Host "正在删除程序文件 ..."
        $left = $false
        if (Test-Path $dest) {
            for ($i = 0; $i -lt 5; $i++) {
                try { Remove-Item $dest -Recurse -Force -ErrorAction Stop; $left = $false; break }
                catch { $left = $true; Start-Sleep -Seconds 2 }
            }
        }
        Write-Host ""
        if ($left) {
            Write-Host "卸载完成，但目录仍被占用：$dest"
            Write-Host "重启后再运行一次 卸载.bat 即可删干净。"
        } else {
            Write-Host "卸载完成。风扇已交还固件自动控制。"
        }
    }
}
"""


def write_text(path: str, text: str, encoding: str) -> None:
    with open(path, "w", encoding=encoding, newline="\r\n") as fh:
        fh.write(text)


def main() -> int:
    exe = os.path.join(DIST, "ECPilot.exe")
    if not os.path.isfile(exe):
        print("[X] 先运行 tools/build_exe.py 生成 dist/ECPilot.exe")
        return 1

    target = os.path.join(DIST, f"ECPilot-{VERSION}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target)

    shutil.copy2(exe, os.path.join(target, "ECPilot.exe"))
    doc = os.path.join(DIST, "使用说明.txt")
    if os.path.isfile(doc):
        shutil.copy2(doc, os.path.join(target, "使用说明.txt"))

    # 批处理用 GBK（cmd 默认代码页）；PowerShell 用带 BOM 的 UTF-8
    write_text(os.path.join(target, "安装.bat"), LAUNCHER.format(mode="install"), "gbk")
    write_text(os.path.join(target, "卸载.bat"), LAUNCHER.format(mode="uninstall"), "gbk")
    write_text(os.path.join(target, "开机自启-关闭.bat"),
               LAUNCHER.format(mode="autostart-off"), "gbk")
    write_text(os.path.join(target, "setup.ps1"), SETUP_PS1, "utf-8-sig")

    archive = shutil.make_archive(os.path.join(DIST, f"ECPilot-{VERSION}"), "zip", target)
    size = os.path.getsize(archive) / 1048576

    print(f"  发布目录：{target}")
    for name in sorted(os.listdir(target)):
        print(f"    · {name}")
    print(f"  压缩包：{archive}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
