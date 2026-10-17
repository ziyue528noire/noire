# -*- coding: utf-8 -*-
"""Noire 研究引擎 v1：长周期回测 + 熔断模拟 + 样本外检验

  python research/research.py          读取 research/data/*.csv，输出 research/report.md 和图表

与 rotation.py 的区别（更严格）：
  - 标的池随时间变化：ETF 上市满 12 个月+1 个月后才参与打分（避免“用到还不存在的 ETF”）
  - 现金：SHY 可用时用 SHY，否则按 13 周国库券利率（^IRX）计息
  - 借钱成本：国库券利率 + 1.5%（券商利差），按日计
  - 每日按目标杠杆再平衡，漂移产生的换手也计成本
  - 熔断：从最高点回撤 >20% 降到 1 倍，回撤 <10% 恢复（与 risk.py 相同）
  - 追缴保证金：净值/持仓 < 30% 记为一次“追缴”
  - 样本外（walk-forward）：每年只用过去 5 年选参数，用在下一年
  - 杠杆控制方式对比（熔断 / 趋势开关 / 波动目标）和换仓日运气检验
  - research/notes.md（人写的结论）若存在，会放在报告开头
只做研究，不连接券商、不交易。
"""
import os
import math
import itertools
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD"]
CASH = "SHY"
DPM = 21                      # 每月交易日
COST = 0.0010                 # 单边手续费+滑点（0.05%+0.05%），与实盘配置一致
BORROW_SPREAD = 0.015         # 借款利率 = 国库券利率 + 1.5%
MAINT = 0.30                  # 维持保证金比例
DELEVER, RELEVER = 0.20, 0.10
CRISES = {"互联网泡沫破裂 2000-03~2002-10": ("2000-03-24", "2002-10-09"),
          "金融危机 2007-10~2009-03": ("2007-10-09", "2009-03-09"),
          "新冠暴跌 2020-02~03": ("2020-02-19", "2020-03-23"),
          "2022 熊市 2022-01~10": ("2022-01-03", "2022-10-12")}


# ------------------------------------------------------------------ 数据
def load():
    o, c = {}, {}
    for s in UNIVERSE + [CASH]:
        df = pd.read_csv(os.path.join(DATA, f"{s}.csv"), index_col=0, parse_dates=True)
        o[s], c[s] = df["Open"], df["Close"]
    irx = pd.read_csv(os.path.join(DATA, "IRX.csv"), index_col=0, parse_dates=True)["Close"]
    o, c = pd.DataFrame(o).sort_index(), pd.DataFrame(c).sort_index()
    idx = c["SPY"].dropna().index
    o, c = o.reindex(idx), c.reindex(idx)
    irx = irx.reindex(idx).ffill().bfill() / 100.0          # 年化，小数
    return o, c, irx


# ------------------------------------------------------------------ 信号
def month_signal_dates(index, offset=0):
    """每月最后一个交易日（offset>0 表示提前 offset 个交易日，用来检验“换仓日运气”）。"""
    s = pd.Series(index, index=index)
    out = []
    for _, g in s.groupby(index.to_period("M")):
        out.append(g.iloc[max(len(g) - 1 - offset, 0)])
    return pd.DatetimeIndex(out[:-1])        # 最后一个月可能未结束，丢掉


def targets(c, top_n, lookbacks, offset=0):
    """返回每天开盘生效的“基础目标权重”（未加杠杆），以及各信号日的选择。"""
    uni = c[UNIVERSE]
    score = sum(uni / uni.shift(m * DPM) - 1 for m in lookbacks) / len(lookbacks)
    hist_ok = uni.notna().cumsum() > (max(lookbacks) * DPM + DPM)   # 上市满回看期+1个月
    score = score.where(hist_ok)
    cols = UNIVERSE + ["CASH"]
    W = pd.DataFrame(np.nan, index=c.index, columns=cols)
    picks = {}
    for d in month_signal_dates(c.index, offset):
        s = score.loc[d].dropna().sort_values(ascending=False)
        row = pd.Series(0.0, index=cols)
        chosen = []
        for sym in s.index[:top_n]:
            if s[sym] > 0:
                row[sym] += 1.0 / top_n
                chosen.append(sym)
            else:
                row["CASH"] += 1.0 / top_n
        if len(s) < top_n:                      # 可选标的不够 → 剩余份额拿现金
            row["CASH"] += (top_n - len(s)) / top_n
        W.loc[d] = row
        picks[d] = chosen or ["现金"]
    W = W.shift(1).ffill().fillna(0.0)          # 信号日收盘决定 → 下一天开盘生效
    W.loc[W.sum(axis=1) == 0, "CASH"] = 1.0
    return W, picks


