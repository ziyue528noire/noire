# -*- coding: utf-8 -*-
"""Noire 研究 v5：用标普 500 指数 1928 年以来的历史，做两项“更长的检验”（2026-10-16，用户：“继续精进”）

  python research/research_v5.py      读取 research/data/GSPC.csv（fetch_data.py long 下载），输出 research/report_v5.md

事先定好的问题和判断标准（在看结果之前写下，不看完结果再改）：
  1. 第二条腿 RSI(2) 在 1950–1992 年（以前的研究从没用过的 43 年数据）是否也有效？
     规则一点不改（收盘在 200 日线上方且 2 日 RSI < 10 买，收盘高于 5 日均线卖）。指数 1962 年以前没有真正的开盘价，
     所以按收盘成交算两种：当天收盘成交（参考，偏乐观）和晚一天收盘成交（保守）。每笔扣来回 0.2% 成本。
     标准：1950–1992 年“晚一天收盘”版本平均每笔 > 0 且胜率 > 55% → 通过（说明这个现象不是最近几十年才有的巧合）。
     另报 1928–1949（数据质量较差，有个别明显错误，只供参考）、1993–2012、2013 至今。
  2. A+ 的杠杆规则（波动目标 30%、1~2 倍、标普在 200 日线下方时最多 1 倍）在历次大跌里表现怎样？
     A+ 的 7 只 ETF 1993 年以前大多不存在，这里只用标普 500 一个资产近似（不是 A+ 本身，A+ 分散得多），
     和 1 倍、固定 2 倍比较，再加上第二条腿（只用剩余额度，总仓位不超过 2 倍）。
     只看价格（没算分红和借钱利息），所以只比回撤、不比收益。按“晚一天收盘”调仓（保守）。
     问题：每次大跌（1929–32、1937–38、1969–70、1973–74、1987、2000–02、2007–09、2020、2022）跌多少？会不会被追缴保证金？
  3. 1987 年 10 月“黑色星期一”逐日：这套规则那几天是几倍、账户一天亏多少。
  4. （第 1~3 项算完后、看到 1929 年 10 月第二条腿在暴跌中把总仓位补回 2 倍之后补充；在看第 4 项结果之前写下）
     “趋势开关也管第二条腿”：标普（实盘是 SPY）收盘在 200 日线下方时，总仓位上限是 1 倍（含第二条腿），
     即第二条腿的额度 = min(1, 上限 − A+ 风险仓位)，上限 = 趋势向上时 2 倍、向下时 1 倍。
     这和系统本来的原则一致（大趋势向下时最多 1 倍），等于给第二条腿加了一个“跌破 200 日线就减仓”的止损。
     判断标准（这是降风险的改动，允许少赚一点）：ETF 研究（2000 至今，5 种分批方式，研究的理想执行）里
     设计期和检验期年化都少不超过 0.3 个点；而且长历史（1928 至今，标普近似）里最差一天、最差 5 天都不变差，
     历次大跌回撤没有一次变差超过 1 个点。全部满足才建议用户改。
只做研究，不连券商、不交易。
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0010          # 单边
END = "2026-10-15"     # 最后一个完整交易日（下载时当天还没收盘）
PERIODS = (("1928–1949（数据较差，只供参考）", "1928-01-01", "1949-12-31"),
           ("1950–1992（以前从没用过）", "1950-01-01", "1992-12-31"),
           ("1993–2012", "1993-01-01", "2012-12-31"), ("2013 至今", "2013-01-01", None))
CRASHES = (("1929–32 大萧条", "1929-09-01", "1932-07-31"), ("1937–38", "1937-03-01", "1938-04-30"),
           ("1969–70", "1968-11-01", "1970-06-30"), ("1973–74", "1973-01-01", "1974-10-31"),
           ("1987 黑色星期一", "1987-08-01", "1987-12-31"), ("2000–02 互联网泡沫", "2000-03-01", "2002-10-31"),
           ("2007–09 金融危机", "2007-10-01", "2009-03-31"), ("2020 新冠", "2020-02-01", "2020-04-30"),
           ("2022 加息", "2022-01-01", "2022-10-31"))


def load():
    d = pd.read_csv(os.path.join(HERE, "data", "GSPC.csv"), index_col=0, parse_dates=True)
    return d["Close"].dropna().loc[:END]


def rsi2(c):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def dip_positions(c):
    """和实盘 dipbuy.positions 完全相同的规则。"""
    entry = (c > c.rolling(200).mean()) & (rsi2(c) < 10)
    exit_ = c > c.rolling(5).mean()
    on, out = False, []
    for e, x in zip(entry.values, exit_.values):
        if on and x:
            on = False
        elif not on and e:
            on = True
        out.append(1.0 if on else 0.0)
    return pd.Series(out, index=c.index)


def trade_list(c, pos, lag):
    """lag=0：信号当天收盘成交；lag=1：晚一天收盘成交。返回每笔（买入日, 卖出日, 扣成本后收益）。"""
    r = c.pct_change().fillna(0.0)
    held = pos.shift(1 + lag).fillna(0.0)          # 第 t 天的收益由第 t-1-lag 天收盘的信号决定
    out, start, acc = [], None, 1.0
    for t in range(len(c)):
        if held.iloc[t] > 0.5:
            if start is None:
                start, acc = c.index[t - 1], 1.0
            acc *= 1 + r.iloc[t]
        elif start is not None:
            out.append((start, c.index[t - 1], acc - 1 - 2 * COST))
            start = None
    return pd.DataFrame(out, columns=["买入", "卖出", "收益"])


def section1(c):
    pos = dip_positions(c)
    rows = []
    for per, a, b in PERIODS:
        for lag, lab in ((0, "当天收盘成交（参考）"), (1, "晚一天收盘成交（保守）")):
            tr = trade_list(c, pos, lag)
            tr = tr[(tr["买入"] >= a) & ((tr["买入"] <= b) if b else True)]
            yrs = (pd.Timestamp(b or END) - pd.Timestamp(a)).days / 365.25
            rows.append({"区间": per, "成交方式": lab, "笔数": len(tr), "每年几笔": len(tr) / yrs,
                         "平均每笔": tr["收益"].mean(), "胜率": (tr["收益"] > 0).mean(), "最差一笔": tr["收益"].min(),
                         "合计（简单相加，每年）": tr["收益"].sum() / yrs})
    df = pd.DataFrame(rows)
    key = df[(df["区间"].str.startswith("1950")) & (df["成交方式"].str.startswith("晚一天"))].iloc[0]
    passed = bool(key["平均每笔"] > 0 and key["胜率"] > 0.55)
    decades = []
    tr = trade_list(c, pos, 1)
    for d0 in range(1930, 2030, 10):
        x = tr[(tr["买入"].dt.year >= d0) & (tr["买入"].dt.year < d0 + 10)]
        if len(x):
            decades.append({"年代": f"{d0}s", "笔数": len(x), "平均每笔": x["收益"].mean(), "胜率": (x["收益"] > 0).mean()})
    return df, passed, pd.DataFrame(decades)


def exposures(c, lag=1):
    """各方案每天的仓位（第 t 行 = 第 t 天收益用的仓位）。A+ 杠杆规则用标普 500 一个资产近似。"""
    r = c.pct_change()
    vol = r.rolling(63).std() * np.sqrt(252)
    vol_lev = (0.30 / vol).clip(1.0, 2.0).fillna(1.0)
    trend = c > c.rolling(200).mean()
    lev = vol_lev.where(trend, np.minimum(vol_lev, 1.0))
    dip = dip_positions(c)
    spare = (2.0 - lev).clip(0.0, 1.0) * dip
    sh = 1 + lag
    return pd.DataFrame({"1 倍": 1.0, "固定 2 倍": 2.0,
                         "A+ 的杠杆规则（近似）": lev.shift(sh),
                         "A+ 杠杆规则 + 第二条腿": (lev + spare).shift(sh)}, index=c.index).fillna(1.0), lev, dip


def run(c, ex):
    """只看价格的净值；记录追缴（净值/持仓 < 30%）和爆仓。"""
    r = c.pct_change().fillna(0.0).values
    out, calls = {}, {}
    for name in ex.columns:
        L = ex[name].values
        eq, e, n_call = np.empty(len(r)), 1.0, 0
        for t in range(len(r)):
            if e <= 0:
                eq[t] = 0.0
                continue
            g = 1 + L[t] * r[t]
            if L[t] > 1 and g > 0 and g / (L[t] * (1 + r[t])) < 0.30:
                n_call += 1
            e = e * g if g > 0 else 0.0
            eq[t] = e
        out[name], calls[name] = pd.Series(eq, index=c.index), n_call
    return out, calls


def mdd(x):
    x = x[x > 0] if (x > 0).any() else x
    return float((x / x.cummax() - 1).min()) if len(x) else -1.0


def section2(c):
    ex, lev, dip = exposures(c, lag=1)
    eqs, calls = run(c, ex)
    rows = []
    for name, a, b in CRASHES:
        row = {"大跌": name}
        for k, e in eqs.items():
            seg = e.loc[a:b]
            row[k] = -1.0 if (seg <= 0).any() else mdd(seg)
        rows.append(row)
    crash = pd.DataFrame(rows)
    summ = []
    for k, e in eqs.items():
        r_ = e.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        summ.append({"方案": k, "1928 至今最大回撤": -1.0 if (e <= 0).any() else mdd(e),
                     "1950 至今最大回撤": mdd(e.loc["1950":]), "最差一天": float(r_.min()),
                     "被追缴保证金的天数": calls[k], "平均仓位（1950 至今）": float(ex[k].loc["1950":].mean())})
    return crash, pd.DataFrame(summ), lev, dip


def section3(c, lev, dip):
    r = c.pct_change()
    w = pd.DataFrame({"标普收盘": c, "当天涨跌": r, "200 日均线": c.rolling(200).mean(),
                      "近 63 天波动": r.rolling(63).std() * np.sqrt(252), "当天收盘算出的 A+ 杠杆": lev,
                      "第二条腿": dip, "2 日 RSI": rsi2(c)})
    tot = lev + (2.0 - lev).clip(0.0, 1.0) * dip
    w["当天收盘算出的总仓位"] = tot
    w["按前一天收盘的仓位，当天账户涨跌"] = tot.shift(1) * r
    return w.loc["1987-10-01":"1987-10-30"]


def section4(c):
    """第 4 项：趋势开关也管第二条腿（ETF 研究 5 种分批 + 标普长历史）。"""
    import research as r_
    import research_v4 as q4
    ce, o, irx, R, R2 = q4.load_all()
    held = q4.dip_held(["SPY"], ce.index)
    cap_e = r_.lev_trend(ce)

    def capped(T, gross):
        T = T.copy()
        spare = (cap_e.reindex(T.index).fillna(1.0) - gross).clip(0.0, 1.0) * held["SPY"]
        T["SPY"] = T["SPY"] + spare
        T["CASH"] = np.minimum(T["CASH"], (1.0 - gross - spare).clip(lower=0.0))
        return T
    rows = []
    for p in range(5):
        T, g = q4.aplus_targets(ce, R2, p)
        for name, e in (("现在", q4.ideal(q4.add_dip(T, g, held), R, irx)), ("趋势开关也管第二条腿", q4.ideal(capped(T, g), R, irx))):
            for per, a, b in q4.PERIODS:
                x = q4.m(e, irx, a, b)
                rows.append({"方案": name, "分批": p, "区间": per, "年化": x["年化"], "最大回撤": x["最大回撤"]})
    df = pd.DataFrame(rows)
    etf = df.groupby(["方案", "区间"], sort=False).agg(年化=("年化", "mean"), 最大回撤=("最大回撤", "mean")).reset_index()
    piv = df.pivot_table(index=["分批", "区间"], columns="方案", values="年化")
    cagr_diff = (piv["趋势开关也管第二条腿"] - piv["现在"]).groupby(level=1).mean()
    r = c.pct_change()
    vol_lev = (0.30 / (r.rolling(63).std() * np.sqrt(252))).clip(1.0, 2.0).fillna(1.0)
    trend = c > c.rolling(200).mean()
    lev = vol_lev.where(trend, np.minimum(vol_lev, 1.0))
    dip = dip_positions(c)
    cap = pd.Series(np.where(trend, 2.0, 1.0), index=c.index)
    ex = pd.DataFrame({"现在": (lev + (2.0 - lev).clip(0, 1) * dip).shift(2),
                       "趋势开关也管第二条腿": (lev + (cap - lev).clip(0, 1) * dip).shift(2)}, index=c.index).fillna(1.0)
    eqs, _ = run(c, ex)
    crash = pd.DataFrame([{"大跌": n, **{k: mdd(e.loc[a:b]) for k, e in eqs.items()}} for n, a, b in CRASHES])
    tails = []
    for k, e in eqs.items():
        rr = e.pct_change().dropna()
        w5 = (1 + rr).rolling(5).apply(np.prod, raw=True) - 1
        tails.append({"方案": k, "最差一天": float(rr.min()), "最差一天日期": rr.idxmin().date(),
                      "最差 5 天": float(w5.min()), "1928 至今最大回撤": mdd(e)})
    tails = pd.DataFrame(tails)
    ok = (bool((cagr_diff.loc[["设计期 2000–12", "检验期 2013–"]] >= -0.003).all())
          and tails.iloc[1]["最差一天"] >= tails.iloc[0]["最差一天"] and tails.iloc[1]["最差 5 天"] >= tails.iloc[0]["最差 5 天"]
          and bool(((crash["趋势开关也管第二条腿"] - crash["现在"]) >= -0.01).all()))
    return etf, cagr_diff, crash, tails, ok


def main():
    c = load()
    s1, passed, dec = section1(c)
    crash, summ, lev, dip = section2(c)
    s3 = section3(c, lev, dip)
    s4 = section4(c)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    print(s1.round(4).to_string()); print("PASS:", passed); print(dec.round(4).to_string())
    print(crash.round(3).to_string()); print(summ.round(3).to_string()); print(s3.round(3).to_string())
    print(s4[0].round(4).to_string()); print(s4[1].round(4).to_string()); print(s4[2].round(3).to_string())
    print(s4[3].round(4).to_string()); print("PASS4:", s4[4])
    write_report(c, s1, passed, dec, crash, summ, s3, s4)


def table(df, pct_cols, num_cols=(), int_cols=()):
    cols = list(df.columns)
    L = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        cells = []
        for col in cols:
            v_ = row[col]
            if col in pct_cols:
                cells.append("—" if pd.isna(v_) else ("爆仓" if v_ <= -0.999 else f"{v_:.1%}"))
            elif col in num_cols:
                cells.append("—" if pd.isna(v_) else f"{v_:.2f}")
            elif col in int_cols:
                cells.append("—" if pd.isna(v_) else f"{int(v_)}")
            else:
                cells.append(str(v_.date()) if hasattr(v_, "date") else str(v_))
        L.append("| " + " | ".join(cells) + " |")
    return "\n".join(L)


def write_report(c, s1, passed, dec, crash, summ, s3, s4):
    L = [f"# Noire 研究报告 v5：标普 500 指数 1928 年以来的长期检验（数据 {c.index[0].date()} ~ {c.index[-1].date()}）", "",
         "> 只做研究，不改实盘。问题和判断标准在看结果之前写在 research_v5.py 开头。"
         "数据是标普 500 价格指数（不含分红）；1962 年以前没有真正的开盘价，所以都按收盘价成交计算。", ""]
    notes = os.path.join(HERE, "notes_v5.md")
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            L += [f.read().strip(), "", "---", "", "# 附：全部数据", ""]
    L += ["## 1. 第二条腿 RSI(2) 在更早的年代（每笔扣来回 0.2% 成本）", "",
          table(s1, ["平均每笔", "胜率", "最差一笔", "合计（简单相加，每年）"], ["每年几笔"], ["笔数"]), "",
          f"**事先定的标准（1950–1992 年、晚一天收盘：平均每笔 > 0 且胜率 > 55%）：{'通过' if passed else '没通过'}。**", "",
          "按年代（晚一天收盘成交）：", "", table(dec, ["平均每笔", "胜率"], [], ["笔数"]), "",
          "## 2. A+ 的杠杆规则在历次大跌里（只用标普 500 近似；只看价格；晚一天收盘调仓）", "",
          "每次大跌期间从高点到低点跌了多少：", "",
          table(crash, [col for col in crash.columns if col != "大跌"]), "",
          table(summ, ["1928 至今最大回撤", "1950 至今最大回撤", "最差一天"], ["平均仓位（1950 至今）"], ["被追缴保证金的天数"]), "",
          "## 3. 1987 年 10 月逐日（当天收盘算出仓位，第二天用）", "",
          table(s3.reset_index().rename(columns={"Date": "日期"}),
                ["当天涨跌", "近 63 天波动", "按前一天收盘的仓位，当天账户涨跌"],
                ["标普收盘", "200 日均线", "当天收盘算出的 A+ 杠杆", "2 日 RSI", "当天收盘算出的总仓位"], ["第二条腿"]), "",
          "## 4. “趋势开关也管第二条腿”（大趋势向下时总仓位最多 1 倍，含第二条腿）", "",
          "ETF 研究（2000 至今，5 种分批方式，研究的理想执行）：", "", table(s4[0], ["年化", "最大回撤"]), "",
          "年化变化：" + "；".join(f"{k} {v_:+.2%}" for k, v_ in s4[1].items()), "",
          "标普长历史（近似，只看价格）：", "", table(s4[2], ["现在", "趋势开关也管第二条腿"]), "",
          table(s4[3], ["最差一天", "最差 5 天", "1928 至今最大回撤"]), "",
          f"**事先定的标准：{'全部满足' if s4[4] else '没有全部满足 → 不改'}。**", ""]
    with open(os.path.join(HERE, "report_v5.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
