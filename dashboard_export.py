# -*- coding: utf-8 -*-
"""把 Noire 的日志整理成网页仪表盘用的数据（只读：不连券商、不下单、不改任何记录）。

用法：python dashboard_export.py [项目文件夹] [输出文件夹]
默认读本文件夹，输出到 reports/dashboard/。每个 .json 是一组表格行，仪表盘直接读取。
研究回测那几份（backtest_*.json、expectations.json）只在加 --backtest 时重算（需要 research/data）。
"""
import csv
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read(path, default=""):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return default


def num(s):
    return float(str(s).replace(",", "").replace("$", "").replace("%", "").strip())


def pct_after(text, label):
    m = re.search(label + r"\s*([+-]?[\d.]+)%", text)
    return float(m.group(1)) if m else None


def export(proj, out, backtest=False):
    os.makedirs(out, exist_ok=True)
    P = lambda *a: os.path.join(proj, *a)
    status_txt = read(P("reports", "paper_status.txt"))
    score_txt = read(P("reports", "scoreboard.txt"))
    monitor_last = read(P("reports", "monitor_last.txt")).strip()
    monitor_run = read(P("logs", "monitor_last_run.txt"))
    tests_txt = read(P("logs", "tests_last.txt"))
    risk = json.loads(read(P("risk_state.json"), "{}") or "{}")
    job = json.loads(read(P("logs", "paper_job_state.json"), "{}") or "{}")
    alerts_txt = read(P("alerts.txt"))

    # ---------- 净值历史
    equity = []
    for r in csv.DictReader(read(P("equity_history.csv")).splitlines()):
        equity.append({"date": r["date"], "equity": num(r["equity"]), "spy_close": num(r["spy_close"]),
                       "leverage": num(r["leverage"]), "drawdown_pct": round(num(r["drawdown"]) * 100, 2),
                       "broker_equity": num(r["broker_equity"]) if r.get("broker_equity") else None})
    last = equity[-1]

    # ---------- 目标（最近一次）
    targets = list(csv.DictReader(read(P("logs", "targets.csv")).splitlines()))
    tgt = targets[-1] if targets else {}
    weights = {}
    for part in (tgt.get("weights") or "").split("|"):
        if "=" in part:
            k, v = part.split("=")
            weights[k] = float(v)

    # ---------- 持仓（盘中监控最近一次的读数）
    mon_time = re.search(r"模拟盘状态 (\S+ \S+)", monitor_run)
    holdings = []
    for m in re.finditer(r"^\s*([A-Z]{2,5})\s+(\d+) 股　成本 \$([\d,.]+)　现价 \$([\d,.]+)　市值 \$([\d,.]+)　浮动盈亏 \$([-\d,.]+) \(([-\d.]+)%\)",
                         monitor_run, re.M):
        sym = m.group(1)
        holdings.append({"symbol": sym, "shares": int(m.group(2)), "cost": num(m.group(3)), "price": num(m.group(4)),
                         "value": num(m.group(5)), "pnl": num(m.group(6)), "pnl_pct": num(m.group(7)),
                         "target_pct": round(weights.get(sym, 0.0) * 100, 1),
                         "price_time": mon_time.group(1) if mon_time else ""})
    for sym, w in weights.items():                     # 目标里有、手上没有的
        if not any(h["symbol"] == sym for h in holdings):
            holdings.append({"symbol": sym, "shares": 0, "cost": None, "price": None, "value": 0.0, "pnl": 0.0,
                             "pnl_pct": 0.0, "target_pct": round(w * 100, 1), "price_time": ""})

    # ---------- 四批信号
    tranches = []
    for m in re.finditer(r"第(\d)批（([^，]+)，信号 (\d{4}-\d{2}-\d{2})）：(.+)", status_txt):
        for sm in re.finditer(r"([A-Z]{2,5}) (\d+)%", m.group(4)):
            tranches.append({"tranche": f"第{m.group(1)}批", "timing": m.group(2), "signal_date": m.group(3),
                             "symbol": sm.group(1), "weight_pct": float(sm.group(2))})

    # ---------- 概况
    lev_m = re.search(r"杠杆 ([\d.]+) 倍（组合近63天波动 ([\d.]+)%", status_txt)
    spy_m = re.search(r"SPY ([\d.]+) (高于|低于) 200 日均线 ([\d.]+)", status_txt)
    dip_line = re.search(r"第二条腿[^：]*：(.+)", status_txt)
    dip_text = dip_line.group(1).strip() if dip_line else ""
    rsi = re.search(r"2 日 RSI (\d+)", dip_text)
    sma5 = re.search(r"5 日均线 ([\d.]+)", dip_text)
    margin = re.search(r"距追缴保证金 再跌 (\d+)%", status_txt)
    real_lev = re.search(r"实际杠杆 ([\d.]+)x", status_txt)
    upd = re.search(r"更新时间：(\S+ \S+)", status_txt)
    act = status_txt.split("本次操作：", 1)[1].split("\n\n")[0].strip() if "本次操作：" in status_txt else ""
    since = re.search(r"自 (\S+) 起：策略 ([+-][\d.]+)%　同期 SPY ([+-][\d.]+)%", status_txt)
    status = [{
        "as_of": last["date"], "updated": upd.group(1) if upd else "",
        "equity": last["equity"], "broker_equity": last["broker_equity"],
        "start_date": since.group(1) if since else equity[0]["date"], "start_equity": equity[0]["equity"],
        "return_pct": float(since.group(2)) if since else None, "spy_return_pct": float(since.group(3)) if since else None,
        "leverage_target": float(lev_m.group(1)) if lev_m else None, "port_vol_pct": float(lev_m.group(2)) if lev_m else None,
        "leverage_actual": float(real_lev.group(1)) if real_lev else None,
        "gross_target_pct": round(sum(weights.values()) * 100, 1),
        "spy_close": float(spy_m.group(1)) if spy_m else None, "spy_above_200": (spy_m.group(2) == "高于") if spy_m else None,
        "spy_sma200": float(spy_m.group(3)) if spy_m else None, "spy_sma5": float(sma5.group(1)) if sma5 else None,
        "rsi2": float(rsi.group(1)) if rsi else None, "dip_held": dip_text.startswith("持有"), "dip_text": dip_text,
        "dip_share_pct": round(float(tgt.get("dip") or 0) * 100, 1),
        "margin_buffer_pct": float(margin.group(1)) if margin else None,
        "peak_equity": risk.get("peak_equity"), "drawdown_pct": last["drawdown_pct"], "action": act,
    }]

    # ---------- 成绩单
    score = []
    for m in re.finditer(r"^(.+?)：(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})（(\d+)天）　收益 ([+-][\d.]+)%　同期SPY ([+-][\d.]+)%　超额 ([+-][\d.]+)%　最大回撤 ([+-]?[\d.]+)%",
                         score_txt, re.M):
        score.append({"system": m.group(1).split("（")[0], "detail": m.group(1), "start": m.group(2), "end": m.group(3),
                      "days": int(m.group(4)), "return_pct": float(m.group(5)), "spy_pct": float(m.group(6)),
                      "excess_pct": float(m.group(7)), "max_dd_pct": float(m.group(8))})

    # ---------- 交易记录（下单）+ 实际成交价
    fills = {}
    for r in csv.DictReader(read(P("logs", "fills.csv")).splitlines()):
        fills.setdefault((r["symbol"], r["side"], int(r["qty"])), []).append(r)
    year = (equity[0]["date"] if equity else "2026")[:4]
    for m in re.finditer(r"^\s*(\d{2}-\d{2}) \d{2}:\d{2} (买入|卖出) ([A-Z]{2,5}) (\d+) 股.*已成交 \d+ 股 @ \$([\d,.]+)",
                         monitor_run, re.M):        # fills.csv 开始记录之前的成交，用监控里的订单列表补上
        key = (m.group(3), "buy" if m.group(2) == "买入" else "sell", int(m.group(4)))
        d = f"{year}-{m.group(1)}"
        if not any(c["date"] == d for c in fills.get(key, [])):
            fills.setdefault(key, []).append({"date": d, "price": num(m.group(5))})
    for v in fills.values():
        v.sort(key=lambda c: c["date"])
    trades = []
    for r in csv.DictReader(read(P("logs", "trades.csv")).splitlines()):
        key = (r["标的"], r["方向"], int(r["股数"]))
        f = None
        for cand in fills.get(key, []):
            if cand["date"] >= r["时间"][:10]:
                f = cand
                break
        trades.append({"order_time": r["时间"], "symbol": r["标的"], "side": "买入" if r["方向"] == "buy" else "卖出",
                       "shares": int(r["股数"]), "ref_price": num(r["参考价"]),
                       "fill_price": round(float(f["price"]), 2) if f else None, "fill_date": f["date"] if f else None,
                       "reason": r["原因"]})
    trades.reverse()

    # ---------- 系统健康
    today = last["date"]
    health = []

    words = {"ok": "正常", "warn": "注意", "bad": "异常"}

    def add(check, state, detail, at=""):
        health.append({"check": check, "state": words[state], "detail": detail, "at": at})

    hist = job.get("history") or []
    if job.get("date") == today and job.get("success"):
        add("今日换仓", "ok", "已完成" + ("（" + hist[-1]["time"] + "）" if hist else ""), today)
    elif job.get("date") == today:
        add("今日换仓", "bad", f"尚未成功（已试 {job.get('attempts', 0)} 次）", today)
    else:
        add("今日换仓", "warn", "今天还没有运行记录", job.get("date", ""))
    ran = re.search(r"\nRan (\d+) tests? in [\d.]+s\s*\n\s*\n(OK|FAILED[^\n]*)", tests_txt)
    t_at = tests_txt.splitlines()[0] if tests_txt else ""
    if ran:
        add("安全自检", "ok" if ran.group(2) == "OK" else "bad",
            f"{ran.group(1)} 项全部通过" if ran.group(2) == "OK" else f"{ran.group(1)} 项中有未通过：{ran.group(2)}", t_at)
    else:
        add("安全自检", "warn", "找不到最近一次自检结果", t_at)
    stale = re.search(r"⚠ 行情不是最新的[^\n]*", status_txt)
    add("行情数据", "warn" if stale else "ok", stale.group(0).lstrip("⚠ ") if stale else f"最新收盘价 {today}", today)
    mon_ok = "正常" in monitor_last
    add("盘中监控", "ok" if mon_ok else "warn", ("与券商核对正常" if mon_ok else monitor_last) , monitor_last[:11])
    today_alerts = [l for l in alerts_txt.splitlines() if l.startswith(today)]
    add("警报", "bad" if today_alerts else "ok", today_alerts[-1][17:] if today_alerts else "今天没有警报", today)
    la = status[0]["leverage_actual"]
    if la is not None:
        add("实际杠杆", "ok" if la <= 2.0 else "bad", f"{la:.2f} 倍（上限 2 倍）", today)
    mb = status[0]["margin_buffer_pct"]
    if mb is not None:
        add("保证金", "ok" if mb >= 25 else ("warn" if mb >= 15 else "bad"), f"再跌 {mb:.0f}% 才会被追缴", today)
    dd = last["drawdown_pct"]
    add("回撤", "ok" if dd < 20 else ("warn" if dd < 39 else "bad"),
        f"离最高点 -{dd:.1f}%（历史上 15%~20% 算正常，超过 39% 要停下检查）", today)

    files = {"equity": equity, "status": status, "holdings": holdings, "tranches": tranches, "scoreboard": score,
             "trades": trades, "health": health}

    if backtest:
        files.update(backtest_rows(proj))

    for name, rows in files.items():
        with open(os.path.join(out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    return files


def backtest_rows(proj):
    """用和实盘相同的算法（research/expectations.live_equity，分批方式同实盘）回放 2000 年至今。"""
    import pandas as pd
    sys.path.insert(0, os.path.join(proj, "research"))
    sys.path.insert(0, proj)
    import expectations as ex
    eq, spy, _ = ex.live_equity()
    eq = eq.dropna()
    spy = spy.reindex(eq.index)
    w = pd.DataFrame({"noire": eq / eq.iloc[0], "spy": spy / spy.iloc[0],
                      "noire_dd": (eq / eq.cummax() - 1) * 100, "spy_dd": (spy / spy.cummax() - 1) * 100})
    wk = w.resample("W-FRI").agg({"noire": "last", "spy": "last", "noire_dd": "min", "spy_dd": "min"}).dropna()
    weekly = [{"week": i.strftime("%Y-%m-%d"), "noire": round(r.noire, 4), "spy": round(r.spy, 4),
               "noire_dd": round(r.noire_dd, 2), "spy_dd": round(r.spy_dd, 2)} for i, r in wk.iterrows()]
    y = pd.DataFrame({"noire": eq, "spy": spy}).resample("YE").last()
    y = pd.concat([pd.DataFrame({"noire": [eq.iloc[0]], "spy": [spy.iloc[0]]}, index=[eq.index[0]]), y])
    yr = (y.pct_change().dropna() * 100).round(2)
    yearly = [{"year": int(i.year), "noire_pct": r.noire, "spy_pct": r.spy,
               "partial": bool(i.year == eq.index[-1].year)} for i, r in yr.iterrows()]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    stats = []
    for name, e in (("Noire（A+ 稳健版 + 第二条腿）", eq), ("SPY 买入持有", spy)):
        stats.append({"system": name, "start": eq.index[0].strftime("%Y-%m-%d"), "end": eq.index[-1].strftime("%Y-%m-%d"),
                      "cagr_pct": round(((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1) * 100, 1),
                      "max_dd_pct": round(float((e / e.cummax() - 1).min()) * 100, 1),
                      "growth_x": round(float(e.iloc[-1] / e.iloc[0]), 1),
                      "losing_years": int((yr["noire" if e is eq else "spy"] < 0).sum()), "years": int(len(yr))})
    return {"backtest_weekly": weekly, "backtest_yearly": yearly, "backtest_stats": stats}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    proj = args[0] if args else HERE
    out = args[1] if len(args) > 1 else os.path.join(proj, "reports", "dashboard")
    res = export(proj, out, backtest="--backtest" in sys.argv)
    print("已生成：", ", ".join(f"{k}({len(v)})" for k, v in res.items()), "→", out)
