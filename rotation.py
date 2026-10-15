# -*- coding: utf-8 -*-
"""动量轮动策略回测：运行  python rotation.py

规则（每月一次，非常简单）：
  1. 每月最后一个交易日收盘后，给每只 ETF 打分：
     分数 = 过去 1/3/6/12 个月收益率的平均（越高越强）
  2. 选分数最高的 TOP_N 只；如果某只分数 <= 0（说明它在跌），那一份换成短期国债 SHY
  3. 下一个交易日开盘按等权重换仓，扣手续费+滑点

为什么可能有效：“强者恒强”的动量效应是学术上被反复验证过的现象之一；
加上“跌了就躲进国债”这条规则，目的是在 2008、2022 这类大熊市里少亏。

同时会测 TOP_N = 1/2/3 三种参数——如果只有某一个参数好看，说明可能是巧合（过拟合）。
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
from data import load_prices
from backtest import metrics, run_backtest
from strategy import sma_crossover

DAYS_PER_MONTH = 21


def load_panel(symbols):
    """把多只 ETF 的开盘价、收盘价对齐成两张表。"""
    opens, closes = {}, {}
    for s in symbols:
        df = load_prices(s)
        opens[s], closes[s] = df["Open"], df["Close"]
    o = pd.DataFrame(opens).sort_index().ffill()
    c = pd.DataFrame(closes).sort_index().ffill()
    first_ok = c.dropna().index[0]          # 所有标的都有数据的第一天
    return o.loc[first_ok:], c.loc[first_ok:]


def momentum_score(close: pd.DataFrame) -> pd.DataFrame:
    parts = [close / close.shift(m * DAYS_PER_MONTH) - 1 for m in config.LOOKBACK_MONTHS]
    return sum(parts) / len(parts)


def target_weights(close: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """只在每月最后一个交易日决定新的目标权重，其余日子沿用。"""
    universe, cash = config.ROTATION_UNIVERSE, config.CASH_SYMBOL
    score = momentum_score(close[universe])
    month_end = close.groupby(close.index.to_period("M")).tail(1).index

    w = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for d in month_end:
        s = score.loc[d].dropna()
        row = pd.Series(0.0, index=close.columns)
        if len(s) < len(universe):          # 回看期数据还不够：先拿现金
            row[cash] = 1.0
        else:
            for sym in s.sort_values(ascending=False).index[:top_n]:
                row[sym if s[sym] > 0 else cash] += 1.0 / top_n
        w.loc[d] = row
    return w.ffill().fillna(0.0)


def run_rotation(opens, closes, top_n, cost=None):
    """收盘出信号 → 次日开盘成交；收益按“开盘到开盘”计算，避免偷看未来。"""
    cost = (config.COMMISSION + config.SLIPPAGE) if cost is None else cost
    w = target_weights(closes, top_n)
    held = w.shift(1).fillna(0.0)                      # 第 t 天开盘起持有的仓位
    ret_oo = (opens.shift(-1) / opens - 1).fillna(0.0) # 第 t 天开盘 → t+1 天开盘
    turnover = (held - held.shift(1).fillna(0.0)).abs().sum(axis=1)
    daily = (held * ret_oo).sum(axis=1) - turnover * cost
    equity = config.INITIAL_CAPITAL * (1 + daily).cumprod()
    return equity, w


def fmt_row(name, m):
    return (f"  {name:<18} 年化 {m['年化收益']:>6.1%}   夏普 {m['夏普比率']:>5.2f}   "
            f"最大回撤 {m['最大回撤']:>7.1%}   总收益 {m['总收益']:>8.1%}")


def main():
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    symbols = config.ROTATION_UNIVERSE + [config.CASH_SYMBOL]
    opens, closes = load_panel(symbols)

    # 前 12 个月是回看期，不算成绩；所有对比都从同一天开始
    warmup = max(config.LOOKBACK_MONTHS) * DAYS_PER_MONTH + 25
    start = closes.index[warmup]

    results = {}
    for n in (1, 2, 3):
        eq, w = run_rotation(opens, closes, n)
        results[f"动量轮动 TOP{n}"] = eq.loc[start:]
        if n == config.TOP_N:
            main_w = w
    eq2, _ = run_rotation(opens, closes, config.TOP_N,
                          cost=2 * (config.COMMISSION + config.SLIPPAGE))
    results[f"TOP{config.TOP_N} 成本翻倍"] = eq2.loc[start:]

    spy_full = load_prices("SPY")
    spy = spy_full.loc[start:]
    results["SPY 一直持有"] = spy["Close"] / spy["Close"].iloc[0] * config.INITIAL_CAPITAL
    # 均线需要 200 天预热，所以用完整数据算，再截取同一区间
    results["SPY 双均线(旧策略)"] = run_backtest(spy_full, sma_crossover(spy_full))["equity"].loc[start:]

    # ---- 文字报告 ----
    lines = [f"回测区间：{start.date()} ~ {closes.index[-1].date()}",
             f"候选 ETF：{', '.join(config.ROTATION_UNIVERSE)}；避险：{config.CASH_SYMBOL}", ""]
    for name, eq in results.items():
        eq = eq / eq.iloc[0] * config.INITIAL_CAPITAL
        results[name] = eq
        lines.append(fmt_row(name, metrics(eq)))

    # 分年度收益，看看是不是只靠某一两年
    yearly = pd.DataFrame({
        k: pd.concat([v.iloc[:1], v.resample("YE").last()]).pct_change().iloc[1:]
        for k, v in results.items()})
    yearly.index = yearly.index.year
    cols = [f"动量轮动 TOP{config.TOP_N}", "SPY 一直持有"]
    lines += ["", f"分年度收益（{cols[0]} vs SPY）："]
    for y, r in yearly[cols].iterrows():
        mark = "  ← 跑赢" if r.iloc[0] > r.iloc[1] else ""
        lines.append(f"  {y}   {r.iloc[0]:>7.1%}   {r.iloc[1]:>7.1%}{mark}")

    # 持仓历史 + 当前信号
    hist = main_w.loc[main_w.index.isin(
        closes.groupby(closes.index.to_period("M")).tail(1).index)].loc[start:]
    holdings = hist.apply(lambda r: " + ".join(f"{s}" for s in r[r > 0].index), axis=1)
    holdings.to_csv(os.path.join(config.REPORT_DIR, "rotation_holdings.csv"),
                    header=["持仓"], encoding="utf-8-sig")
    switches = (holdings != holdings.shift()).sum() - 1
    lines += ["", f"共换仓 {switches} 次（约每年 {switches / (len(holdings) / 12):.1f} 次）",
              f"最近 6 个月持仓：",
              *[f"  {d.date()}  {h}" for d, h in holdings.tail(6).items()],
              f"", f"按最新收盘数据，当前应持有：{holdings.iloc[-1]}"]

    text = "\n".join(lines)
    with open(os.path.join(config.REPORT_DIR, "rotation_summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)

    # ---- 图 ----
    show = [f"动量轮动 TOP{config.TOP_N}", "SPY 一直持有", "SPY 双均线(旧策略)"]
    labels = [f"Momentum TOP{config.TOP_N}", "SPY buy & hold", "SPY 50/200 SMA"]
    fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1]})
    for k, lab in zip(show, labels):
        ax[0].plot(results[k], label=lab)
        ax[1].plot(results[k] / results[k].cummax() - 1, label=lab)
    ax[0].set_yscale("log"); ax[0].legend(); ax[0].set_title("Equity (log scale)")
    ax[1].set_title("Drawdown")
    fig.tight_layout()
    fig.savefig(os.path.join(config.REPORT_DIR, "rotation.png"), dpi=110)
    print(f"\n报告已保存：{config.REPORT_DIR}/rotation_summary.txt, rotation.png, rotation_holdings.csv")


if __name__ == "__main__":
    main()