# ------------------------------------------------------------------ 模拟
def daily_returns(o, c, irx):
    """开盘→下一天开盘的收益；CASH = SHY（若可用）否则国库券利息。"""
    R = (o.shift(-1) / o - 1)
    cash = R[CASH].copy()
    bill = irx.shift(1).fillna(irx) / 252
    cash = cash.where(c[CASH].notna() & o[CASH].notna(), bill).fillna(bill)
    R = R[UNIVERSE].copy()
    R["CASH"] = cash
    return R.fillna(0.0)


def simulate(W, R, irx, lev=1.0, breaker=False, cost=COST, start=None, end=None, reset_days=None,
             lev_path=None):
    """reset_days：熔断持续满这么多个交易日后，把“最高点”重置为当前净值并恢复杠杆（对比用）。
    lev_path：每天的杠杆（pd.Series，只能用前一天收盘前已知的信息），给了就代替固定的 lev。"""
    W = W.loc[start:end]
    R = R.loc[W.index]
    rate = (irx.shift(1).fillna(irx) + BORROW_SPREAD).loc[W.index].values / 252
    levs = lev_path.reindex(W.index).fillna(1.0).values if lev_path is not None else np.full(len(W), lev)
    Wv, Rv = W.values, R.values
    cash_i = list(W.columns).index("CASH")
    n = len(W)
    eq = np.empty(n)
    lev_used = np.empty(n)
    equity, peak, delev, delev_len = 1.0, 1.0, False, 0
    w = np.zeros(Wv.shape[1])
    margin_calls, breaker_days = 0, 0
    for t in range(n):
        L = 1.0 if (breaker and delev) else levs[t]
        tgt = Wv[t].copy()
        risky = np.ones_like(tgt, dtype=bool)
        risky[cash_i] = False
        tgt[risky] *= L
        equity *= 1 - np.abs(tgt - w).sum() * cost
        borrowed = max(tgt.sum() - 1.0, 0.0)
        r = float(tgt @ Rv[t]) - borrowed * rate[t]
        equity *= 1 + r
        w = tgt * (1 + Rv[t]) / (1 + r) if (1 + r) > 0 else np.zeros_like(tgt)
        gross = w[risky].sum()
        if gross > 1 and 1 / gross < MAINT:
            margin_calls += 1
        peak = max(peak, equity)
        dd = 1 - equity / peak
        if breaker:
            if not delev and dd > DELEVER:
                delev, delev_len = True, 0
            elif delev and dd < RELEVER:
                delev = False
            elif delev and reset_days and delev_len >= reset_days:
                delev, peak = False, equity          # 熔断满期：以当前净值为新起点
            delev_len += delev
            breaker_days += delev
        eq[t] = equity
        lev_used[t] = L
        if equity <= 0:
            eq[t:] = 0
            break
    s = pd.Series(eq, index=W.index)
    risky_on = Wv[:, [i for i in range(Wv.shape[1]) if i != cash_i]].sum(axis=1) > 0
    avg_lev = float(lev_used[risky_on].mean()) if risky_on.any() else 1.0
    s.attrs.update(margin_calls=margin_calls, breaker_days=breaker_days, avg_lev=avg_lev)
    return s


def lev_trend(c, hi=2.0, lo=1.0, n=200):
    """趋势开关：前一天收盘 SPY 在 200 日均线上方 → hi 倍，否则 lo 倍。"""
    spy = c["SPY"]
    above = (spy > spy.rolling(n).mean()).astype(float).shift(1).fillna(0.0) > 0
    return pd.Series(np.where(above, hi, lo), index=c.index)


