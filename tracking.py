# -*- coding: utf-8 -*-
"""实盘 vs 模型 对账：账户每天实际赚的，和“完全按目标仓位、按开盘价成交”应该赚的，差多少？

  python tracking.py                    读本文件夹的记录，写 reports/tracking.txt
  python tracking.py --root 某文件夹     读另一个文件夹（例如云端日报把文件拷过去后在那边运行）

用到的记录（都由系统自动写入）：
  equity_history.csv   每天收盘后的账户净值（paper_rotation 写）
  logs/targets.csv     每天算出的目标仓位（paper_rotation 正式运行时写）
  logs/fills.csv       本系统订单的实际成交价（monitor 每小时写）
  data/<代码>.csv       行情（开盘价、收盘价）

模型收益（第 d 天，前一天收盘 → 当天收盘）：
  隔夜（前一天收盘 → 当天开盘）按“前一晚之前”那次的目标仓位；白天（开盘 → 收盘）按“前一晚”算出的新目标仓位。
  目标仓位 × (1 − 现金缓冲) = 占净值的比例。没算借钱利息（模拟盘可能不收），也没算手动买卖的股票。
只读，不连券商、不交易；只依赖 pandas，可以在任何电脑上运行。
"""
import os
import sys
import csv
import argparse
import datetime as dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_targets(root):
    """{as_of 日期: {代码: 占净值比例}}，同一天多次运行取最后一次。"""
    out = {}
    for r in read_csv_rows(os.path.join(root, "logs", "targets.csv")):
        try:
            buf = float(r.get("cash_buffer") or 0.0)
            w = {}
            for part in (r.get("weights") or "").split("|"):
                if "=" in part:
                    s, x = part.split("=", 1)
                    w[s.strip()] = float(x) * (1 - buf)
            out[dt.date.fromisoformat(r["as_of"][:10])] = w
        except Exception:
            continue
    return out


