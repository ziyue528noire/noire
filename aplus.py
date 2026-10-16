# -*- coding: utf-8 -*-
"""A+ 稳健版：实盘用的“目标仓位 + 杠杆”计算。纯计算，不连券商、不下单。

规则（与 research/research_v2.py 里的“A+ 稳健版”相同，2026-10-15 用户选定）：
  1. 分 4 批换仓：资金分 4 份，各自的换仓日 = 每月最后一个交易日、以及它之前第 5/10/15 个交易日。
     每一批在自己的换仓日收盘后打分（过去 1/3/6/12 个月收益的平均），选前 2 名；
     选中的两只按“近 63 天日波动的倒数”分配这一批的资金（波动小的多买）；分数 <= 0 的那一份换成 SHY。
     每一批一直持有到它的下一个换仓日。
  2. 总的基础权重 = 4 批的平均（合计 100%）。
  3. 杠杆 = 30% ÷ 基础组合近 63 天的年化波动，限制在 1~2 倍；
     SPY 收盘价在 200 日均线下方（或数据不够判断）时，杠杆最多 1 倍。
  4. 杠杆只加在风险资产上；SHY 只用自有资金买，绝不借钱买现金。
"""
import math
import datetime as dt
import numpy as np
import pandas as pd

import config

DAYS_PER_MONTH = 21


def _as_date(x):
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return pd.Timestamp(x).date()


def signal_dates(trading_days, offsets=None):
    """trading_days：按时间排好的交易日（需覆盖完整月份）。
    返回 {offset: [各月的换仓日]}；offset=k 表示“月末前第 k 个交易日”。"""
    offsets = config.TRANCHE_OFFSETS if offsets is None else offsets
    days = sorted({_as_date(d) for d in trading_days})
    by_month = {}
    for d in days:
        by_month.setdefault((d.year, d.month), []).append(d)
    out = {k: [] for k in offsets}
    for _, ds in sorted(by_month.items()):
        for k in offsets:
            out[k].append(ds[max(len(ds) - 1 - k, 0)])
    return out


def latest_signal_dates(trading_days, as_of, offsets=None):
    """每一批“最近一次（<= as_of）”的换仓日。trading_days 必须包含 as_of 所在月份的全部交易日。"""
    as_of = _as_date(as_of)
    out = {}
    for k, ds in signal_dates(trading_days, offsets).items():
        past = [d for d in ds if d <= as_of]
        if past:
            out[k] = past[-1]
    return out


def momentum_score(close: pd.DataFrame) -> pd.Series:
    """最后一天的分数：过去 1/3/6/12 个月收益率的平均。"""
    parts = [close.iloc[-1] / close.iloc[-1 - m * DAYS_PER_MONTH] - 1 for m in config.LOOKBACK_MONTHS]
    return sum(parts) / len(parts)


def tranche_weights(closes: pd.DataFrame, day):
    """某一批在换仓日 day 收盘后的目标（合计 100%，未加杠杆）。返回 (权重, 说明)。"""
    uni, cash = list(config.ROTATION_UNIVERSE), config.CASH_SYMBOL
    c = closes.loc[:pd.Timestamp(day)]
    w = pd.Series(0.0, index=uni + [cash])
    need = max(config.LOOKBACK_MONTHS) * DAYS_PER_MONTH + 1
    if len(c) <= max(need, config.VOL_WINDOW + 1) or c[uni].iloc[-need:].isna().any().any():
        w[cash] = 1.0
        return w, "数据不够，拿现金"
    score = momentum_score(c[uni]).sort_values(ascending=False)
    picks = list(score.index[:config.TOP_N])
    vol = c[picks].pct_change().iloc[-config.VOL_WINDOW:].std()
    inv = 1.0 / vol.replace(0, np.nan)
    if config.WEIGHTING != "invvol" or inv.isna().any() or not np.isfinite(inv).all():
        inv = pd.Series(1.0, index=picks)              # 平均分配（或波动无法计算时的保守做法）
    share = inv / inv.sum()
    parts = []
    for sym in picks:
        if score[sym] > 0:
            w[sym] += share[sym]
            parts.append(f"{sym} {share[sym]:.0%}")
        else:
            w[cash] += share[sym]
            parts.append(f"{sym}→{cash} {share[sym]:.0%}（分数≤0）")
    return w, "、".join(parts)


