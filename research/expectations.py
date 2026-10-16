# -*- coding: utf-8 -*-
"""Noire 风险预期：A+ 稳健版“正常会亏多少、多久回本”

  python research/expectations.py     读取 research/data/*.csv，输出 research/expectations.md

做法（只做研究，不连券商）：
  1. 用和实盘完全相同的算法（aplus.py：4 批、波动倒数、收盘价算波动、趋势开关、不借钱买现金）
     回放 2000 年至今，借钱利率按 Alpaca 现价（国库券 + 2.5%）。
     实盘的 4 批是“月末、月末前第 5/10/15 个交易日”；历史上它碰巧是较好的一种分批方式，
     所以统计时把 5 种分批方式（第 p、p+5、p+10、p+15 天，p=0~4）的结果合在一起，避免把运气当本事。
  2. 历史上每一次超过 15% 的回撤：跌了多深、跌了多久、多久回本。
  3. 任意一年 / 三年会怎样（历史滚动窗口 + 打乱重组 2 万条“可能的未来”，并做一个“未来每年少赚 3 个点”的保守版本）。
  4. 单日、单周、单月的亏损多常见 —— 用来判断“正常波动”还是“程序出问题”。
"""
import os
import numpy as np
import pandas as pd
import research as r
import research_v2 as v

HERE = r.HERE
SPREAD = 0.025          # Alpaca 普通用户保证金 6.5% ≈ 国库券 + 2.5%


def live_equity(spread=SPREAD, start="2000-01-03", phase=0):
    c, irx, R = v.load_universe(v.U0)
    cc = c[v.U0 + ["SHY"]].pct_change(fill_method=None)
    R2 = cc[v.U0].copy()
    R2["CASH"] = cc["SHY"].fillna(irx.shift(1) / 252)
    R2 = R2.fillna(0.0)                                   # 实盘用收盘价算波动
    W = sum(v.make_W(c, v.U0, 2, "avg", "invvol", phase + 5 * j) for j in range(4)) / 4
    lev = v.lowest(v.lev_vol(W, R2, 0.30), r.lev_trend(c).reindex(W.index))
    eq = v.simulate(W, R, irx, lev_path=lev, cash_no_borrow=True, spread=spread, start=start)
    spyW = pd.DataFrame(0.0, index=c.index, columns=v.U0 + ["CASH"])
    spyW["SPY"] = 1.0
    return eq, v.simulate(spyW, R, irx, lev=1.0, start=start), irx


def episodes(e, thr=0.15):
    dd = e / e.cummax() - 1
    out, start = [], None
    for t in range(len(e)):
        if start is None and dd.iloc[t] < 0:
            start = max(t - 1, 0)
        if start is not None and (dd.iloc[t] == 0 or t == len(e) - 1):
            seg = dd.iloc[start:t + 1]
            if seg.min() <= -thr:
                trough = seg.idxmin()
                rec = (e.index[t] - trough).days / 30.4 if dd.iloc[t] == 0 else None
                out.append({"开始": e.index[start].date(), "最低点": trough.date(), "最深": seg.min(),
                            "跌了几个月": (trough - e.index[start]).days / 30.4, "回本用了几个月": rec})
            start = None
    return pd.DataFrame(out)


def rolling(e, n, step=5):
    x = e.values
    rets, mdds = [], []
    for i in range(0, len(x) - n, step):
        w = x[i:i + n + 1]
        rets.append(w[-1] / w[0] - 1)
        mdds.append((w / np.maximum.accumulate(w) - 1).min())
    return np.array(rets), np.array(mdds)


def bootstrap(daily, n_days, n_paths=20000, block=63, shift=0.0, seed=7):
    """按 63 天一块随机拼接历史日收益，保留“动荡期扎堆”的特点。daily：(分批方式数, 天数)，每块随机选一种分批方式。"""
    rng = np.random.default_rng(seed)
    x = np.atleast_2d(daily) - shift
    P, L = x.shape
    rets, mdds = np.empty(n_paths), np.empty(n_paths)
    nb = n_days // block + 1
    for p in range(n_paths):
        rows, starts = rng.integers(0, P, nb), rng.integers(0, L - block, nb)
        seq = np.concatenate([x[a, b:b + block] for a, b in zip(rows, starts)])[:n_days]
        path = np.cumprod(1 + seq)
        rets[p] = path[-1] - 1
        mdds[p] = (path / np.maximum.accumulate(np.concatenate([[1.0], path]))[1:] - 1).min()
    return rets, mdds


