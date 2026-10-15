# -*- coding: utf-8 -*-
"""回测引擎：运行  python backtest.py

逻辑（避免常见作弊）：
  - 第 t 天收盘产生信号 → 第 t+1 天开盘成交（不用当天收盘价“偷跑”）
  - 每笔买卖都扣手续费 + 滑点
  - 持仓从买入价跌超 STOP_LOSS_PCT 时，下一天开盘强制卖出，等信号重新翻多才能再买
  - 和“一直持有不动”（买入持有）对比，跑不赢它的策略就没意义
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
from data import load_prices
from strategy import STRATEGY


def run_backtest(df: pd.DataFrame, signal: pd.Series) -> pd.DataFrame:
    cost = config.COMMISSION + config.SLIPPAGE
    opens, closes = df["Open"].values, df["Close"].values
    sig = signal.values

    cash = config.INITIAL_CAPITAL
    shares = 0.0
    entry_price = 0.0
    stopped = False                 # 止损后等信号先变 0 再允许重新入场
    equity, pos_list, trades = [], [], []

    for t in range(len(df)):
        if t > 0:
            want = sig[t - 1]       # 用昨天收盘的信号，今天开盘执行
            if want == 0:
                stopped = False
            if shares > 0 and config.STOP_LOSS_PCT > 0 and \
                    closes[t - 1] <= entry_price * (1 - config.STOP_LOSS_PCT):
                want, stopped = 0, True
            if stopped:
                want = 0

            px = opens[t]
            if want > 0 and shares == 0:                    # 买入
                budget = cash * config.MAX_POSITION_PCT
                shares = budget / (px * (1 + cost))
                cash -= shares * px * (1 + cost)
                entry_price = px
                trades.append((df.index[t], "BUY", px))
            elif want == 0 and shares > 0:                  # 卖出
                cash += shares * px * (1 - cost)
                ret = px * (1 - cost) / (entry_price * (1 + cost)) - 1
                trades.append((df.index[t], "SELL", px, ret))
                shares = 0.0

        equity.append(cash + shares * closes[t])
        pos_list.append(1 if shares > 0 else 0)

    out = pd.DataFrame({"equity": equity, "position": pos_list}, index=df.index)
    out.attrs["trades"] = trades
    return out


def metrics(equity: pd.Series) -> dict:
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    max_dd = (equity / equity.cummax() - 1).min()
    return {"总收益": equity.iloc[-1] / equity.iloc[0] - 1, "年化收益": cagr,
            "夏普比率": sharpe, "最大回撤": max_dd}


def main():
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    rows, lines = [], []

    for sym in config.SYMBOLS:
        df = load_prices(sym)
        res = run_backtest(df, STRATEGY(df))
        bh = df["Close"] / df["Close"].iloc[0] * config.INITIAL_CAPITAL

        sells = [t for t in res.attrs["trades"] if t[1] == "SELL"]
        win = np.mean([t[3] > 0 for t in sells]) if sells else 0
        m, mb = metrics(res["equity"]), metrics(bh)
        rows.append({"标的": sym, **{f"策略_{k}": v for k, v in m.items()},
                     "交易次数": len(sells), "胜率": win, "持仓时间占比": res["position"].mean(),
                     **{f"买入持有_{k}": v for k, v in mb.items()}})

        fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                               gridspec_kw={"height_ratios": [3, 1]})
        ax[0].plot(res["equity"], label="Strategy")
        ax[0].plot(bh, label="Buy & Hold", alpha=0.7)
        ax[0].set_yscale("log"); ax[0].legend(); ax[0].set_title(f"{sym} equity (log)")
        ax[1].fill_between(res.index, res["equity"] / res["equity"].cummax() - 1, 0,
                           color="tab:red", alpha=0.4)
        ax[1].set_title("Strategy drawdown")
        fig.tight_layout(); fig.savefig(os.path.join(config.REPORT_DIR, f"{sym}.png"), dpi=110)
        plt.close(fig)

        lines.append(f"\n=== {sym} ===")
        for k in m:
            fmt = "{:.2f}" if k == "夏普比率" else "{:.1%}"
            lines.append(f"  {k:<6} 策略 {fmt.format(m[k]):>9}   买入持有 {fmt.format(mb[k]):>9}")
        lines.append(f"  交易 {len(sells)} 次，胜率 {win:.0%}，持仓时间 {res['position'].mean():.0%}")

    table = pd.DataFrame(rows)
    table.to_csv(os.path.join(config.REPORT_DIR, "summary.csv"), index=False, encoding="utf-8-sig")
    text = "\n".join(lines)
    with open(os.path.join(config.REPORT_DIR, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\n报告和图表已保存到 {config.REPORT_DIR}/ 文件夹")


if __name__ == "__main__":
    main()