def base_weights(closes: pd.DataFrame, trading_days, as_of=None):
    """4 批平均后的基础权重（合计 100%）。返回 (权重, 每批说明列表)。"""
    as_of = closes.index[-1] if as_of is None else as_of
    dates = latest_signal_dates(trading_days, as_of)
    total, notes = None, []
    for k in config.TRANCHE_OFFSETS:
        d = dates.get(k)
        if d is None:
            w = pd.Series(0.0, index=list(config.ROTATION_UNIVERSE) + [config.CASH_SYMBOL])
            w[config.CASH_SYMBOL] = 1.0
            note = "还没有换仓日，拿现金"
        else:
            w, note = tranche_weights(closes, d)
        total = w if total is None else total + w
        label = "月末" if k == 0 else f"月末前{k}天"
        notes.append(f"第{config.TRANCHE_OFFSETS.index(k) + 1}批（{label}，信号 {d}）：{note}")
    return total / len(config.TRANCHE_OFFSETS), notes


def leverage(closes: pd.DataFrame, base: pd.Series):
    """杠杆 = 波动目标 ÷ 组合近 63 天年化波动（1~LEVERAGE 倍）；SPY 在 200 日线下方时最多 1 倍。
    返回 (杠杆, 说明, 详细数据)。"""
    cols = [s for s in base.index if base[s] > 0]
    rets = closes[cols].pct_change().iloc[-config.VOL_WINDOW:]
    detail = {"vol": None, "trend_ok": False, "spy": None, "sma": None}
    if len(rets) < config.VOL_WINDOW or rets.isna().any().any():
        vol_lev, vol_msg = config.LEV_FLOOR, "波动数据不够 → 1 倍"
    else:
        vol = float((rets @ base[cols]).std(ddof=1)) * math.sqrt(252)
        detail["vol"] = vol
        vol_lev = config.LEV_FLOOR if vol <= 0 else min(config.LEVERAGE, max(config.LEV_FLOOR, config.VOL_TARGET / vol))
        vol_msg = f"组合近{config.VOL_WINDOW}天波动 {vol:.1%} → {config.VOL_TARGET:.0%}÷波动 = {vol_lev:.2f} 倍"
    spy = closes[config.TREND_SYMBOL].dropna()
    if len(spy) >= config.TREND_DAYS:
        last, sma = float(spy.iloc[-1]), float(spy.iloc[-config.TREND_DAYS:].mean())
        detail.update(spy=last, sma=sma, trend_ok=last > sma)
        trend_msg = (f"{config.TREND_SYMBOL} {last:.2f} {'高于' if last > sma else '低于'} "
                     f"{config.TREND_DAYS} 日均线 {sma:.2f}")
    else:
        trend_msg = f"{config.TREND_SYMBOL} 数据不够判断趋势 → 最多 1 倍"
    lev = vol_lev if detail["trend_ok"] else min(vol_lev, 1.0)
    msg = f"杠杆 {lev:.2f} 倍（{vol_msg}；{trend_msg}{'' if detail['trend_ok'] else ' → 最多 1 倍'}）"
    return lev, msg, detail


def final_weights(base: pd.Series, lev: float) -> pd.Series:
    """把杠杆加到风险资产上；SHY 只用剩下的自有资金，不借钱买现金。合计可以超过 100%（= 借钱）。"""
    cash = config.CASH_SYMBOL
    w = base.copy()
    risky = [s for s in w.index if s != cash]
    w[risky] = w[risky] * lev
    w[cash] = min(float(base.get(cash, 0.0)), max(0.0, 1.0 - float(w[risky].sum())))
    return w[w > 1e-9]


def compute(closes: pd.DataFrame, trading_days, as_of=None):
    """一站式：返回 (最终目标权重, 杠杆, 说明行列表)。"""
    as_of = closes.index[-1] if as_of is None else pd.Timestamp(as_of)
    c = closes.loc[:as_of]
    base, notes = base_weights(c, trading_days, as_of)
    lev, lev_msg, _ = leverage(c, base)
    target = final_weights(base, lev)
    lines = ["四批信号：", *[f"  {n}" for n in notes],
             f"合并后（未加杠杆）：{', '.join(f'{s} {v:.0%}' for s, v in base[base > 1e-9].items())}",
             lev_msg]
    return target, lev, lines