def load_prices(root, symbols):
    opens, closes = {}, {}
    for s in symbols:
        p = os.path.join(root, "data", f"{s}.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index).date
        opens[s], closes[s] = df["Open"], df["Close"]
    return pd.DataFrame(opens), pd.DataFrame(closes)


def load_dips(root):
    """{as_of 日期: 第二条腿另外加的 SPY 占净值比例}（没有 dip 列的旧记录当作 0）。"""
    out = {}
    for r in read_csv_rows(os.path.join(root, "logs", "targets.csv")):
        try:
            buf = float(r.get("cash_buffer") or 0.0)
            out[dt.date.fromisoformat(r["as_of"][:10])] = float(r.get("dip") or 0.0) * (1 - buf)
        except Exception:
            continue
    return out


def latest_on_or_before(targets, day):
    keys = [k for k in targets if k <= day]
    return targets[max(keys)] if keys else None


def daily_tracking(root):
    hist = read_csv_rows(os.path.join(root, "equity_history.csv"))
    targets = load_targets(root)
    dips = load_dips(root)
    if len(hist) < 2 or not targets:
        return pd.DataFrame(), targets
    eq = pd.Series({dt.date.fromisoformat(r["date"]): float(r["equity"]) for r in hist}).sort_index()
    symbols = sorted({s for w in targets.values() for s in w} | {"SPY"})
    opens, closes = load_prices(root, symbols)
    if closes.empty:
        return pd.DataFrame(), targets
    cal = [d for d in closes.index]                     # 交易日历（以行情为准）
    rows = []
    for d in eq.index:
        if d not in cal:
            continue
        i = cal.index(d)
        if i < 2:
            continue
        p, pp = cal[i - 1], cal[i - 2]
        if p not in eq.index:
            continue                                     # 前一个交易日没有净值记录，跳过
        w_new = latest_on_or_before(targets, p)          # 前一晚算出、当天开盘成交
        w_old = latest_on_or_before(targets, pp)         # 隔夜拿着的（再前一晚的目标）
        if w_new is None or w_old is None or min(targets) > pp:
            continue
        try:
            r_night = sum(w * (opens.at[d, s] / closes.at[p, s] - 1) for s, w in w_old.items())
            r_day = sum(w * (closes.at[d, s] / opens.at[d, s] - 1) for s, w in w_new.items())
        except KeyError:
            continue
        model = (1 + r_night) * (1 + r_day) - 1
        actual = eq[d] / eq[p] - 1
        dn, do = latest_on_or_before(dips, p) or 0.0, latest_on_or_before(dips, pp) or 0.0
        try:
            dip = do * (opens.at[d, "SPY"] / closes.at[p, "SPY"] - 1) + dn * (closes.at[d, "SPY"] / opens.at[d, "SPY"] - 1)
        except KeyError:
            dip = 0.0
        rows.append({"date": d, "actual": actual, "model": model, "diff": actual - model,
                     "gross": sum(w_new.values()), "dip_model": dip, "dip_on": (dn > 0) or (do > 0)})
    return pd.DataFrame(rows), targets


def slippage(root):
    """本系统换仓单（noire-rot-）的成交价比当天开盘价吃亏多少（基点，正数 = 吃亏）。"""
    fills = [f for f in read_csv_rows(os.path.join(root, "logs", "fills.csv"))
             if str(f.get("client_order_id", "")).startswith("noire-rot-")]
    latest = {}
    for i, f in enumerate(fills):        # 同一笔订单先“部分成交”、后“全部成交”会记两行，只用最后一行（不重复计算）
        latest[f.get("broker_id") or f"row{i}"] = f
    fills = list(latest.values())
    if not fills:
        return pd.DataFrame()
    syms = sorted({f["symbol"] for f in fills})
    opens, _ = load_prices(root, syms)
    rows = []
    for f in fills:
        try:
            d = dt.date.fromisoformat(f["date"][:10])        # 开盘时段美东日期 = UTC 日期
            o = float(opens.at[d, f["symbol"]])
        except Exception:
            continue
        price, qty = float(f["price"]), float(f["qty"])
        sign = 1.0 if f["side"] == "buy" else -1.0
        bps = (price / o - 1) * 1e4 * sign
        rows.append({"date": d, "symbol": f["symbol"], "side": f["side"], "qty": qty, "price": price,
                     "open": o, "bps": bps, "cost": (price - o) * qty * sign})
    return pd.DataFrame(rows)


def report(root):
    tr, targets = daily_tracking(root)
    sl = slippage(root)
    L = [f"实盘 vs 模型 对账（更新 {dt.datetime.now():%Y-%m-%d %H:%M}）"]
    if tr.empty:
        L.append("还没有足够的记录（需要连续几天的目标仓位和净值），以后会自动有。")
    else:
        act = float(np.prod(1 + tr["actual"]) - 1)
        mod = float(np.prod(1 + tr["model"]) - 1)
        L += [f"对账天数 {len(tr)}（{tr['date'].min()} ~ {tr['date'].max()}）",
              f"累计：实盘 {act:+.2%}　模型 {mod:+.2%}　相差 {act - mod:+.2%}",
              f"平均每天相差 {tr['diff'].mean() * 1e4:+.1f} 个基点（0.01%），波动 {tr['diff'].std() * 1e4:.1f} 个基点" if len(tr) > 1
              else f"这一天相差 {tr['diff'].iloc[0] * 1e4:+.1f} 个基点",
              "（相差来自：成交价和开盘价不同、整股取整、5% 以内不调仓、借钱利息等；长期每天在 ±5 个基点以内算正常）"]
        if "dip_on" in tr and tr["dip_on"].any():
            L.append(f"其中第二条腿（RSI(2) 超跌反弹）：有仓位 {int(tr['dip_on'].sum())} 天，模型贡献约 {tr['dip_model'].sum():+.2%}"
                     f"（A+ 稳健版部分约 {tr['model'].sum() - tr['dip_model'].sum():+.2%}，均为简单相加）")
        big = tr[tr["diff"].abs() > 0.01]
        if len(big):
            L.append("⚠ 相差超过 1% 的日子（要查原因：手动交易？订单没成交？程序出错？）：")
            L += [f"  {r.date}：实盘 {r.actual:+.2%}，模型 {r.model:+.2%}" for r in big.itertuples()]
    if sl.empty:
        L.append("成交价：还没有成交记录。")
    else:
        L += [f"成交价 vs 开盘价：{len(sl)} 笔，平均 {sl['bps'].mean():+.1f} 个基点（正数 = 比开盘价吃亏），"
              f"合计约 ${sl['cost'].sum():,.0f}",
              *[f"  {r.date} {r.side} {r.symbol} {r.qty:g} 股 @ {r.price:.2f}（开盘 {r.open:.2f}，{r.bps:+.1f} 基点）"
                for r in sl.tail(8).itertuples()]]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=HERE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    text = report(a.root)
    out = a.out or os.path.join(a.root, "reports", "tracking.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