def lev_vol(W, R, target, cap=2.0, floor=1.0, win=63):
    """波动目标：杠杆 = 目标波动 / 当前持仓组合近 3 个月（63 天）的实际波动，限制在 floor~cap 之间。
    第 t 天只用到 t 之前已经发生的收益（不偷看未来）。"""
    Rv, Wv = R[list(W.columns)].values, W.values
    out = np.full(len(W), floor)
    for t in range(win, len(W)):
        sd = (Rv[t - win:t] @ Wv[t]).std(ddof=1) * math.sqrt(252)
        if sd > 0:
            out[t] = min(cap, max(floor, target / sd))
    return pd.Series(out, index=W.index)


# ------------------------------------------------------------------ 指标
def metrics(eq, irx):
    eq = eq[eq > 0]
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    ex = r - (irx.shift(1).reindex(r.index).fillna(0) / 252)
    sharpe = ex.mean() / r.std() * math.sqrt(252) if r.std() > 0 else np.nan
    dd = eq / eq.cummax() - 1
    under, longest = 0, 0
    for v in dd.values:
        under = under + 1 if v < 0 else 0
        longest = max(longest, under)
    yearly = pd.concat([eq.iloc[:1], eq.resample("YE").last()]).pct_change().iloc[1:]
    return {"年化": cagr, "波动": r.std() * math.sqrt(252), "夏普": sharpe, "最大回撤": dd.min(),
            "最差一年": yearly.min(), "最长套牢(月)": longest / DPM,
            "追缴次数": eq.attrs.get("margin_calls", 0), "熔断天数": eq.attrs.get("breaker_days", 0)}


def window_ret(eq, a, b):
    s = eq.loc[a:b]
    return s.iloc[-1] / s.iloc[0] - 1 if len(s) > 1 else np.nan


def fmt_pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.{d}%}" if x < 0 or x > 0 else f"{x:.{d}%}"


