# -*- coding: utf-8 -*-
"""Noire 研究 v4：上线前后的“细节”检查（2026-10-16，用户要求“再仔细钻研有什么可以改或优化的地方”）

  python research/research_v4.py      读取 research/data/*.csv，输出 research/report_v4.md

事先定好的问题和判断标准（在看结果之前写下，不看完结果再改）：
  A. 实盘执行和研究的差距：研究假设每天精确调到目标、可以买零股、不留现金；实盘是“相差超过总资产 5% 才调”、
     整股、留 2% 现金、单日亏损超过 5% 当天只卖不买。用同一套目标仓位按实盘规则逐笔模拟（10 万美元起步），
     看年化 / 回撤 / 换手差多少。目的是知道“实盘大概会比研究少多少”，不据此调参数
     （只有当 5% 比 2.5% 和 10% 都差 0.3 个点以上时，才建议换）。
  B. 第二条腿扩展到 QQQ、IWM（同一条 RSI(2) 规则，不改参数）：三只各自判断，剩余额度在“正在发信号的几只”之间平分。
     判断标准（全部满足才建议改）：合并后年化在设计期（2000–2012）和检验期（2013 至今）都提高；
     5 种分批方式里两段都至少 4 种更好；检验期最大回撤变差不超过 1 个点、设计期不超过 2 个点。
  C. 第二条腿改成“当天收盘成交”（收盘前几分钟按接近收盘的价格判断、用收盘竞价单）：能多赚多少。只报告，
     因为需要多一个下午的定时任务和新的下单方式（要用户决定）；而且研究用的是真实收盘价，实际只能用收盘前的价格，会略差。
  D. 避险资产用 BIL（1–3 个月国库券）代替 SHY（1–3 年国债）：BIL 2007 年上市后比较，标准同 B（两段、5 种分批）。
  E. 压力测试：历史上最差的一天 / 一周 / 一个月（A+ 稳健版 + 第二条腿）；假想单日暴跌在不同仓位下会亏多少、会不会被追缴保证金。
  F. 借钱利率：国库券 + 1.5% / 2.5% / 3.5%（以后上真钱时比较券商用）。
  G. （A 节之后补充、在看 G 的结果之前写下）趋势开关来回翻转：SPY 在 200 日均线附近上下穿时，杠杆会在 1 倍和 2 倍之间
     来回跳（2000 年以来 184 次，其中 133 次 10 个交易日内又翻回来），每次都要买卖约 1 倍本金。事先定两种常见的“缓冲”做法：
       G1 两天确认：连续 2 个收盘都在均线另一边才切换；
       G2 1% 缓冲带：收盘高于均线 1% 才打开、低于均线 1% 才关闭，中间保持原来的状态。
     判断标准同 B（两段都提高、5 种分批方式两段都至少 4 种更好、回撤变差不超过 1 / 2 个点），
     而且两种都达标才建议（说明效果不依赖具体做法），届时采用更简单的 G1。
  H. （只供参考，不是建议）杠杆上限 1 倍 / 1.5 倍 / 2 倍时的收益和风险，给以后决定“上真钱用多少杠杆”用。
成本单边 0.10%（研究默认，偏保守）；A 节另报 0.03%（ETF 实际点差 + 开盘竞价的大致成本）。借钱默认国库券 + 2.5%（Alpaca）。
只做研究，不连券商、不交易。依赖 research.py / research_v2.py / research_v3.py / expectations.py。
"""
import os
import numpy as np
import pandas as pd
import research as r
import research_v2 as v
import research_v3 as s3
import expectations as ex

HERE = r.HERE
U0 = v.U0
COST = 0.0010
SPREAD = ex.SPREAD
PERIODS = (("全期 2000–", "2000-01-03", None), ("设计期 2000–12", "2000-01-03", "2012-12-31"), ("检验期 2013–", "2013-01-01", None))


