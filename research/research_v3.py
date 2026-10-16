# -*- coding: utf-8 -*-
"""Noire 研究 v3：找“第二条腿”——和动量轮动关系不大的策略

  python research/research_v3.py      读取 research/data/*.csv，输出 research/report_v3.md

事先定好的规则（全部来自公开文献的常用参数，不在数据上调参）：
  S1 RSI(2) 超跌反弹：SPY 收盘在 200 日均线上方、且 2 日 RSI < 10 → 买；收盘高于 5 日均线 → 卖（Connors）。
  S2 IBS 反弹：SPY 在 200 日均线上方、当天收盘位于当日最高最低价区间的下 20%（IBS<0.2）→ 买；IBS>0.8 → 卖。
  S3 多 ETF 超跌：SPY/QQQ/IWM/EFA/EEM 中，在各自 200 日均线上方、过去 5 天跌得最多（且跌了）的那只 → 买入持有 5 天。
  S4 月初月末效应：每月倒数第 4 个交易日收盘买 SPY，下月第 3 个交易日收盘卖。
执行：信号在收盘时确定。主版本按“下一个交易日开盘成交”（现有系统就能做）；参考版本按“当天收盘成交”（需要在收盘前几分钟下单的新程序）。
成本：单边 0.10%。设计期 2000–2012，检验期 2013 至今（主要看检验期）。
和 A+ 稳健版合并：券商隔夜杠杆上限 2 倍，所以第二条腿只能用 A+ 没用完的额度（2 倍 − A+ 当天杠杆），看合并后是否更好。
只做研究，不连券商、不交易。
"""
import os
import numpy as np
import pandas as pd
import research as r
import research_v2 as v
import expectations as ex

HERE = r.HERE
COST = 0.0010
EQ = ["SPY", "QQQ", "IWM", "EFA", "EEM"]


def load(sym):
    df = pd.read_csv(os.path.join(r.DATA, f"{sym}.csv"), index_col=0, parse_dates=True)
    return df[["Open", "High", "Low", "Close"]].dropna()


def rsi2(c):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def positions_from_signals(entry, exit_):
    """收盘时的信号 → 每天收盘后的持仓（1 = 持有）。"""
    pos, on = [], False
    for e, x in zip(entry.values, exit_.values):
        if on and x:
            on = False
        elif not on and e:
            on = True
        pos.append(1.0 if on else 0.0)
    return pd.Series(pos, index=entry.index)


def returns_next_open(pos, df):
    """收盘信号 → 下一天开盘成交：第 t 天的收益 = 第 t 天开盘到 t+1 天开盘，持仓 = 前一天收盘后的信号。"""
    oo = df["Open"].shift(-1) / df["Open"] - 1
    held = pos.shift(1).fillna(0.0)
    turn = held.diff().abs().fillna(held)
    return (held * oo - turn * COST).fillna(0.0)


def returns_same_close(pos, df):
    """收盘信号 → 当天收盘成交（参考）：第 t 天收益 = 收盘到收盘，持仓 = 前一天收盘的信号。"""
    cc = df["Close"].pct_change()
    held = pos.shift(1).fillna(0.0)
    turn = pos.diff().abs().fillna(pos)
    return (held * cc - turn.shift(1).fillna(0.0) * COST).fillna(0.0)


def s1_rsi2(df):
    c = df["Close"]
    sma200, sma5 = c.rolling(200).mean(), c.rolling(5).mean()
    return positions_from_signals((c > sma200) & (rsi2(c) < 10), c > sma5)


def s2_ibs(df):
    c, h, l_ = df["Close"], df["High"], df["Low"]
    ibs = ((c - l_) / (h - l_).replace(0, np.nan)).fillna(0.5)
    return positions_from_signals((c > c.rolling(200).mean()) & (ibs < 0.2), ibs > 0.8)


def s3_multi(dfs):
    """每天收盘：在 200 日线上方、5 日跌幅最大（且为负）的那只，买入持有 5 个交易日（一次只持有一只）。"""
    closes = pd.DataFrame({s: d["Close"] for s, d in dfs.items()}).dropna()
    r5 = closes / closes.shift(5) - 1
    above = closes > closes.rolling(200).mean()
    cand = r5.where(above & (r5 < 0))
    pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    hold, left = None, 0
    for i, d in enumerate(closes.index):
        if left > 0:
            pos.loc[d, hold] = 1.0
            left -= 1
            continue
        row = cand.loc[d].dropna()
        if len(row):
            hold, left = row.idxmin(), 4
            pos.loc[d, hold] = 1.0
    return pos


