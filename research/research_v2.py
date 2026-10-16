# -*- coding: utf-8 -*-
"""Noire 研究引擎 v2：在 A+（波动目标 30% + 趋势开关）基础上，系统地找“有没有更好的方案”

  python research/research_v2.py      读取 research/data/*.csv，输出 research/report_v2.md 和 research/equity_v2.png

搜索范围（事先列好，全部报告，不只挑好看的）：
  A. 现行 7 只 ETF：持有几只（前 2 / 前 3 / 全部趋势向上的）× 打分方法（1/3/6/12 平均 / 13612W / 除以波动）× 仓位分配（平均 / 波动小的多买）
  B. 扩大可选范围：加入中期国债、公司债、高收益债、房地产、大宗商品、行业 ETF 等，共 5 组
  C. 杠杆控制的其他做法：金丝雀信号、更激进的波动目标、只用趋势开关、每日 200 日线离场、分 4 批换仓、不借钱买现金，
     以及“稳健版”（分 4 批换仓 + 波动小的多买）
  D. 借钱利率敏感性（Alpaca 现在的保证金利率约 6.5%，比国库券高约 2.5 个百分点）
  E. 和简单择时（SPY/QQQ 在 200 日线上方才持有）比较
防止“碰巧好看”：单一换仓日的方案在 20 个换仓日、分批方案在 5 种分批方式下各算一遍取平均，并分前后两段检查。
只做研究，不连接券商、不交易。依赖 research.py 里的函数。
"""
import os
import math
import numpy as np
import pandas as pd
import research as r

HERE = r.HERE
DPM = r.DPM
U0 = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD"]
SECT = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
UNIVERSES = {
    "现行 7 只": U0,
    "多资产 12 只（+中期国债、公司债、高收益债、房地产、大宗商品）": U0 + ["IEF", "LQD", "HYG", "IYR", "DBC"],
    "Keller G12（另一套常见的 12 只）": ["SPY", "IWM", "QQQ", "VGK", "EWJ", "EEM", "VNQ", "DBC", "GLD", "TLT", "LQD", "HYG"],
    "现行 7 只 + 9 个行业": U0 + SECT,
    "多资产 12 只 + 9 个行业": U0 + ["IEF", "LQD", "HYG", "IYR", "DBC"] + SECT,
}
OFFSETS = (0, 3, 5, 8, 10, 13, 15)
START = "2000-01-03"


# ------------------------------------------------------------------ 数据
def load_universe(universe, cash="SHY"):
    o, c = {}, {}
    for s in universe + [cash]:
        df = pd.read_csv(os.path.join(r.DATA, f"{s}.csv"), index_col=0, parse_dates=True)
        o[s], c[s] = df["Open"], df["Close"]
    irx = pd.read_csv(os.path.join(r.DATA, "IRX.csv"), index_col=0, parse_dates=True)["Close"]
    o, c = pd.DataFrame(o).sort_index(), pd.DataFrame(c).sort_index()
    idx = c["SPY"].dropna().index
    o, c = o.reindex(idx), c.reindex(idx)
    irx = irx.reindex(idx).ffill().bfill() / 100.0
    ret = o.shift(-1) / o - 1
    bill = irx.shift(1).fillna(irx) / 252
    cash_r = ret[cash].where(c[cash].notna() & o[cash].notna(), bill).fillna(bill)
    R = ret[universe].copy()
    R["CASH"] = cash_r
    return c, irx, R.fillna(0.0)


# ------------------------------------------------------------------ 信号
def score(c, universe, kind="avg", lookbacks=(1, 3, 6, 12)):
    uni = c[universe]
    ret = {m: uni / uni.shift(m * DPM) - 1 for m in (1, 3, 6, 12)}
    if kind == "avg":            # 现行：1/3/6/12 个月收益平均
        s = sum(ret[m] for m in lookbacks) / len(lookbacks)
    elif kind == "w13612":       # Keller 13612W：更看重最近一个月
        s = (12 * ret[1] + 4 * ret[3] + 2 * ret[6] + ret[12]) / 4
    elif kind == "radj":         # 平均收益 ÷ 近半年波动（风险调整后的动量）
        s = sum(ret[m] for m in lookbacks) / len(lookbacks)
        s = s / (uni.pct_change(fill_method=None).rolling(126).std() * math.sqrt(252))
    else:
        raise ValueError(kind)
    return s.where(uni.notna().cumsum() > 13 * DPM)      # 上市满 13 个月才参与