# ------------------------------------------------------------------ 数据和目标仓位
def load_all():
    c, irx, R = v.load_universe(U0)
    o = {}
    for s in U0 + ["SHY", "BIL"]:
        o[s] = pd.read_csv(os.path.join(r.DATA, f"{s}.csv"), index_col=0, parse_dates=True)["Open"]
    o = pd.DataFrame(o).reindex(c.index)
    cc = c[U0 + ["SHY"]].pct_change(fill_method=None)
    R2 = cc[U0].copy()
    R2["CASH"] = cc["SHY"].fillna(irx.shift(1) / 252)
    return c, o, irx, R, R2.fillna(0.0)


def trend_state(c, kind="plain", n=200):
    """趋势开关（第 t 行 = 第 t 天开盘生效的杠杆上限：2 或 1）。
    plain = 现在的做法；confirm2 = 连续 2 个收盘在另一边才切换；band1 = 高于均线 1% 才开、低于 1% 才关。"""
    if kind == "plain":
        return r.lev_trend(c, n=n)
    spy = c["SPY"]
    sma = spy.rolling(n).mean()
    above = (spy > sma).values
    ratio = (spy / sma).values
    on, out = False, []
    for i in range(len(spy)):
        if np.isnan(ratio[i]):
            on = False
        elif kind == "confirm2":
            if i >= 1 and not np.isnan(ratio[i - 1]):
                if on and not above[i] and not above[i - 1]:
                    on = False
                elif not on and above[i] and above[i - 1]:
                    on = True
        elif kind == "band1":
            if on and ratio[i] < 0.99:
                on = False
            elif not on and ratio[i] > 1.01:
                on = True
        out.append(2.0 if on else 1.0)
    return pd.Series(out, index=spy.index).shift(1).fillna(1.0)


def aplus_targets(c, R2, phase, trend="plain", cap=2.0):
    """实盘算法的 A+ 稳健版目标（占本金，已含杠杆；第 t 行 = 第 t 天开盘生效、前一天收盘决定）。"""
    W = sum(v.make_W(c, U0, 2, "avg", "invvol", phase + 5 * j) for j in range(4)) / 4
    tr = trend_state(c, trend) if cap == 2.0 else r.lev_trend(c, hi=cap, lo=1.0)
    lev = v.lowest(v.lev_vol(W, R2, 0.30, cap=cap), tr.reindex(W.index))
    T = W[U0].mul(lev, axis=0)
    gross = T.sum(axis=1)
    T["CASH"] = np.minimum(W["CASH"], (1.0 - gross).clip(lower=0.0))     # 不借钱买现金
    return T, gross


def dip_held(syms, idx):
    """各只的 RSI(2) 持仓（第 t 行 = 第 t 天开盘时拿着，即前一天收盘的信号）。"""
    out = {}
    for s in syms:
        out[s] = s3.s1_rsi2(s3.load(s)).reindex(idx).fillna(0.0).shift(1).fillna(0.0)
    return pd.DataFrame(out)


def add_dip(T, gross, held, cap=2.0):
    """第二条腿：剩余额度 min(1, 上限 − A+ 仓位) 在正在发信号的几只之间平分；先用 SHY 那部分自有资金。"""
    T = T.copy()
    spare = (cap - gross).clip(0.0, 1.0)
    n = held.sum(axis=1)
    used = pd.Series(0.0, index=T.index)
    for s in held.columns:
        a = pd.Series(np.where(n > 0, spare * held[s] / n.where(n > 0, 1.0), 0.0), index=T.index)
        T[s] = T[s] + a
        used += a
    T["CASH"] = np.minimum(T["CASH"], (1.0 - gross - used).clip(lower=0.0))
    return T


def ideal(T, R, irx, spread=SPREAD, cost=COST):
    """研究的理想执行：每天开盘精确调到目标、可买零股、不留现金（= research_v2.simulate）。"""
    return v.simulate(T, R, irx, lev=1.0, spread=spread, cost=cost)