# ------------------------------------------------------------------ 研究
def run():
    o, c, irx = load()
    R = daily_returns(o, c, irx)
    spyW = pd.DataFrame(0.0, index=c.index, columns=UNIVERSE + ["CASH"])
    spyW["SPY"] = 1.0
    full_start = "2000-01-03"
    first_full = c[UNIVERSE].apply(lambda s: s.first_valid_index()).max()   # 全部 ETF 都已上市
    uni_start = (first_full + pd.Timedelta(days=400)).strftime("%Y-%m-%d")    # 再等满 13 个月

    base_lb = [1, 3, 6, 12]
    out = {"meta": {"data_start": str(c.index[0].date()), "data_end": str(c.index[-1].date()),
                    "first_full": str(first_full.date()), "uni_start": uni_start,
                    "inception": {s: str(c[s].first_valid_index().date()) for s in UNIVERSE + [CASH]}}}

    # 1) 主网格：TOP1/2/3 × 1/1.5/2 倍 × 熔断开/关 × 两个区间
    rows, curves = [], {}
    for period, start in (("2000至今", full_start), ("全标的可用后", uni_start)):
        bh = simulate(spyW, R, irx, 1.0, start=start)
        bh2 = simulate(spyW, R, irx, 2.0, start=start)
        bh2b = simulate(spyW, R, irx, 2.0, breaker=True, start=start)
        for name, eq in (("SPY 一直持有", bh), ("SPY 2倍", bh2), ("SPY 2倍+熔断", bh2b)):
            rows.append({"区间": period, "方案": name, **metrics(eq, irx),
                         **{k: window_ret(eq, *v) for k, v in CRISES.items()}})
            curves[(period, name)] = eq
        for top_n in (1, 2, 3):
            W, _ = targets(c, top_n, base_lb)
            for lev, brk, rst in ((1.0, False, None), (1.5, False, None), (1.5, True, None),
                                  (2.0, False, None), (2.0, True, None), (2.0, True, 126)):
                eq = simulate(W, R, irx, lev, breaker=brk, start=start, reset_days=rst)
                name = f"轮动TOP{top_n} {lev:g}倍" + ("+熔断" if brk else "") + ("(6个月重置)" if rst else "")
                rows.append({"区间": period, "方案": name, **metrics(eq, irx),
                             **{k: window_ret(eq, *v) for k, v in CRISES.items()}})
                curves[(period, name)] = eq
    out["grid"] = pd.DataFrame(rows)

    # 2) 分时期（当前方案 TOP2 2倍+熔断，以及 1 倍，和 SPY）
    W2, picks2 = targets(c, 2, base_lb)
    sub = []
    for a, b, label in (("2000-01-03", "2009-12-31", "2000–2009"), ("2010-01-01", "2019-12-31", "2010–2019"),
                        ("2020-01-01", None, "2020–至今")):
        for name, eq in (("SPY 一直持有", simulate(spyW, R, irx, 1.0, start=a, end=b)),
                         ("轮动TOP2 1倍", simulate(W2, R, irx, 1.0, start=a, end=b)),
                         ("轮动TOP2 2倍+熔断(现行)", simulate(W2, R, irx, 2.0, breaker=True, start=a, end=b))):
            m = metrics(eq, irx)
            sub.append({"时期": label, "方案": name, "年化": m["年化"], "最大回撤": m["最大回撤"], "夏普": m["夏普"]})
    out["sub"] = pd.DataFrame(sub)

    # 3) 稳健性：成本、换仓日、回看期
    rob = []
    cur = lambda eq: metrics(eq, irx)
    for label, eq in (
        ("基准：TOP2 1倍，月末换仓，回看1/3/6/12", simulate(W2, R, irx, 1.0, start=uni_start)),
        ("成本翻倍", simulate(W2, R, irx, 1.0, cost=COST * 2, start=uni_start)),
        ("成本×4", simulate(W2, R, irx, 1.0, cost=COST * 4, start=uni_start)),
        ("月中换仓（提前10个交易日）", simulate(targets(c, 2, base_lb, offset=10)[0], R, irx, 1.0, start=uni_start)),
        ("回看 3/6/12", simulate(targets(c, 2, [3, 6, 12])[0], R, irx, 1.0, start=uni_start)),
        ("回看 6/12", simulate(targets(c, 2, [6, 12])[0], R, irx, 1.0, start=uni_start)),
        ("回看 12", simulate(targets(c, 2, [12])[0], R, irx, 1.0, start=uni_start)),
        ("SPY 一直持有", simulate(spyW, R, irx, 1.0, start=uni_start)),
    ):
        m = cur(eq)
        rob.append({"变体": label, "年化": m["年化"], "最大回撤": m["最大回撤"], "夏普": m["夏普"]})
    out["robust"] = pd.DataFrame(rob)

    # 4) 样本外（walk-forward）：每年用过去 5 年夏普最高的参数，用到下一年（1 倍，不加熔断，检验信号本身）
    combos = list(itertools.product((1, 2, 3), ([1, 3, 6, 12], [3, 6, 12], [6, 12], [12])))
    daily = {}
    for tn, lb in combos:
        eq = simulate(targets(c, tn, lb)[0], R, irx, 1.0, start=full_start)
        daily[f"TOP{tn} 回看{'/'.join(map(str, lb))}"] = eq.pct_change().fillna(0)
    D = pd.DataFrame(daily)
    years = sorted(set(D.index.year))
    wf, chosen = [], []
    for y in years:
        ins = D[(D.index.year >= y - 5) & (D.index.year < y)]
        if ins.index.year.nunique() < 5:
            continue
        sh = ins.mean() / ins.std()
        best = sh.idxmax()
        oos = D.loc[D.index.year == y, best]
        wf.append(oos)
        chosen.append({"年份": y, "选中参数": best})
    wf = pd.concat(wf)
    wf_eq = (1 + wf).cumprod()
    fixed_eq = (1 + D.loc[wf.index, "TOP2 回看1/3/6/12"]).cumprod()
    spy_eq = simulate(spyW, R, irx, 1.0, start=str(wf.index[0].date()))
    wf_rows = []
    for name, eq in (("样本外自动选参数", wf_eq), ("固定参数 TOP2 回看1/3/6/12（现行）", fixed_eq), ("SPY 一直持有", spy_eq)):
        m = metrics(eq, irx)
        wf_rows.append({"方案": name, "年化": m["年化"], "最大回撤": m["最大回撤"], "夏普": m["夏普"]})
    out["wf"] = pd.DataFrame(wf_rows)
    out["wf_choices"] = pd.DataFrame(chosen)
    out["wf_range"] = (str(wf.index[0].date()), str(wf.index[-1].date()))
    # 全区间参数稳定性（1 倍）
    stab = []
    for key, r in daily.items():
        r = r.loc[uni_start:]
        eq = (1 + r).cumprod()
        m = metrics(eq, irx)
        stab.append({"参数": key, "年化": m["年化"], "夏普": m["夏普"], "最大回撤": m["最大回撤"]})
    out["stab"] = pd.DataFrame(stab)

    # 5) 杠杆控制方式对比（TOP2，上限 2 倍）：现行熔断 vs 趋势开关 vs 波动目标
    trend = lev_trend(c)
    vols = {t: lev_vol(W2, R, t) for t in (0.18, 0.20, 0.25, 0.30)}
    variants = [("1倍", dict(lev=1.0)), ("1.5倍", dict(lev=1.5)), ("2倍（无熔断）", dict(lev=2.0)),
                ("2倍+熔断（现行）", dict(lev=2.0, breaker=True)),
                ("2倍+熔断（6个月重置）", dict(lev=2.0, breaker=True, reset_days=126)),
                ("趋势开关：SPY 在200日线上2倍，否则1倍", dict(lev_path=trend))]
    variants += [(f"波动目标 {int(t * 100)}%（1~2倍）", dict(lev_path=v)) for t, v in vols.items()]
    variants += [("波动目标 25% + 熔断", dict(lev_path=vols[0.25], breaker=True))]
    both = lambda v: pd.concat([v, trend.reindex(v.index)], axis=1).min(axis=1)   # 两个条件取较低的杠杆
    variants += [(f"波动目标 {int(t * 100)}% + 趋势开关", dict(lev_path=both(vols[t]))) for t in (0.25, 0.30)]
    alt = []
    for period, start in (("2000至今", full_start), ("全标的可用后", uni_start)):
        for name, kw in variants:
            eq = simulate(W2, R, irx, start=start, **kw)
            m = metrics(eq, irx)
            m10 = metrics(simulate(W2, R, irx, start="2010-01-01", end="2019-12-31", **kw), irx)
            alt.append({"区间": period, "方案": name, "年化": m["年化"], "最大回撤": m["最大回撤"], "夏普": m["夏普"],
                        "最差一年": m["最差一年"], "平均杠杆": eq.attrs["avg_lev"], "2010–2019年化": m10["年化"],
                        **{k: window_ret(eq, *v) for k, v in CRISES.items()}})
    out["alt"] = pd.DataFrame(alt)

    # 6) 换仓日运气：同一策略在月末前 0~15 个交易日换仓的差别，以及“分 4 批换仓”
    luck = []
    for tn in (2, 3):
        Ws = {k: targets(c, tn, base_lb, offset=k)[0] for k in (0, 3, 5, 8, 10, 13, 15)}
        WT = sum(Ws[k] for k in (0, 5, 10, 15)) / 4
        for lev in (1.0, 2.0):
            row = {"方案": f"TOP{tn} {lev:g}倍"}
            vals = []
            for k, W in Ws.items():
                v = metrics(simulate(W, R, irx, lev, start=uni_start), irx)["年化"]
                row["月末" if k == 0 else f"提前{k}天"] = v
                vals.append(v)
            row["平均"] = float(np.mean(vals))
            row["分4批换仓"] = metrics(simulate(WT, R, irx, lev, start=uni_start), irx)["年化"]
            luck.append(row)
    out["luck"] = pd.DataFrame(luck)

    # 7) 主要方案在 7 个不同换仓日下的表现（2000至今）：看改进是不是靠运气
    schemes = {"1倍": lambda W: dict(lev=1.0), "2倍+熔断（现行）": lambda W: dict(lev=2.0, breaker=True),
               "波动目标 25%": lambda W: dict(lev_path=lev_vol(W, R, 0.25)),
               "波动目标 30% + 趋势开关": lambda W: dict(lev_path=both(lev_vol(W, R, 0.30)))}
    acc = {k: [] for k in schemes}
    for k in (0, 3, 5, 8, 10, 13, 15):
        W = targets(c, 2, base_lb, offset=k)[0]
        for name, kw in schemes.items():
            acc[name].append(metrics(simulate(W, R, irx, start=full_start, **kw(W)), irx))
    out["luck2"] = pd.DataFrame([{"方案": n, "平均年化": np.mean([m["年化"] for m in ms]),
                                  "最低年化": min(m["年化"] for m in ms),
                                  "平均最大回撤": np.mean([m["最大回撤"] for m in ms]),
                                  "最坏最大回撤": min(m["最大回撤"] for m in ms),
                                  "平均最差一年": np.mean([m["最差一年"] for m in ms])} for n, ms in acc.items()])
    out["curves"] = curves
    out["picks2"] = picks2
    return out, irx