def make_W(c, universe, sel=2, kind="avg", weight="equal", offset=0, vol_win=63):
    """sel=前 N 名，或 'all'（全部分数>0 的都持有）；weight='equal' 平均，'invvol' 波动小的多买。"""
    s_all = score(c, universe, kind)
    vol = c[universe].pct_change(fill_method=None).rolling(vol_win).std()
    cols = universe + ["CASH"]
    W = pd.DataFrame(np.nan, index=c.index, columns=cols)
    for d in r.month_signal_dates(c.index, offset):
        s = s_all.loc[d].dropna().sort_values(ascending=False)
        row = pd.Series(0.0, index=cols)
        if len(s) == 0:
            row["CASH"] = 1.0
            W.loc[d] = row
            continue
        picks = list(s.index) if sel == "all" else list(s.index[:sel])
        slots = len(picks) if sel == "all" else sel
        if weight == "equal":
            w = pd.Series(1.0 / slots, index=picks)
        else:
            iv = 1.0 / vol.loc[d, picks]
            iv = iv.fillna(iv.mean() if iv.notna().any() else 1.0)
            w = iv / iv.sum() * (len(picks) / slots)
        for sym in picks:
            row[sym if s[sym] > 0 else "CASH"] += w[sym]
        row["CASH"] += max(0.0, 1.0 - row.sum())
        W.loc[d] = row
    W = W.shift(1).ffill().fillna(0.0)                  # 收盘出信号 → 下一天开盘生效
    W.loc[W.sum(axis=1) == 0, "CASH"] = 1.0
    return W


def sma_exit(W, c, universe, n=200):
    """每天检查：持有的资产前一天收盘跌破自己的 200 日均线 → 这份改拿现金。"""
    px = c[universe]
    below = (px < px.rolling(n).mean()).astype(float).shift(1).fillna(0.0) > 0
    W2 = W.copy()
    for s in universe:
        m = below[s].reindex(W2.index).fillna(False).values & (W2[s].values > 0)
        W2.loc[m, "CASH"] += W2.loc[m, s]
        W2.loc[m, s] = 0.0
    return W2


def timing(c, universe, sym, n=200):
    """简单择时：前一天收盘在 200 日均线上方就持有 sym，否则拿现金。"""
    p = c[sym]
    above = (p > p.rolling(n).mean()).astype(float).shift(1).fillna(0.0) > 0
    W = pd.DataFrame(0.0, index=c.index, columns=universe + ["CASH"])
    W.loc[above, sym] = 1.0
    W.loc[~above, "CASH"] = 1.0
    return W


# ------------------------------------------------------------------ 杠杆
def lev_vol(W, R, target, cap=2.0, floor=1.0, win=63):
    Rv, Wv = R[list(W.columns)].values, W.values
    out = np.full(len(W), floor)
    for t in range(win, len(W)):
        sd = (Rv[t - win:t] @ Wv[t]).std(ddof=1) * math.sqrt(252)
        if sd > 0:
            out[t] = min(cap, max(floor, target / sd))
    return pd.Series(out, index=W.index)


def lowest(*series):
    return pd.concat(series, axis=1).min(axis=1)


def canary(c):
    """金丝雀（Keller DAA 的思路）：EEM 或中期国债 IEF 的 13612W 动量为负 → 杠杆最多 1 倍。"""
    ief = pd.read_csv(os.path.join(r.DATA, "IEF.csv"), index_col=0, parse_dates=True)["Close"].reindex(c.index)
    eem = pd.read_csv(os.path.join(r.DATA, "EEM.csv"), index_col=0, parse_dates=True)["Close"].reindex(c.index)

    def w13612(p):
        x = {m: p / p.shift(m * DPM) - 1 for m in (1, 3, 6, 12)}
        return (12 * x[1] + 4 * x[3] + 2 * x[6] + x[12]) / 4
    bad = ((w13612(eem) < 0) | (w13612(ief) < 0)).astype(float).shift(1).fillna(0.0) > 0
    return pd.Series(np.where(bad, 1.0, 2.0), index=c.index)


