# -*- coding: utf-8 -*-
"""报警：写入 alerts.txt，并在 Windows 右下角弹通知。手机推送由 Claude 定时任务读取 alerts.txt 转发。"""
import os
import base64
import subprocess
import datetime as dt

from paths import rt

ALERTS = rt("alerts.txt")


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


def add(msg: str, title: str = "Noire 交易警报", popup: bool = True):
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    with open(ALERTS, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {msg}\n")
    if popup:
        notify(title, msg)