def live_like(T, R, o, c, irx, band=0.05, buffer=0.02, whole=True, cost=COST, spread=SPREAD,
              loss_rule=True, capital=100_000.0, start="2000-01-03"):
    """按实盘规则逐笔模拟：相差超过总资产 band 才调、整股、留 buffer 现金、卖出不超过持有、
    单日亏损超过 5% 第二天开盘只卖不买；借钱按日计息。返回“每天开盘后（含当天开盘到下一天开盘）”的净值序列和换手。"""
    T = T.loc[start:]
    idx = T.index
    cols = list(T.columns)
    px = pd.DataFrame(index=idx)
    for s in cols:
        px[s] = o[s].reindex(idx) if s != "CASH" else (1 + R["CASH"].reindex(idx)).cumprod().shift(1).fillna(1.0) * 50
    cl = pd.DataFrame(index=idx)
    for s in cols:   # 收盘价（只用于“单日亏损”判断）；CASH 近似用开盘价序列
        cl[s] = c[s].reindex(idx) if s != "CASH" else px[s]
    P, CL, TT = px.values, cl.values, T.values
    rate = ((irx.shift(1).fillna(irx) + spread) / 252).reindex(idx).values
    shares = np.zeros(len(cols))
    cash = capital
    eq_out, turn = np.full(len(idx), np.nan), 0.0
    prev_close_eq, last_close_eq = None, None
    for t in range(len(idx) - 1):
        p = np.nan_to_num(P[t], nan=0.0)
        equity = cash + float(shares @ p)
        no_buy = (loss_rule and prev_close_eq and last_close_eq and last_close_eq / prev_close_eq - 1 < -0.05)
        tgt = TT[t] * (1 - buffer)
        for j in range(len(cols)):
            if p[j] <= 0:
                continue
            now_v = shares[j] * p[j]
            diff = tgt[j] * equity - now_v
            if tgt[j] <= 1e-12 and shares[j] > 0:
                q = -shares[j]
            elif abs(diff) > band * equity:
                q = diff / p[j]
                q = float(np.trunc(q)) if whole else q
                if q < 0:
                    q = max(q, -shares[j])
            else:
                q = 0.0
            if q > 0 and no_buy:
                q = 0.0
            if q != 0:
                shares[j] += q
                cash -= q * p[j] + abs(q) * p[j] * cost
                turn += abs(q) * p[j] / max(equity, 1e-9)
        if cash < 0:
            cash -= -cash * rate[t]                     # 借钱利息（到下一天开盘）
        cpx = np.nan_to_num(CL[t], nan=0.0)
        prev_close_eq, last_close_eq = last_close_eq, cash + float(shares @ cpx)
        eq_out[t] = cash + float(shares @ np.nan_to_num(P[t + 1], nan=0.0))
    s = pd.Series(eq_out, index=idx).dropna() / capital
    s.attrs["turnover_per_year"] = turn / (len(idx) / 252)
    return s


def m(eq, irx, a=None, b=None):
    x = eq.loc[a:b]
    x = x / x.iloc[0]
    return r.metrics(x, irx)


def phases_table(builders, R, irx, label_col="方案"):
    """builders: {名字: 函数(phase) -> 净值序列}。返回各区间 5 种分批方式平均的指标和“比第一个方案好”的次数。"""
    rows, base = [], None
    res = {name: [fn(p) for p in range(5)] for name, fn in builders.items()}
    first = list(res)[0]
    for name, eqs in res.items():
        for per, a, b in PERIODS:
            ms = [m(e, irx, a, b) for e in eqs]
            bs = [m(e, irx, a, b) for e in res[first]]
            rows.append({label_col: name, "区间": per,
                         "年化": np.mean([x["年化"] for x in ms]), "最大回撤": np.mean([x["最大回撤"] for x in ms]),
                         "最坏的最大回撤": np.min([x["最大回撤"] for x in ms]), "夏普": np.mean([x["夏普"] for x in ms]),
                         "比第一行好的分批方式数": int(sum(y["年化"] > x["年化"] + 1e-12 for x, y in zip(bs, ms))) if name != first else None})
    return pd.DataFrame(rows), res