# ------------------------------------------------------------------ 模拟（与 research.simulate 相同，多两个选项）
def simulate(W, R, irx, lev=1.0, breaker=False, cost=r.COST, start=START, end=None, lev_path=None,
             spread=None, cash_no_borrow=False):
    """spread：借钱利率比国库券高多少；cash_no_borrow：不借钱去买现金（SHY）。"""
    spread = r.BORROW_SPREAD if spread is None else spread
    W = W.loc[start:end]
    R = R.loc[W.index]
    rate = (irx.shift(1).fillna(irx) + spread).loc[W.index].values / 252
    levs = lev_path.reindex(W.index).fillna(1.0).values if lev_path is not None else np.full(len(W), lev)
    Wv, Rv = W.values, R[list(W.columns)].values
    cash_i = list(W.columns).index("CASH")
    risky = np.ones(Wv.shape[1], dtype=bool)
    risky[cash_i] = False
    n = len(W)
    eq, lev_used = np.empty(n), np.empty(n)
    equity, peak, delev = 1.0, 1.0, False
    w = np.zeros(Wv.shape[1])
    for t in range(n):
        L = 1.0 if (breaker and delev) else levs[t]
        tgt = Wv[t].copy()
        tgt[risky] *= L
        if cash_no_borrow:
            tgt[cash_i] = min(tgt[cash_i], max(0.0, 1.0 - tgt[risky].sum()))
        equity *= 1 - np.abs(tgt - w).sum() * cost
        borrowed = max(tgt.sum() - 1.0, 0.0)
        rr = float(tgt @ Rv[t]) - borrowed * rate[t]
        equity *= 1 + rr
        w = tgt * (1 + Rv[t]) / (1 + rr) if (1 + rr) > 0 else np.zeros_like(tgt)
        peak = max(peak, equity)
        if breaker:
            dd = 1 - equity / peak
            if not delev and dd > r.DELEVER:
                delev = True
            elif delev and dd < r.RELEVER:
                delev = False
        eq[t], lev_used[t] = equity, L
        if equity <= 0:
            eq[t:] = 0
            break
    s = pd.Series(eq, index=W.index)
    on = Wv[:, risky].sum(axis=1) > 0
    s.attrs.update(avg_lev=float(lev_used[on].mean()) if on.any() else 1.0)
    return s


def m(eq, irx):
    x = r.metrics(eq, irx)
    x["平均杠杆"] = eq.attrs.get("avg_lev", 1.0)
    return x


