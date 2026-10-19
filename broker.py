# -*- coding: utf-8 -*-
"""连接券商的唯一入口。模拟盘和以前一样；实盘（真钱）要过三道闸才允许下单：

  1) config.py 里 LIVE_ENABLED = True
  2) 用户自己双击 enable_live.bat 并输入 YES（会在 live\\ 里留下 LIVE_ENABLED 文件）
  3) config.py 里 LIVE_MAX_CAPITAL（实盘最多管理多少美元）大于 0
只看账户（空跑、监控、查状态）只需要实盘密钥，不需要打开开关；这种“只读连接”不允许下新单。
实盘密钥只能由用户用 set_live_keys.bat 输入；Claude 不替用户输入密钥、不替用户打开开关、不替用户下真钱订单。
"""
import os

import config
import runmode
from paths import rt

ENABLE_FLAG = rt("LIVE_ENABLED")


class LiveDisabled(Exception):
    """实盘没有打开：不下单（这是正常的保护，不是程序出错）。"""


def live_trading_ready():
    """(能不能真钱下单, 不能的原因)。模拟盘永远可以。"""
    if not runmode.IS_LIVE:
        return True, ""
    if not getattr(config, "LIVE_ENABLED", False):
        return False, "config.py 里 LIVE_ENABLED 还是 False"
    if not os.path.exists(ENABLE_FLAG):
        return False, "还没有双击 enable_live.bat 打开实盘开关"
    if float(getattr(config, "LIVE_MAX_CAPITAL", 0) or 0) <= 0:
        return False, "config.py 里 LIVE_MAX_CAPITAL（实盘最多管理多少美元）还没设"
    return True, ""


class _ReadOnly:
    """实盘只读连接：可以查账户、持仓、订单；不能下新单。allow_unwind=True 时还能撤单、平仓（紧急停止用）。"""

    _BLOCKED = {"submit_order", "replace_order_by_id"}
    _UNWIND = {"cancel_orders", "cancel_order_by_id", "close_all_positions", "close_position"}

    def __init__(self, inner, allow_unwind=False):
        self._inner = inner
        self._allow_unwind = allow_unwind

    def __getattr__(self, name):
        if name in self._BLOCKED or (name in self._UNWIND and not self._allow_unwind):
            raise LiveDisabled(f"实盘只读连接不允许 {name}")
        return getattr(self._inner, name)


def client(key, secret, trading=False, allow_unwind=False):
    """trading=True：要下单（实盘必须先过三道闸）；False：只看账户（空跑、监控、状态、紧急停止）。"""
    from alpaca.trading.client import TradingClient
    if not runmode.IS_LIVE:
        return TradingClient(key, secret, paper=True)          # 模拟盘：和以前完全一样
    if trading:
        ok, why = live_trading_ready()
        if not ok:
            raise LiveDisabled(why)
        return TradingClient(key, secret, paper=False)         # 实盘（真钱），已过三道闸
    return _ReadOnly(TradingClient(key, secret, paper=False), allow_unwind=allow_unwind)