def s4_tom(df):
    c = df["Close"]
    idx = c.index
    month = idx.to_period("M")
    pos = pd.Series(0.0, index=idx)
    groups = pd.Series(range(len(idx)), index=idx).groupby(month)
    starts = {m: g.iloc[0] for m, g in groups}
    ends = {m: g.iloc[-1] for m, g in groups}
    months = sorted(starts)
    for a, b in zip(months[:-1], months[1:]):
        i0 = max(ends[a] - 3, starts[a])            # 本月倒数第 4 个交易日收盘买入
        i1 = min(starts[b] + 2, ends[b])            # 下月第 3 个交易日收盘卖出
        pos.iloc[i0:i1] = 1.0
    return pos


def stats(ret, irx, start=None, end=None):
    x = ret.loc[start:end]
    eq = (1 + x).cumprod()
    m = r.metrics(eq, irx)
    return m


def aplus_base(phase, c, irx, R, R2):
    """实盘算法的 A+ 稳健版（分批方式 phase）：返回 (每日收益, 风险资产总仓位)。"""
    W = sum(v.make_W(c, v.U0, 2, "avg", "invvol", phase + 5 * j) for j in range(4)) / 4
    lev = v.lowest(v.lev_vol(W, R2, 0.30), r.lev_trend(c).reindex(W.index))
    eq = v.simulate(W, R, irx, lev_path=lev, cash_no_borrow=True, spread=ex.SPREAD)
    a = eq.pct_change().fillna(0.0)
    return a, (W[v.U0].sum(axis=1) * lev).reindex(a.index).fillna(0.0)


def combine(a, gross, rno, expo, irx):
    """第二条腿只用 A+ 没用完的额度（总仓位不超过 2 倍）；用到借来的钱时按国库券 + 2.5% 付利息。"""
    spare = (2.0 - gross).clip(lower=0.0, upper=1.0)
    used = spare * expo
    borrowed = (used - (1.0 - gross).clip(lower=0.0)).clip(lower=0.0)
    rate = (irx.shift(1).reindex(a.index).ffill() + ex.SPREAD) / 252
    return a + spare * rno - borrowed * rate


def other_markets(fn=None):
    """同一条 RSI(2) 规则用在其他美股 ETF 上（不改参数），看是不是只对 SPY 有效。"""
    rows = []
    for sym in ("SPY", "QQQ", "IWM", "MDY", "DIA", "EFA"):
        df = load(sym)
        pos = s1_rsi2(df)
        held = pos.shift(1).fillna(0.0)
        oo = df["Open"]
        t, trades = None, []
        for i in range(1, len(held)):
            if held.iloc[i] == 1 and held.iloc[i - 1] == 0:
                t = i
            if held.iloc[i] == 0 and held.iloc[i - 1] == 1 and t is not None:
                trades.append((held.index[t], oo.iloc[i] / oo.iloc[t] - 1 - 2 * COST))
                t = None
        tr = pd.DataFrame(trades, columns=["entry", "ret"])
        a, b = tr[tr.entry < "2013-01-01"], tr[tr.entry >= "2013-01-01"]
        rows.append({"ETF": sym, "每年交易次数": len(tr) / ((df.index[-1] - df.index[200]).days / 365.25),
                     "设计期 平均每笔": a.ret.mean(), "设计期 胜率": (a.ret > 0).mean(),
                     "检验期 平均每笔": b.ret.mean(), "检验期 胜率": (b.ret > 0).mean(), "最差一笔": tr.ret.min()})
    return pd.DataFrame(rows)