def pct(x, d=0):
    return f"{x * 100:+.{d}f}%" if x is not None and not (isinstance(x, float) and np.isnan(x)) else "—"


def main():
    eqs = []
    for ph in range(5):
        e_, spy, irx = live_equity(phase=ph)
        eqs.append(e_)
    eq = eqs[0]                                            # 实盘用的分批方式
    daily_all = np.vstack([e_.pct_change().fillna(0.0).values[1:] for e_ in eqs])
    daily = pd.Series(daily_all.ravel())
    ms = [v.m(e_, irx) for e_ in eqs]
    m = {"年化": float(np.mean([x["年化"] for x in ms])), "最大回撤": float(np.min([x["最大回撤"] for x in ms])),
         "最长套牢(月)": float(np.max([x["最长套牢(月)"] for x in ms]))}
    eps = [episodes(e_) for e_ in eqs]
    ep = eps[0]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    n15 = np.mean([len(x) for x in eps])
    n20 = np.mean([int((x["最深"] <= -0.20).sum()) for x in eps])
    n2030 = np.mean([int(((x["最深"] <= -0.20) & (x["最深"] > -0.30)).sum()) for x in eps])
    n30 = np.mean([int((x["最深"] <= -0.30).sum()) for x in eps])
    roll = [rolling(e_, 252) for e_ in eqs]
    r1, d1 = np.concatenate([a for a, _ in roll]), np.concatenate([b for _, b in roll])
    roll = [rolling(e_, 756) for e_ in eqs]
    r3, d3 = np.concatenate([a for a, _ in roll]), np.concatenate([b for _, b in roll])
    s1, sd1 = rolling(spy, 252)
    b1, bd1 = bootstrap(daily_all, 252)
    b3, bd3 = bootstrap(daily_all, 756)
    h1, hd1 = bootstrap(daily_all, 252, shift=0.03 / 252)
    h3, hd3 = bootstrap(daily_all, 756, shift=0.03 / 252)
    monthly = pd.concat([e_.resample("ME").last().pct_change().dropna() for e_ in eqs])
    weekly = pd.concat([e_.resample("W-FRI").last().pct_change().dropna() for e_ in eqs])
    yearly = pd.concat([eq.iloc[:1], eq.resample("YE").last()]).pct_change().dropna()
    rec = pd.concat([x["回本用了几个月"] for x in eps]).dropna()
    worst_dd = [x["最大回撤"] for x in ms]

    L = [f"# Noire 风险预期：A+ 稳健版正常会亏多少、多久回本（数据到 {eq.index[-1].date()}）", "",
         "> 用和实盘完全相同的算法回放 2000 年至今，借钱利率按 Alpaca 现价（国库券 + 2.5%）。只做研究，不改实盘。", "",
         "## 一句话", "",
         f"- 这个策略历史上平均每年约 {pct(m['年化'], 1)}，但**大约每 {yrs / max(n20, 1):.0f} 年会遇到一次 20% 以上的回撤**，"
         f"最深一次 {pct(max(worst_dd), 0)}~{pct(min(worst_dd), 0)}（看分批方式）；回撤 15% 以上平均每年 {n15 / yrs:.1f} 次。",
         f"- **任意一年**：中间水平 {pct(np.median(r1))}，但约 {np.mean(r1 < 0) * 100:.0f}% 的年份是亏的；"
         f"差的年份（20 年一遇）约 {pct(np.percentile(r1, 5))}。",
         f"- **任意三年**：中间水平 {pct(np.median(r3))}；三年下来亏钱的概率：历史 {np.mean(r3 < 0) * 100:.0f}%，"
         f"打乱重组后 {np.mean(b3 < 0) * 100:.0f}%，假设未来每年少赚 3 个点则 {np.mean(h3 < 0) * 100:.0f}%。",
         f"- **回本时间**：历次 15% 以上的回撤，从最低点回到前高一般要 {np.median(rec):.0f} 个月左右，最长 {rec.max():.0f} 个月；"
         f"如果从开始下跌算起，最长一次 {m['最长套牢(月)']:.0f} 个月才回到前高。",
         f"- **单日**：亏 3% 以上的日子每年约 {(daily < -0.03).sum() / (5 * yrs):.0f} 天，亏 5% 以上每年约 {(daily < -0.05).sum() / (5 * yrs):.1f} 天，"
         f"最差一天 {pct(daily.min(), 1)}。**单月**：亏 10% 以上大约每 {5 * yrs / max((monthly < -0.10).sum(), 1):.0f} 年一次，最差一个月 {pct(monthly.min(), 1)}。",
         "", "## 怎么判断“正常”还是“出问题”", "",
         f"- 回撤 15%~20%：**正常**，平均每年都可能来一次。",
         f"- 回撤 20%~30%：**少见但正常**，历史上约 {n2030:.0f} 次（约每 {yrs / max(n2030, 1):.0f} 年一次）。",
         f"- 回撤 30%~{abs(m['最大回撤']) * 100:.0f}%：**很少**，历史上约 {n30:.0f} 次，都回本了，但从开始下跌到回到前高要 2~3 年多。",
         f"- 回撤超过 {abs(m['最大回撤']) * 100:.0f}%：**超出历史**，要停下来检查程序和市场（不一定是程序坏了，但必须看）。",
         *([f"- 注意：最近一次（{ep.iloc[-1]['开始']} 开始，最深 {pct(ep.iloc[-1]['最深'], 1)}）在数据结束时还没回到前高"
            f"（当时离前高 {pct(float(eq.iloc[-1] / eq.cummax().iloc[-1] - 1), 1)}）。"]
           if len(ep) and pd.isna(ep.iloc[-1]["回本用了几个月"]) else []),
         f"- 单日亏损超过 {abs(daily.min()) * 100:.1f}%（历史最差一天）或实际杠杆超过 2.3 倍：先检查程序。",
         "", "## 历次 15% 以上的回撤（实盘这种分批方式）", "",
         r.table(ep.assign(最深=ep["最深"]), ["最深"], ["跌了几个月", "回本用了几个月"]), "",
         "## 任意一年 / 三年的结果分布", "",
         "| 情形 | 5%（很差） | 25% | 中间 | 75% | 95%（很好） | 亏钱概率 | 期间回撤超 20% | 超 30% |",
         "|---|---|---|---|---|---|---|---|---|"]
    for name, x, d in (("历史滚动·1 年", r1, d1), ("打乱重组·1 年", b1, bd1), ("保守（每年少 3 点）·1 年", h1, hd1),
                       ("历史滚动·3 年", r3, d3), ("打乱重组·3 年", b3, bd3), ("保守（每年少 3 点）·3 年", h3, hd3),
                       ("对照：SPY 一直持有·1 年", s1, sd1)):
        q = np.percentile(x, [5, 25, 50, 75, 95])
        L.append(f"| {name} | " + " | ".join(pct(v_) for v_ in q) +
                 f" | {np.mean(x < 0) * 100:.0f}% | {np.mean(d < -0.2) * 100:.0f}% | {np.mean(d < -0.3) * 100:.0f}% |")
    L += ["", "## 每年的收益（实盘这种分批方式）", "", " · ".join(f"{d.year}: {pct(x)}" for d, x in yearly.items()), "",
          f"亏钱的年份：{int((yearly < 0).sum())} / {len(yearly)}。"
          f"周收益最差 {pct(weekly.min(), 1)}；月收益 5% 分位 {pct(np.percentile(monthly, 5), 1)}。", ""]
    with open(os.path.join(HERE, "expectations.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L[:20]))


if __name__ == "__main__":
    main()