# ------------------------------------------------------------------ 各节
def section_a(c, o, irx, R, R2):
    held = dip_held(["SPY"], c.index)
    rows = []
    for p in range(5):
        T, g = aplus_targets(c, R2, p)
        T = add_dip(T, g, held)
        e0 = ideal(T, R, irx)
        variants = {"研究的理想执行（每天精确调仓、零股、不留现金）": e0,
                    "+ 留 2% 现金": live_like(T, R, o, c, irx, band=0.0, whole=False, loss_rule=False),
                    "+ 整股（10 万美元起步）": live_like(T, R, o, c, irx, band=0.0, whole=True, loss_rule=False),
                    "+ 相差 5% 才调（= 实盘规则）": live_like(T, R, o, c, irx, band=0.05, loss_rule=False),
                    "+ 单日亏 5% 只卖不买（= 实盘全部规则）": live_like(T, R, o, c, irx, band=0.05),
                    "实盘规则，但相差 2.5% 就调": live_like(T, R, o, c, irx, band=0.025),
                    "实盘规则，但相差 10% 才调": live_like(T, R, o, c, irx, band=0.10),
                    "实盘全部规则，成本按 0.03%": live_like(T, R, o, c, irx, band=0.05, cost=0.0003),
                    "研究的理想执行，成本按 0.03%": ideal(T, R, irx, cost=0.0003)}
        for name, e in variants.items():
            for per, a, b in PERIODS:
                x = m(e, irx, a, b)
                rows.append({"执行方式": name, "分批": p, "区间": per, "年化": x["年化"], "最大回撤": x["最大回撤"],
                             "每年换手（倍）": e.attrs.get("turnover_per_year", np.nan)})
    df = pd.DataFrame(rows)
    out = df.groupby(["执行方式", "区间"], sort=False).agg(年化=("年化", "mean"), 最大回撤=("最大回撤", "mean"),
                                                        最坏的最大回撤=("最大回撤", "min"),
                                                        每年换手=("每年换手（倍）", "mean")).reset_index()
    return out


def section_b(c, irx, R, R2):
    hs = {"只用 SPY（现在）": dip_held(["SPY"], c.index),
          "SPY + QQQ + IWM（平分剩余额度）": dip_held(["SPY", "QQQ", "IWM"], c.index)}
    builders = {"只用 A+ 稳健版": lambda p: ideal(aplus_targets(c, R2, p)[0], R, irx)}
    for name, h in hs.items():
        builders[f"A+ + 第二条腿：{name}"] = (lambda h: (lambda p: ideal(add_dip(*aplus_targets(c, R2, p), h), R, irx)))(h)
    tab, res = phases_table(builders, R, irx)
    # 判断标准：和“只用 SPY”比
    keys = list(res)
    one, three = res[keys[1]], res[keys[2]]
    verdict = {}
    for per, a, b in PERIODS[1:]:
        m1 = [m(e, irx, a, b) for e in one]
        m3 = [m(e, irx, a, b) for e in three]
        verdict[per] = {"年化差": np.mean([y["年化"] - x["年化"] for x, y in zip(m1, m3)]),
                        "更好的分批方式数": int(sum(y["年化"] > x["年化"] for x, y in zip(m1, m3))),
                        "最大回撤差": np.mean([y["最大回撤"] - x["最大回撤"] for x, y in zip(m1, m3)])}
    ok = (verdict["设计期 2000–12"]["年化差"] > 0 and verdict["检验期 2013–"]["年化差"] > 0
          and verdict["设计期 2000–12"]["更好的分批方式数"] >= 4 and verdict["检验期 2013–"]["更好的分批方式数"] >= 4
          and verdict["检验期 2013–"]["最大回撤差"] >= -0.01 and verdict["设计期 2000–12"]["最大回撤差"] >= -0.02)
    held3 = hs["SPY + QQQ + IWM（平分剩余额度）"]
    share = {"只用 SPY 时有仓位的日子": float((hs["只用 SPY（现在）"].loc["2000":].sum(axis=1) > 0).mean()),
             "三只时有仓位的日子": float((held3.loc["2000":].sum(axis=1) > 0).mean())}
    return tab, verdict, ok, share


