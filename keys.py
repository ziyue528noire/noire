# -*- coding: utf-8 -*-
"""读取 Alpaca 密钥：优先读本文件夹里的 .env，其次读系统环境变量。
密钥只保存在你自己的电脑上，不要发给任何人。"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load_keys():
    values = {}
    for name in (".env", ".env.txt"):
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.split("=", 1)
                        values[k.strip()] = v.strip().strip('"').strip("'")
            break
    key = values.get("ALPACA_KEY") or os.getenv("ALPACA_KEY")
    secret = values.get("ALPACA_SECRET") or os.getenv("ALPACA_SECRET")
    return key, secret