# ------------------------------------------------------------------ 研究
def run():
    out = {}
    c, irx, R = load_universe(U0)
    trend = r.lev_trend(c)
    aplus = lambda W, cc=c, RR=R: lowest(lev_vol(W, RR, 0.30), r.lev_trend(cc).reindex(W.index))
    out["data_end"] = str(c.index[-1].date())

    # A. 现行 7 只：持有数量 × 打分 × 仓位分配（月末换仓）
    rows = []
    for sel in (2, 3, "all"):
        for kind, kname in (("avg", "1/3/6/12平均"), ("w13612", "13612W"), ("radj", "除以波动")):
            for wt, wname in (("equal", "平均分配"), ("invvol", "波动小的多买")):
                W = make_W(c, U0, sel, kind, wt)
                m1 = m(simulate(W, R, irx, lev=1.0), irx)
                lp = aplus(W)
                ma = m(simulate(W, R, irx, lev_path=lp), irx)
                h1 = m(simulate(W, R, irx, lev_path=lp, end="2012-12-31"), irx)
                h2 = m(simulate(W, R, irx, lev_path=lp, start="2013-01-01"), irx)
                rows.append({"持有": "全部向上的" if sel == "all" else f"前{sel}名", "打分": kname, "分配": wname,
                             "1倍年化": m1["年化"], "1倍夏普": m1["夏普"], "A+年化": ma["年化"], "A+最大回撤": ma["最大回撤"],
                             "A+最差一年": ma["最差一年"], "A+夏普": ma["夏普"],
                             "前半段年化(2000–12)": h1["年化"], "后半段年化(2013–)": h2["年化"]})
    out["A"] = pd.DataFrame(rows)

    # B. 扩大可选范围（月末换仓，A+ 杠杆控制）
    rows = []
    for uname, U in UNIVERSES.items():
        cu, irxu, Ru = load_universe(U)
        for sel in (2, 3):
            for wt, wname in (("equal", "平均分配"), ("invvol", "波动小的多买")):
                W = make_W(cu, U, sel, "avg", wt)
                m1 = m(simulate(W, Ru, irxu, lev=1.0), irxu)
                lp = aplus(W, cu, Ru)
                e = simulate(W, Ru, irxu, lev_path=lp)
                ma = m(e, irxu)
                h1 = m(simulate(W, Ru, irxu, lev_path=lp, end="2012-12-31"), irxu)
                h2 = m(simulate(W, Ru, irxu, lev_path=lp, start="2013-01-01"), irxu)
                rows.append({"可选范围": uname, "方案": f"前{sel}名·{wname}", "1倍年化": m1["年化"], "A+年化": ma["年化"],
                             "A+最大回撤": ma["最大回撤"], "A+夏普": ma["夏普"],
                             "前半段年化": h1["年化"], "后半段年化": h2["年化"],
                             "金融危机": r.window_ret(e, *r.CRISES["金融危机 2007-10~2009-03"]),
                             "2022年": r.window_ret(e, "2022-01-03", "2022-12-30")})
    out["B"] = pd.DataFrame(rows)

    # C + D. 杠杆控制的其他做法，并检验借钱利率。
    #   只在一个换仓日换仓的方案：在 20 个不同换仓日（月末、月末前第 1~19 个交易日）下各算一遍；
    #   分 4 批的方案：在 5 种不同分批方式（月末前第 p、p+5、p+10、p+15 个交易日，p = 0~4）下各算一遍。
    #   （2026-10-16 修正：之前用 7 个换仓日，分批方案实际只覆盖了 2 种分批方式，回撤被低估。）
    cn = canary(c)
    acc = {}

    def record(name, WW, kw):
        x = m(simulate(WW, R, irx, **kw), irx)
        h1 = m(simulate(WW, R, irx, end="2012-12-31", **kw), irx)
        h2 = m(simulate(WW, R, irx, start="2013-01-01", **kw), irx)
        c2 = m(simulate(WW, R, irx, cost=r.COST * 2, **kw), irx)
        sp = [m(simulate(WW, R, irx, spread=s, **kw), irx)["年化"] for s in (0.025, 0.035)]
        acc.setdefault(name, []).append([x["年化"], x["最大回撤"], x["最差一年"], x["夏普"], x["平均杠杆"],
                                         h1["年化"], h2["年化"], c2["年化"], sp[0], sp[1]])

    for k in range(20):
        W = make_W(c, U0, 2, "avg", "equal", k)
        Wi = make_W(c, U0, 2, "avg", "invvol", k)
        WX = sma_exit(W, c, U0)
        W3 = make_W(c, U0, 3, "radj", "invvol", k)
        v = {t: lev_vol(W, R, t) for t in (0.25, 0.30, 0.35, 0.40, 0.50)}
        tr = trend.reindex(W.index)
        single = {
            "1倍": (W, dict(lev=1.0)),
            "2倍+熔断（原来的）": (W, dict(lev=2.0, breaker=True)),
            "A：波动目标 25%": (W, dict(lev_path=v[0.25])),
            "A+：波动目标 30% + 趋势开关": (W, dict(lev_path=lowest(v[0.30], tr))),
            "A+ 不借钱买现金": (W, dict(lev_path=lowest(v[0.30], tr), cash_no_borrow=True)),
            "A+ 波动小的多买": (Wi, dict(lev_path=aplus(Wi))),
            "A+ 前 3 名·除以波动·波动小的多买": (W3, dict(lev_path=aplus(W3))),
            "A+ 每日跌破 200 日线就离场": (WX, dict(lev_path=aplus(WX))),
            "波动目标 30% + 金丝雀": (W, dict(lev_path=lowest(v[0.30], cn))),
            "波动目标 30% + 趋势 + 金丝雀": (W, dict(lev_path=lowest(v[0.30], tr, cn))),
            "波动目标 35% + 趋势": (W, dict(lev_path=lowest(v[0.35], tr))),
            "波动目标 40% + 趋势": (W, dict(lev_path=lowest(v[0.40], tr))),
            "波动目标 50% + 趋势": (W, dict(lev_path=lowest(v[0.50], tr))),
            "只用趋势开关（2 倍/1 倍）": (W, dict(lev_path=tr)),
        }
        for name, (WW, kw) in single.items():
            record(name, WW, kw)
    for p in range(5):
        ks = [p + 5 * j for j in range(4)]                      # 4 批，每批相隔 5 个交易日
        WT = sum(make_W(c, U0, 2, "avg", "equal", j) for j in ks) / 4
        WTi = sum(make_W(c, U0, 2, "avg", "invvol", j) for j in ks) / 4
        record("A+ 分 4 批换仓", WT, dict(lev_path=aplus(WT)))
        record("A+ 稳健版：分 4 批 + 波动小的多买（实盘）", WTi, dict(lev_path=aplus(WTi)))
    order = ["1倍", "2倍+熔断（原来的）", "A：波动目标 25%", "A+：波动目标 30% + 趋势开关", "A+ 不借钱买现金",
             "A+ 波动小的多买", "A+ 分 4 批换仓", "A+ 稳健版：分 4 批 + 波动小的多买（实盘）",
             "A+ 前 3 名·除以波动·波动小的多买", "A+ 每日跌破 200 日线就离场", "波动目标 30% + 金丝雀",
             "波动目标 30% + 趋势 + 金丝雀", "波动目标 35% + 趋势", "波动目标 40% + 趋势", "波动目标 50% + 趋势",
             "只用趋势开关（2 倍/1 倍）"]
    acc = {k: acc[k] for k in order}
    rows, sens = [], []
    for name, vals in acc.items():
        a = np.array(vals)
        rows.append({"方案": name, "平均年化": a[:, 0].mean(), "最低年化": a[:, 0].min(), "最高年化": a[:, 0].max(),
                     "平均最大回撤": a[:, 1].mean(), "最坏最大回撤": a[:, 1].min(), "平均最差一年": a[:, 2].mean(),
                     "夏普": a[:, 3].mean(), "平均杠杆": a[:, 4].mean(),
                     "前半段年化": a[:, 5].mean(), "后半段年化": a[:, 6].mean(), "成本翻倍年化": a[:, 7].mean()})
        sens.append({"方案": name, "利差 1.5%（基准）": a[:, 0].mean(), "利差 2.5%（≈Alpaca 现价）": a[:, 8].mean(),
                     "利差 3.5%": a[:, 9].mean()})
    out["C"] = pd.DataFrame(rows)
    out["D"] = pd.DataFrame(sens)

    # E. 和简单择时比较（月末换仓）
    rows = []
    W2 = make_W(c, U0, 2, "avg", "equal")
    for name, W in (("动量轮动前 2 名（现行）", W2), ("SPY 200 日线择时", timing(c, U0, "SPY")),
                    ("QQQ 200 日线择时", timing(c, U0, "QQQ")),
                    ("一半轮动 + 一半 SPY 择时", (W2 + timing(c, U0, "SPY")) / 2)):
        for lname, kw in (("1 倍", dict(lev=1.0)), ("A+", dict(lev_path=aplus(W)))):
            x = m(simulate(W, R, irx, **kw), irx)
            rows.append({"策略": name, "杠杆": lname, "年化": x["年化"], "最大回撤": x["最大回撤"],
                         "最差一年": x["最差一年"], "夏普": x["夏普"]})
    out["E"] = pd.DataFrame(rows)

    # 图：现行 vs A+ vs 1 倍 vs SPY
    spyW = pd.DataFrame(0.0, index=c.index, columns=U0 + ["CASH"])
    spyW["SPY"] = 1.0
    WS = sum(make_W(c, U0, 2, "avg", "invvol", j) for j in (0, 5, 10, 15)) / 4
    out["curves"] = {"SPY buy & hold": simulate(spyW, R, irx, lev=1.0),
                     "Momentum TOP2 1x": simulate(W2, R, irx, lev=1.0),
                     "2x + breaker (live now)": simulate(W2, R, irx, lev=2.0, breaker=True),
                     "A+ steady: 4 tranches + inverse-vol": simulate(WS, R, irx, lev_path=aplus(WS))}
    band = []
    for k in range(20):                                 # A+ 在 20 个换仓日下的净值，画成一条“运气范围”带
        Wk = make_W(c, U0, 2, "avg", "equal", k)
        band.append(simulate(Wk, R, irx, lev_path=aplus(Wk)))
    out["band"] = pd.concat(band, axis=1)
    return out