def section_c(c, irx, R, R2):
    spy = s3.load("SPY")
    pos = s3.s1_rsi2(spy)
    rno = s3.returns_next_open(pos, spy)
    rsc = s3.returns_same_close(pos, spy)
    rows = []
    for p in range(5):
        a, g = s3.aplus_base(p, c, irx, R, R2)
        expo = pos.reindex(a.index).fillna(0.0).shift(1).fillna(0.0)
        for name, rr in (("次日开盘成交（现在）", rno), ("当天收盘成交（参考）", rsc)):
            comb = s3.combine(a, g, rr.reindex(a.index).fillna(0.0), expo, irx)
            eq = (1 + comb).cumprod()
            for per, a0, b0 in PERIODS:
                x = m(eq, irx, a0, b0)
                rows.append({"第二条腿成交方式": name, "分批": p, "区间": per, "年化": x["年化"], "最大回撤": x["最大回撤"]})
    df = pd.DataFrame(rows)
    return df.groupby(["第二条腿成交方式", "区间"], sort=False).agg(年化=("年化", "mean"), 最大回撤=("最大回撤", "mean")).reset_index()


def section_d(c, irx, R, R2):
    bil = pd.read_csv(os.path.join(r.DATA, "BIL.csv"), index_col=0, parse_dates=True)
    o_b = bil["Open"].reindex(c.index)
    bill = irx.shift(1).fillna(irx) / 252
    rb = (o_b.shift(-1) / o_b - 1)
    RB = R.copy()
    RB["CASH"] = rb.where(o_b.notna() & o_b.shift(-1).notna(), R["CASH"]).fillna(R["CASH"])
    # 只在 BIL 上市以后比较（之前两边一样）
    cc = c[U0].pct_change(fill_method=None)
    R2B = cc.copy()
    R2B["CASH"] = bil["Close"].reindex(c.index).pct_change(fill_method=None).fillna(irx.shift(1) / 252)
    R2B = R2B.fillna(0.0)
    held = dip_held(["SPY"], c.index)
    builders = {"SHY（现在）": lambda p: ideal(add_dip(*aplus_targets(c, R2, p), held), R, irx),
                "BIL": lambda p: ideal(add_dip(*aplus_targets(c, R2B, p), held), RB, irx)}
    rows = []
    res = {k: [f(p) for p in range(5)] for k, f in builders.items()}
    for per, a, b in (("BIL 上市后 2007-06–2012", "2007-06-01", "2012-12-31"), ("检验期 2013–", "2013-01-01", None),
                      ("2022 年（加息、债券大跌）", "2022-01-01", "2022-12-31")):
        for k, eqs in res.items():
            ms = [m(e, irx, a, b) for e in eqs]
            rows.append({"避险资产": k, "区间": per, "年化": np.mean([x["年化"] for x in ms]),
                         "最大回撤": np.mean([x["最大回撤"] for x in ms]),
                         "比 SHY 好的分批方式数": int(sum(y["年化"] > x["年化"] for x, y in
                                                     zip([m(e, irx, a, b) for e in res["SHY（现在）"]], ms))) if k == "BIL" else None})
    # 避险资产平均占多少仓位
    T0, _ = aplus_targets(c, R2, 0)
    share = float(T0.loc["2007-06-01":, "CASH"].mean())
    return pd.DataFrame(rows), share


