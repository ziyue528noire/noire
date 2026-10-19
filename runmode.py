# -*- coding: utf-8 -*-
"""运行模式：模拟盘（默认）或实盘（真钱）。

由环境变量 NOIRE_PROFILE 决定：只有实盘脚本（run_live.bat、live_dryrun.bat 等）会把它设成 live，
其他任何情况（包括拼错）一律是模拟盘。
实盘的所有记录（日志、状态、订单台账、警报、成绩）都放在 live\\ 子文件夹，和模拟盘完全分开。
"""
import os

NAME = (os.environ.get("NOIRE_PROFILE") or "paper").strip().lower()
IS_LIVE = NAME == "live"
LABEL = "实盘" if IS_LIVE else "模拟盘"