# ------------------------------------------------------------------ 报告
def charts(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    b = out["band"]
    ax[0].fill_between(b.index, b.min(axis=1), b.max(axis=1), color="tab:red", alpha=0.25,
                       label="A+ (range over 20 different rebalance days)")
    colors = {"SPY buy & hold": "tab:blue", "Momentum TOP2 1x": "tab:orange",
              "2x + breaker (live now)": "tab:green", "A+ steady: 4 tranches + inverse-vol": "tab:purple"}
    for lab, eq in out["curves"].items():
        ax[0].plot(eq, label=lab, color=colors.get(lab))
        ax[1].plot(eq / eq.cummax() - 1, label=lab, color=colors.get(lab))
    bdd = (b / b.cummax() - 1).min(axis=1)
    ax[1].fill_between(b.index, bdd, 0, color="tab:red", alpha=0.15)
    ax[0].set_yscale("log")
    ax[0].legend()
    ax[0].set_title("Growth of $1 since 2000 (log scale)")
    ax[1].set_title("Drawdown (red shade: worst of the 20 A+ variants)")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "equity_v2.png"), dpi=110)


def write_report(out):
    pct_all = lambda df: [col for col in df.columns if any(k in col for k in ("年化", "回撤", "最差", "危机", "2022", "利差"))]
    num = lambda df: [col for col in df.columns if col in ("夏普", "1倍夏普", "A+夏普", "平均杠杆")]
    L = [f"# Noire 研究报告 v2：有没有比 A+ 更好的方案（数据到 {out['data_end']}）", "",
         "> 只做研究，不改实盘参数。所有数字是历史模拟（2000 年 1 月起），不代表未来。",
         "> A+ = 动量轮动前 2 名，杠杆 = min(30% ÷ 当前持仓近 63 天波动, 趋势开关)，限制在 1~2 倍；趋势开关：SPY 前一天收盘在 200 日均线上方才允许超过 1 倍。",
         "> 成本：单边 0.10%；借钱利率 = 13 周国库券利率 + 1.5%（另见 D 节）。", ""]
    notes = os.path.join(HERE, "notes_v2.md")
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            L += [f.read().strip(), "", "---", "", "# 附：全部数据", ""]
    L += ["## A. 现行 7 只 ETF：持有几只 × 怎么打分 × 怎么分配（月末换仓，2000 至今）", "",
          r.table(out["A"], pct_all(out["A"]), num(out["A"])), ""]
    L += ["## B. 扩大可选范围（月末换仓，A+ 杠杆控制，2000 至今；新 ETF 上市满 13 个月后才参与）", "",
          r.table(out["B"], pct_all(out["B"]), num(out["B"])), ""]
    L += ["## C. 杠杆控制的其他做法（前 2 名，2000 至今）", "",
          "只在一个换仓日换仓的方案：在 20 个不同换仓日（月末、月末前第 1~19 个交易日）下各算一遍；"
          "分 4 批的方案：在 5 种分批方式（月末前第 p、p+5、p+10、p+15 个交易日，p=0~4）下各算一遍。"
          "表里是平均值，“最低/最高年化”是其中最差和最好的那个；差得越多，说明运气成分越大。", "",
          r.table(out["C"], pct_all(out["C"]), num(out["C"])), ""]
    L += ["## D. 借钱利率敏感性（平均年化，算法同 C 节）", "",
          "Alpaca 公布的保证金利率：普通用户 6.50%、Elite 5.00%（按 360 天计息）；现在 13 周国库券约 4.1%，所以利差约 2.5 个百分点。"
          "模拟盘可能不收利息，所以模拟盘成绩会比真钱好一点。", "",
          r.table(out["D"], pct_all(out["D"]), num(out["D"])), ""]
    L += ["## E. 和简单择时比较（月末换仓，2000 至今）", "",
          r.table(out["E"], pct_all(out["E"]), num(out["E"])), ""]
    L += ["![净值与回撤](equity_v2.png)", ""]
    return "\n".join(L)


if __name__ == "__main__":
    out = run()
    charts(out)
    text = write_report(out)
    with open(os.path.join(HERE, "report_v2.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text[:4000])