def main():
    spy = load("SPY")
    dfs = {s: load(s) for s in EQ}
    c, irx, R = v.load_universe(v.U0)
    cc = c[v.U0 + ["SHY"]].pct_change(fill_method=None)
    R2 = cc[v.U0].copy(); R2["CASH"] = cc["SHY"].fillna(irx.shift(1) / 252); R2 = R2.fillna(0.0)
    bases = [aplus_base(p, c, irx, R, R2) for p in range(5)]
    a0 = bases[0][0]

    strategies = {}
    for name, fn in (("S1 RSI(2) 超跌反弹（SPY）", s1_rsi2), ("S2 IBS 反弹（SPY）", s2_ibs), ("S4 月初月末效应（SPY）", s4_tom)):
        pos = fn(spy)
        strategies[name] = (returns_next_open(pos, spy), returns_same_close(pos, spy), pos)
    p3 = s3_multi(dfs)
    strategies["S3 多 ETF 超跌（5 只股票 ETF）"] = (
        sum(returns_next_open(p3[s], dfs[s].reindex(p3.index)) for s in EQ),
        sum(returns_same_close(p3[s], dfs[s].reindex(p3.index)) for s in EQ), p3.sum(axis=1))

    rows, combo = [], []
    for name, (rno, rsc, pos) in strategies.items():
        rno, rsc = rno.reindex(a0.index).fillna(0.0), rsc.reindex(a0.index).fillna(0.0)
        expo = pos.reindex(a0.index).fillna(0.0).shift(1).fillna(0.0)
        for label, rr in (("次日开盘成交", rno), ("当天收盘成交（参考）", rsc)):
            row = {"策略": name, "成交方式": label}
            for per, a, b in (("设计期 2000–12", "2000-01-03", "2012-12-31"), ("检验期 2013–", "2013-01-01", None)):
                mm = stats(rr, irx, a, b)
                row[f"{per} 年化"] = mm["年化"]
                row[f"{per} 最大回撤"] = mm["最大回撤"]
            e2 = expo.loc["2013":]
            row["检验期 持仓天数占比"] = float((e2 > 0).mean())
            inv = rr.loc["2013":][e2 > 0]
            row["检验期 持仓时年化"] = float((1 + inv).prod() ** (252 / max(len(inv), 1)) - 1) if len(inv) else np.nan
            row["和 A+ 的相关性"] = float(rr.loc["2013":].corr(a0.loc["2013":]))
            rows.append(row)
        for per, a, b in (("全期", "2000-01-03", None), ("设计期 2000–12", "2000-01-03", "2012-12-31"), ("检验期 2013–", "2013-01-01", None)):
            m0 = [stats(ar, irx, a, b) for ar, _ in bases]
            m1 = [stats(combine(ar, g, rno, expo, irx), irx, a, b) for ar, g in bases]
            combo.append({"第二条腿": name, "区间": per,
                          "只用 A+ 年化": np.mean([x["年化"] for x in m0]), "合并后年化": np.mean([x["年化"] for x in m1]),
                          "只用 A+ 最大回撤": np.mean([x["最大回撤"] for x in m0]), "合并后最大回撤": np.mean([x["最大回撤"] for x in m1]),
                          "只用 A+ 夏普": np.mean([x["夏普"] for x in m0]), "合并后夏普": np.mean([x["夏普"] for x in m1]),
                          "5 种分批里合并后更好的次数": int(sum(y["年化"] > x["年化"] for x, y in zip(m0, m1)))})
    out, cb, om = pd.DataFrame(rows), pd.DataFrame(combo), other_markets()
    gross_mean = float(np.mean([g.loc["2000":].mean() for _, g in bases]))
    spare_share = float(np.mean([((2 - g).clip(0, 1) >= 0.5).mean() for _, g in bases]))
    write_report(out, cb, om, gross_mean, spare_share, a0.index[-1].date())
    pd.set_option("display.width", 250)
    print(out.round(3).to_string()); print(cb.round(3).to_string()); print(om.round(4).to_string())


def write_report(out, cb, om, gross_mean, spare_share, end):
    pct_cols = lambda df: [col for col in df.columns if any(k in col for k in ("年化", "回撤", "占比", "每笔", "胜率", "最差"))]
    L = [f"# Noire 研究报告 v3：第二条腿（数据到 {end}）", "",
         "> 只做研究，不改实盘。规则全部事先定好、参数来自公开文献，没有在数据上调；设计期 2000–2012，检验期 2013 至今。",
         "> 成本单边 0.10%；第二条腿只用 A+ 稳健版没用完的额度（总仓位不超过 2 倍），借钱按国库券 + 2.5%。"
         "A+ 稳健版按实盘算法，5 种分批方式取平均。", ""]
    notes = os.path.join(HERE, "notes_v3.md")
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            L += [f.read().strip(), "", "---", "", "# 附：全部数据", ""]
    L += ["## 1. 单独看每个策略（满仓资金计，大部分时间空仓）", "", r.table(out, pct_cols(out), ["和 A+ 的相关性"]), "",
          "## 2. 和 A+ 稳健版合在一起（只用剩余额度）", "",
          f"A+ 稳健版平均占用 {gross_mean:.2f} 倍仓位；剩余额度 ≥ 0.5 倍的日子占 {spare_share:.0%}。", "",
          r.table(cb, pct_cols(cb), ["只用 A+ 夏普", "合并后夏普"], ["5 种分批里合并后更好的次数"]), "",
          "## 3. 同一条 RSI(2) 规则换到其他 ETF（不改参数）", "",
          "每笔收益已扣来回 0.20% 成本，按次日开盘买入、卖出信号后的次日开盘卖出。", "",
          r.table(om, pct_cols(om), ["每年交易次数"]), ""]
    with open(os.path.join(HERE, "report_v3.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