# ------------------------------------------------------------------ 报告
def table(df, pct_cols, num_cols=(), int_cols=()):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for col in cols:
            v = r[col]
            if col in pct_cols:
                cells.append("—" if pd.isna(v) else f"{v:.1%}")
            elif col in num_cols:
                cells.append("—" if pd.isna(v) else f"{v:.2f}")
            elif col in int_cols:
                cells.append("—" if pd.isna(v) else f"{int(v)}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def charts(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    period = "2000至今"
    names = [("SPY 一直持有", "SPY buy & hold"), ("轮动TOP2 1倍", "Momentum TOP2 1x"),
             ("轮动TOP2 2倍", "Momentum TOP2 2x (no breaker)"), ("轮动TOP2 2倍+熔断", "Momentum TOP2 2x + breaker (live)")]
    fig, ax = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    for key, lab in names:
        eq = out["curves"][(period, key)]
        ax[0].plot(eq, label=lab)
        ax[1].plot(eq / eq.cummax() - 1, label=lab)
    ax[0].set_yscale("log"); ax[0].legend(); ax[0].set_title("Growth of $1 since 2000 (log scale)")
    ax[1].set_title("Drawdown"); ax[1].axhline(-0.2, color="gray", lw=0.6, ls="--")
    fig.tight_layout()
    p = os.path.join(HERE, "equity_2000.png")
    fig.savefig(p, dpi=110)
    return p


def write_report(out, irx):
    g = out["grid"]
    pct = ["年化", "波动", "最大回撤", "最差一年"] + list(CRISES)
    keep = ["方案", "年化", "最大回撤", "夏普", "最差一年", "最长套牢(月)", "追缴次数", "熔断天数"]
    crisis_cols = ["方案"] + list(CRISES)
    meta = out["meta"]
    L = [f"# Noire 研究报告 v1：长周期回测（数据 {meta['data_start']} ~ {meta['data_end']}）", "",
         "> 只做研究，不改实盘参数。所有数字是历史模拟，不代表未来。",
         f"> 各 ETF 上市日：" + "，".join(f"{k} {v}" for k, v in meta["inception"].items()),
         f"> “2000至今”区间里，早期只有 SPY（以及之后上市的 QQQ、IWM…）可选，其余时间拿现金；"
         f"“全标的可用后”从 {meta['uni_start']} 开始，7 只 ETF 都能参与。", "",
         "成本假设：单边 0.10%；借款利率 = 13 周国库券利率 + 1.5%；熔断 = 回撤 >20% 降到 1 倍、<10% 恢复；"
         "追缴 = 净值/持仓 < 30%。", ""]
    notes = os.path.join(HERE, "notes.md")          # 人写的结论与建议（数字更新后要人工复核）
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            L += [f.read().strip(), "", "---", "", "# 附：全部数据", ""]
    for period in ("2000至今", "全标的可用后"):
        d = g[g["区间"] == period]
        L += [f"## 1. 总表（{period}）", "", table(d[keep], pct, ["夏普", "最长套牢(月)"], ["追缴次数", "熔断天数"]), ""]
    d = g[g["区间"] == "2000至今"]
    L += ["## 2. 几次大跌期间的表现（2000至今区间）", "", table(d[crisis_cols], list(CRISES)), ""]
    L += ["## 3. 分时期（现行方案 vs 1 倍 vs SPY）", "", table(out["sub"], ["年化", "最大回撤"], ["夏普"]), ""]
    L += [f"## 4. 稳健性检验（{meta['uni_start']} 至今，1 倍）", "", table(out["robust"], ["年化", "最大回撤"], ["夏普"]), ""]
    L += [f"## 5. 样本外检验（walk-forward，{out['wf_range'][0]} ~ {out['wf_range'][1]}，1 倍）", "",
          "每年年初只用过去 5 年的数据，在 12 组参数（TOP1/2/3 × 4 种回看期）里挑夏普最高的，用在这一年。", "",
          table(out["wf"], ["年化", "最大回撤"], ["夏普"]), "",
          "每年选中的参数：" + "；".join(f"{r['年份']} {r['选中参数']}" for _, r in out["wf_choices"].iterrows()), ""]
    st = out["stab"].sort_values("夏普", ascending=False)
    L += [f"## 6. 参数稳定性（{meta['uni_start']} 至今，1 倍）", "", table(st, ["年化", "最大回撤"], ["夏普"]), ""]
    a = out["alt"]
    acols = ["方案", "年化", "最大回撤", "夏普", "最差一年", "平均杠杆", "2010–2019年化"]
    apct = ["年化", "最大回撤", "最差一年", "2010–2019年化"]
    L += ["## 7. 杠杆怎么控制（TOP2，杠杆上限 2 倍）", "",
          "“熔断”是跌了 20% 之后才降杠杆；“趋势开关”看 SPY 是否在 200 日均线上方；“波动目标”是市场越动荡杠杆越低"
          "（杠杆 = 目标波动 ÷ 当前持仓近 3 个月的实际波动，限制在 1~2 倍）。都只用当时已知的数据。平均杠杆只算持有风险资产的日子。", ""]
    for period in ("2000至今", "全标的可用后"):
        L += [f"### {period}", "", table(a[a["区间"] == period][acols], apct, ["夏普", "平均杠杆"]), ""]
    L += ["### 大跌期间（2000至今）", "", table(a[a["区间"] == "2000至今"][["方案"] + list(CRISES)], list(CRISES)), ""]
    lk = out["luck"]
    L += [f"## 8. 换仓日运气（{meta['uni_start']} 至今，年化收益）", "",
          "同一策略，只是换仓日不同（月末、或提前 N 个交易日）。差别越大，说明历史成绩里“运气”成分越多。"
          "“分4批换仓”= 资金分 4 份，分别在月末及提前 5/10/15 天换仓。", "",
          table(lk, [col for col in lk.columns if col != "方案"]), ""]
    l2 = out["luck2"]
    L += ["## 9. 主要方案在 7 个不同换仓日下的表现（TOP2，2000至今）", "",
          "月末及提前 3/5/8/10/13/15 个交易日换仓，各算一遍。改进如果在每个换仓日都成立，才不是运气。", "",
          table(l2, [col for col in l2.columns if col != "方案"]), ""]
    L += ["![净值与回撤](equity_2000.png)", ""]
    return "\n".join(L)


if __name__ == "__main__":
    out, irx = run()
    charts(out)
    text = write_report(out, irx)
    with open(os.path.join(HERE, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    out["grid"].to_csv(os.path.join(HERE, "grid.csv"), index=False, encoding="utf-8-sig")
    print(text[:3000])
