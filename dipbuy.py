# -*- coding: utf-8 -*-
"""第二条腿：RSI(2) 超跌反弹（SPY）。纯计算，不连券商、不下单。

规则（research/research_v3.py 的 S1；Connors 2008 年公开参数，没有调；2026-10-16 用户同意启用）：
  - 买：SPY 收盘价在 200 日均线上方（大趋势向上），且 2 日 RSI < 10（最近两天跌得很急）
  - 卖：SPY 收盘价高于 5 日均线（反弹了）
  - 收盘出信号、下一个交易日开盘成交——和 A+ 稳健版在同一次运行里一起下单
  - 仓位：只用 A+ 稳健版没用完的额度 = min(1 倍, 杠杆上限 − A+ 风险资产总仓位)，总仓位永远不超过杠杆上限
  - SHY（现金）只用自有资金：第二条腿要用钱时先用掉 SHY 那部分，不够才借
持仓状态完全由历史价格推算（每天从头算一遍），不需要额外的状态文件。
"""
import numpy as np
import pandas as pd

import config


def rsi2(close: pd.Series) -> pd.Series:
    """2 日 RSI（Wilder 平滑，alpha = 1/2）。只涨不跌的日子没有定义（NaN，不会触发买入）。"""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def positions(close: pd.Series) -> pd.Series:
    """每天收盘后的持仓状态（1 = 持有）。"""
    close = close.dropna()
    entry = (close > close.rolling(config.DIP_TREND_DAYS).mean()) & (rsi2(close) < config.DIP_RSI_ENTRY)
    exit_ = close > close.rolling(config.DIP_EXIT_DAYS).mean()
    out, on = [], False
    for e, x in zip(entry.values, exit_.values):
        if on and x:
            on = False
        elif not on and e:
            on = True
        out.append(1.0 if on else 0.0)
    return pd.Series(out, index=close.index)


def allocate(target: pd.Series, max_total=None):
    """把第二条腿加到 A+ 的目标上（目标是“占本金的比例”，已含杠杆）。返回 (新目标, 第二条腿占本金的比例)。
    max_total：总仓位上限（实盘的杠杆上限）；None = LEVERAGE（模拟盘，和以前一样）。"""
    cash, sym = config.CASH_SYMBOL, config.DIP_SYMBOL
    cap = config.LEVERAGE if max_total is None else float(max_total)
    risky = float(sum(w for s, w in target.items() if s != cash))
    spare = max(0.0, min(config.DIP_MAX, cap - risky))
    new = target.copy()
    new[sym] = float(new.get(sym, 0.0)) + spare
    if cash in new.index:
        new[cash] = min(float(target[cash]), max(0.0, 1.0 - risky - spare))
    return new[new > 1e-9], spare


def compute(closes: pd.DataFrame, target: pd.Series, max_total=None):
    """返回 (合并后的目标, 第二条腿占本金的比例, 说明行)。没启用或没触发时目标不变。
    max_total：总仓位上限（实盘的杠杆上限）；None = 模拟盘。"""
    sym = config.DIP_SYMBOL
    if not getattr(config, "DIP_ENABLED", False):
        return target, 0.0, ["第二条腿（RSI(2) 超跌反弹）：未启用"]
    c = closes[sym].dropna()
    if len(c) < config.DIP_TREND_DAYS + 5:
        return target, 0.0, ["第二条腿：数据不够，不操作"]
    pos = positions(c)
    r, sma_t, sma_x = float(rsi2(c).iloc[-1]), float(c.iloc[-config.DIP_TREND_DAYS:].mean()), float(c.iloc[-config.DIP_EXIT_DAYS:].mean())
    detail = (f"{sym} 收盘 {c.iloc[-1]:.2f}，{config.DIP_TREND_DAYS} 日均线 {sma_t:.2f}，"
              f"{config.DIP_EXIT_DAYS} 日均线 {sma_x:.2f}，2 日 RSI {r:.0f}" if not np.isnan(r) else
              f"{sym} 收盘 {c.iloc[-1]:.2f}，2 日 RSI 无（连涨）")
    if pos.iloc[-1] < 0.5:
        just_exited = len(pos) > 1 and pos.iloc[-2] > 0.5
        note = (f"未持有（今天收盘高于 {config.DIP_EXIT_DAYS} 日均线 → 卖出信号，下一个交易日开盘卖出第二条腿的 {sym}）"
                if just_exited else "未持有")
        return target, 0.0, [f"第二条腿（RSI(2) 超跌反弹）：{note}（{detail}）"]
    since = pos.index[(pos.diff().fillna(pos) > 0).values][-1]          # 这一次是哪天收盘触发的
    when = "今天收盘新触发" if since == pos.index[-1] else f"{pd.Timestamp(since).date()} 收盘触发"
    new, spare = allocate(target, max_total)
    note = (f"持有中（{when}），用 A+ 没用完的额度 {spare:.0%} 买 {sym}" if spare > 0
            else f"持有中（{when}），但 A+ 已用满额度，这次不加仓")
    return new, spare, [f"第二条腿（RSI(2) 超跌反弹）：{note}（{detail}）"]
