# -*- coding: utf-8 -*-
"""运行文件（日志、状态、订单台账、STOP 等）的统一位置。

正常运行时就是本文件夹；跑测试时设置环境变量 EXC_RUNTIME_DIR 指向临时目录，
这样测试绝不会碰到真实账户的日志和状态文件。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.environ.get("EXC_RUNTIME_DIR") or HERE


def rt(*parts) -> str:
    """运行文件的完整路径，例如 rt("logs", "paper_log.txt")。"""
    return os.path.join(RUNTIME, *parts)
