# -*- coding: utf-8 -*-
"""读取 Alpaca 密钥。密钥只保存在你自己的电脑上，不要发给任何人。

模拟盘：读本文件夹里的 .env（set_keys.bat 写的），其次读系统环境变量。
实盘：只读 .env.live（set_live_keys.bat 写的），绝不退回去用模拟盘的密钥或环境变量。
跑测试时（EXC_RUNTIME_DIR）改从临时目录读，绝不碰真实密钥。
"""
import os

import runmode

HERE = os.path.dirname(os.path.abspath(__file__))


def _read(path) -> dict:
    values = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def load_keys():
    base = os.environ.get("EXC_RUNTIME_DIR") or HERE
    if runmode.IS_LIVE:
        v = _read(os.path.join(base, ".env.live"))
        return v.get("ALPACA_LIVE_KEY") or None, v.get("ALPACA_LIVE_SECRET") or None
    values = {}
    for name in (".env", ".env.txt"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            values = _read(path)
            break
    key = values.get("ALPACA_KEY") or os.getenv("ALPACA_KEY")
    secret = values.get("ALPACA_SECRET") or os.getenv("ALPACA_SECRET")
    return key, secret