def section_e(c, irx, R, R2):
    held = dip_held(["SPY"], c.index)
    rets, grosses = [], []
    for p in range(5):
        T, g = aplus_targets(c, R2, p)
        T2 = add_dip(T, g, held)
        e = ideal(T2, R, irx)
        rets.append(e.pct_change().dropna())
        grosses.append(T2[U0].sum(axis=1).loc["2000":])
    out = {}
    for name, n in (("一天", 1), ("一周（5 天）", 5), ("一个月（21 天）", 21)):
        worst = []
        for rr in rets:
            w = (1 + rr).rolling(n).apply(np.prod, raw=True) - 1
            worst.append((float(w.min()), w.idxmin().date()))
        out[name] = (np.mean([x for x, _ in worst]), min(worst)[0], min(worst)[1])
    gross_all = pd.concat(grosses)
    exposure = {"平均总仓位": float(gross_all.mean()),
                "总仓位 ≥ 1.9 倍的日子": float((gross_all >= 1.9).mean()),
                "总仓位 ≤ 1 倍的日子": float((gross_all <= 1.0 + 1e-9).mean())}
    crash = []
    for L in (1.0, 1.5, 1.96):
        for x in (0.10, 0.20, 0.25, 0.30):
            eq_after = 1 - L * x
            lmv = L * (1 - x)
            ratio = eq_after / lmv if lmv > 0 else 0
            crash.append({"总仓位": L, "单日暴跌": x, "账户亏损": L * x,
                          "会被追缴保证金吗（维持保证金 30%）": "会" if ratio < 0.30 else "不会",
                          "离追缴还差": max(0.0, (1 - 0.30 * L) / (L * 0.70) - x) if L > 1 else None})
    return out, exposure, pd.DataFrame(crash)


def section_f(c, irx, R, R2):
    held = dip_held(["SPY"], c.index)
    rows = []
    for sp in (0.015, 0.025, 0.035):
        eqs = [ideal(add_dip(*aplus_targets(c, R2, p), held), R, irx, spread=sp) for p in range(5)]
        for per, a, b in PERIODS[:1]:
            ms = [m(e, irx, a, b) for e in eqs]
            rows.append({"借钱利率": f"国库券 + {sp:.1%}", "年化": np.mean([x["年化"] for x in ms]),
                         "最大回撤": np.mean([x["最大回撤"] for x in ms])})
    T, g = aplus_targets(c, R2, 0)
    T2 = add_dip(T, g, held)
    borrowed = (T2.sum(axis=1) - 1).clip(lower=0).loc["2000":].mean()
    return pd.DataFrame(rows), float(borrowed)


def section_g(c, o, irx, R, R2):
    held = dip_held(["SPY"], c.index)
    builders = {}
    for kind, name in (("plain", "现在：收盘一穿过均线就切换"), ("confirm2", "G1 两天确认"), ("band1", "G2 1% 缓冲带")):
        builders[name] = (lambda kind: (lambda p: ideal(add_dip(*aplus_targets(c, R2, p, kind), held), R, irx)))(kind)
    tab, res = phases_table(builders, R, irx)
    keys = list(res)
    verdict, ok_all = {}, True
    for k in keys[1:]:
        vd = {}
        for per, a, b in PERIODS[1:]:
            m0 = [m(e, irx, a, b) for e in res[keys[0]]]
            m1 = [m(e, irx, a, b) for e in res[k]]
            vd[per] = {"年化差": np.mean([y["年化"] - x["年化"] for x, y in zip(m0, m1)]),
                       "更好的分批方式数": int(sum(y["年化"] > x["年化"] for x, y in zip(m0, m1))),
                       "最大回撤差": np.mean([y["最大回撤"] - x["最大回撤"] for x, y in zip(m0, m1)])}
        ok = (vd["设计期 2000–12"]["年化差"] > 0 and vd["检验期 2013–"]["年化差"] > 0
              and vd["设计期 2000–12"]["更好的分批方式数"] >= 4 and vd["检验期 2013–"]["更好的分批方式数"] >= 4
              and vd["检验期 2013–"]["最大回撤差"] >= -0.01 and vd["设计期 2000–12"]["最大回撤差"] >= -0.02)
        vd["达标"] = ok
        ok_all = ok_all and ok
        verdict[k] = vd
    flips = {}
    for kind, name in (("plain", keys[0]), ("confirm2", keys[1]), ("band1", keys[2])):
        tr = trend_state(c, kind).loc["2000":]
        flips[name] = float((tr.diff().abs() > 0).sum() / (len(tr) / 252))
    # 按实盘规则（5% 才调、整股、2% 现金、单日亏损规则）再看一遍，避免“只在理想执行下有效”
    live = {}
    for kind, name in (("plain", keys[0]), ("confirm2", keys[1]), ("band1", keys[2])):
        eqs = [live_like(add_dip(*aplus_targets(c, R2, p, kind), held), R, o, c, irx) for p in range(5)]
        live[name] = {per: np.mean([m(e, irx, a, b)["年化"] for e in eqs]) for per, a, b in PERIODS}
    return tab, verdict, ok_all, flips, live


