# -*- coding: utf-8 -*-
"""策略：只负责根据价格算出“目标仓位”（1 = 持有，0 = 空仓）。

想换策略，就照着 sma_crossover 再写一个函数，返回同样格式的 Series。
重要规则：第 t 天的信号只能用第 t 天收盘及以前的数据（不能偷看未来）。
"""
import pandas as pd
import config


def sma_crossover(df: pd.DataFrame) -> pd.Series:
    """双均线：快线在慢线上方就持有，否则空仓。
    经典的趋势跟随策略，目的是躲开大熊市，不是为了跑赢牛市。"""
    close = df["Close"]
    fast = close.rolling(config.FAST_MA).mean()
    slow = close.rolling(config.SLOW_MA).mean()
    signal = (fast > slow).astype(float)
    signal[slow.isna()] = 0.0          # 均线还没算出来之前不交易
    return signal


# 当前使用的策略
STRATEGY = sma_crossover
