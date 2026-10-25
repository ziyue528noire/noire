# -*- coding: utf-8 -*-
"""报警：写入 alerts.txt，并在 Windows 右下角弹通知。手机推送由 Claude 定时任务读取 alerts.txt 转发。
实盘的警报写在 live\\alerts.txt，开头带“【实盘】”，通知标题是“Noire 实盘警报”。
实盘警报同时抄一份到本文件夹的 alerts.txt（2026-10-25）：手机推送的 Claude 定时任务（盘中警报、收盘日报、
周六体检）只读这个文件，而且它们的指令只能在用户电脑的桌面应用里改，所以由程序把实盘警报送过去。"""
import os
import base64
import subprocess
import datetime as dt

import runmode
import paths
from paths import rt

ALERTS = rt("alerts.txt")


def _mirror_path():
    """实盘警报的抄送位置：本文件夹的 alerts.txt。模拟盘不抄送。
    测试时（EXC_RUNTIME_DIR）只抄到 EXC_ALERTS_MIRROR 指定的临时文件，绝不碰真实文件。"""
    if not runmode.IS_LIVE:
        return None
    if os.environ.get("EXC_RUNTIME_DIR"):
        return os.environ.get("EXC_ALERTS_MIRROR") or None
    return os.path.join(paths.HERE, "alerts.txt")
TITLE = "Noire 实盘警报" if runmode.IS_LIVE else "Noire 交易警报"


def notify(title: str, body: str):
    """Windows 右下角弹通知（不需要安装任何东西）。非 Windows 环境只打印。"""
    if os.name != "nt" or os.environ.get("EXC_RUNTIME_DIR"):   # 测试时不弹窗
        print(f"[通知] {title}: {body}")
        return
    body = body.replace("'", "’")[:250]
    title = title.replace("'", "’")
    ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
          "$n = New-Object System.Windows.Forms.NotifyIcon; "
          "$n.Icon = [System.Drawing.SystemIcons]::Warning; $n.Visible = $true; "
          f"$n.ShowBalloonTip(15000, '{title}', '{body}', [System.Windows.Forms.ToolTipIcon]::Warning); "
          "Start-Sleep -Seconds 15; $n.Dispose()")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", enc],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        print(f"通知发送失败：{e}")


def add(msg: str, title: str = None, popup: bool = True):
    title = title or TITLE
    if runmode.IS_LIVE and not msg.startswith("【实盘】"):
        msg = "【实盘】" + msg
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M}  {msg}\n"
    with open(ALERTS, "a", encoding="utf-8") as f:
        f.write(line)
    mirror = _mirror_path()
    if mirror and os.path.abspath(mirror) != os.path.abspath(ALERTS):
        try:
            with open(mirror, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:          # 抄送失败不影响交易，live\alerts.txt 里已经有了
            print(f"实盘警报抄送到 {mirror} 失败：{e}")
    if popup:
        notify(title, msg)