def section_h(c, irx, R, R2):
    held = dip_held(["SPY"], c.index)
    rows = []
    for cap in (1.0, 1.5, 2.0):
        eqs = []
        for p in range(5):
            T, g = aplus_targets(c, R2, p, cap=cap)
            eqs.append(ideal(add_dip(T, g, held, cap=cap), R, irx))
        ms = [m(e, irx) for e in eqs]
        worst_day = np.mean([float(e.pct_change().min()) for e in eqs])
        yearly = [e.resample("YE").last().pct_change().dropna() for e in eqs]
        rows.append({"杠杆上限": cap, "年化": np.mean([x["年化"] for x in ms]), "最大回撤": np.mean([x["最大回撤"] for x in ms]),
                     "最差一年": np.mean([x["最差一年"] for x in ms]), "最差一天": worst_day,
                     "单日暴跌 20% 时一天亏": 0.2 * cap * 0.98})
    return pd.DataFrame(rows)


def check_ideal_matches_research(c, irx, R, R2):
    """自检：这里的“理想执行”应和 research_v3 的 aplus_base 完全一样（同一套算法）。"""
    T, g = aplus_targets(c, R2, 0)
    e = ideal(T, R, irx)
    a, _ = s3.aplus_base(0, c, irx, R, R2)
    return float((e.pct_change().fillna(0) - a).abs().max())


def main():
    c, o, irx, R, R2 = load_all()
    diff = check_ideal_matches_research(c, irx, R, R2)
    print("self-check vs research_v3.aplus_base: max daily diff", diff)
    A = section_a(c, o, irx, R, R2)
    B, Bv, Bok, Bshare = section_b(c, irx, R, R2)
    C = section_c(c, irx, R, R2)
    D, Dshare = section_d(c, irx, R, R2)
    E, Eexp, Ecrash = section_e(c, irx, R, R2)
    F, Fborrow = section_f(c, irx, R, R2)
    G, Gv, Gok, Gflips, Glive = section_g(c, o, irx, R, R2)
    H = section_h(c, irx, R, R2)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    for name, x in (("A", A), ("B", B), ("C", C), ("D", D), ("E crash", Ecrash), ("F", F), ("G", G), ("H", H)):
        print(f"===== {name}"); print(x.round(4).to_string())
    print("B verdict", Bv, "adopt:", Bok, Bshare)
    print("D cash share", Dshare)
    print("E worst", E, Eexp)
    print("F borrowed avg", Fborrow)
    print("G verdict", Gv, "adopt:", Gok, Gflips, Glive)
    write_report(A, B, Bv, Bok, Bshare, C, D, Dshare, E, Eexp, Ecrash, F, Fborrow, G, Gv, Gok, Gflips, Glive, H,
                 c.index[-1].date(), diff)


def write_report(A, B, Bv, Bok, Bshare, C, D, Dshare, E, Eexp, Ecrash, F, Fborrow, G, Gv, Gok, Gflips, Glive, H, end, diff):
    pct = lambda df: [col for col in df.columns if any(k in col for k in ("年化", "回撤", "亏损", "暴跌", "离追缴", "最差"))]
    L = [f"# Noire 研究报告 v4：执行细节、第二条腿扩展、避险资产、压力测试（数据到 {end}）", "",
         "> 只做研究，不改实盘。问题和判断标准在看结果之前写在 research_v4.py 开头。5 种分批方式取平均；"
         "成本单边 0.10%（A 节另报 0.03%）；借钱按国库券 + 2.5%（F 节另报 1.5% / 3.5%）。", ""]
    notes = os.path.join(HERE, "notes_v4.md")
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            L += [f.read().strip(), "", "---", "", "# 附：全部数据", ""]
    L += ["## A. 实盘执行规则 vs 研究的理想执行（A+ 稳健版 + 第二条腿）", "",
          f"（自检：理想执行和 research_v3 的结果逐日最大相差 {diff:.1e}）", "",
          r.table(A, pct(A), ["每年换手"]), "",
          "## B. 第二条腿：只用 SPY vs SPY + QQQ + IWM", "",
          r.table(B, pct(B), ["夏普"], ["比第一行好的分批方式数"]), "",
          f"判断（和“只用 SPY”比）：设计期年化差 {Bv['设计期 2000–12']['年化差']:+.2%}、{Bv['设计期 2000–12']['更好的分批方式数']}/5 种更好、"
          f"最大回撤差 {Bv['设计期 2000–12']['最大回撤差']:+.1%}；检验期年化差 {Bv['检验期 2013–']['年化差']:+.2%}、"
          f"{Bv['检验期 2013–']['更好的分批方式数']}/5 种更好、最大回撤差 {Bv['检验期 2013–']['最大回撤差']:+.1%} → "
          f"**{'达到事先定的标准' if Bok else '没达到事先定的标准'}**。"
          f"有仓位的日子：只用 SPY {Bshare['只用 SPY 时有仓位的日子']:.0%}，三只 {Bshare['三只时有仓位的日子']:.0%}。", "",
          "## C. 第二条腿“当天收盘成交”（参考）", "", r.table(C, pct(C)), "",
          "## D. 避险资产：SHY vs BIL", "",
          f"A+ 稳健版在 BIL 上市后平均有 {Dshare:.0%} 的仓位在避险资产里。", "",
          r.table(D, pct(D), [], ["比 SHY 好的分批方式数"]), "",
          "## E. 压力测试", "",
          "历史上最差的一段（5 种分批方式；平均 / 最坏 / 最坏那次的结束日期）：", ""]
    for k, (avg, worst, day) in E.items():
        L.append(f"- 最差的{k}：平均 {avg:.1%}，最坏 {worst:.1%}（{day}）")
    L += ["", f"总仓位：平均 {Eexp['平均总仓位']:.2f} 倍；≥1.9 倍的日子 {Eexp['总仓位 ≥ 1.9 倍的日子']:.0%}；"
              f"≤1 倍的日子 {Eexp['总仓位 ≤ 1 倍的日子']:.0%}。", "",
          "假想单日暴跌（所有风险资产同时下跌）：", "", r.table(Ecrash, ["单日暴跌", "账户亏损", "离追缴还差"], ["总仓位"]), "",
          "## F. 借钱利率", "", f"平均借了本金的 {Fborrow:.0%}。", "", r.table(F, pct(F)), "",
          "## G. 趋势开关加缓冲（减少来回翻转）", "",
          "每年翻转次数：" + "；".join(f"{k} {x:.1f} 次" for k, x in Gflips.items()), "",
          r.table(G, pct(G), ["夏普"], ["比第一行好的分批方式数"]), ""]
    for k, vd in Gv.items():
        L.append(f"- {k}：设计期年化差 {vd['设计期 2000–12']['年化差']:+.2%}（{vd['设计期 2000–12']['更好的分批方式数']}/5 种更好），"
                 f"检验期年化差 {vd['检验期 2013–']['年化差']:+.2%}（{vd['检验期 2013–']['更好的分批方式数']}/5 种更好），"
                 f"检验期最大回撤差 {vd['检验期 2013–']['最大回撤差']:+.1%} → {'达标' if vd['达标'] else '不达标'}")
    L += ["", "按实盘规则（5% 才调、整股、2% 现金、单日亏损规则）的年化：" +
          "；".join(f"{k} 全期 {d['全期 2000–']:.1%} / 检验期 {d['检验期 2013–']:.1%}" for k, d in Glive.items()), "",
          f"**结论：{'两种都达标' if Gok else '没有达到事先定的标准，不改'}。**", "",
          "## H. 杠杆上限（只供参考）", "", r.table(H, pct(H), ["杠杆上限"]), ""]
    with open(os.path.join(HERE, "report_v4.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
