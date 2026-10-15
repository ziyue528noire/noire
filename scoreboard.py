# -*- coding: utf-8 -*-
r"""成绩单：每个交易日记一行净值，和同期 SPY 比较。

  python scoreboard.py        生成 reports/scoreboard.txt

统一格式（任何系统都可以按这个格式记录，放进 scoreboard/ 文件夹就会一起比较）：
  CSV，UTF-8，第一行表头：date,equity
  date = 美东交易日 YYYY-MM-DD，equity = 当天收盘后的账户净值（美元）
  其他列（如 spy_close、leverage）可有可无。
本系统自己的记录在 equity_history.csv，由每天 17:00 的自动换仓写入，
并同步一份到共享文件夹 D:\scoreboard\claude.csv（只写这一个文件）。
共享文件夹里其他系统的 CSV（例如 ChatGPT 的 gpt.csv）只读，用来同期比较。
"""
import os
import csv
import glob
import math
import datetime as dt

import config
from paths import rt, HERE

HISTORY = rt("equity_history.csv")
OTHERS_DIR = os.path.join(HERE, "scoreboard")
# 测试时（EXC_RUNTIME_DIR）共享文件夹也指向临时目录，绝不碰真实的 D:\scoreboard
SHARED_DIR = rt("shared_scoreboard") if os.environ.get("EXC_RUNTIME_DIR") else config.SHARED_SCOREBOARD_DIR
MY_SHARED = os.path.join(SHARED_DIR, f"{config.SCOREBOARD_NAME}.csv")
REPORT = rt("reports", "scoreboard.txt")
FIELDS = ["date", "equity", "spy_close", "leverage", "drawdown"]


def record(day: dt.date, equity: float, spy_close: float, leverage: float, drawdown: float):
    """写入（或覆盖）某一天的记录。同一天重复运行只保留最后一次。"""
    rows = read(HISTORY)
    rows = [r for r in rows if r["date"] != f"{day:%Y-%m-%d}"]
    rows.append({"date": f"{day:%Y-%m-%d}", "equity": f"{equity:.2f}", "spy_close": f"{spy_close:.4f}",
                 "leverage": f"{leverage:.3f}", "drawdown": f"{drawdown:.4f}"})
    rows.sort(key=lambda r: r["date"])
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    with open(HISTORY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read(path) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("date") and r.get("equity")]


def sync_shared():
    """把本系统的净值历史同步到共享文件夹（只写自己的 claude.csv）。"""
    rows = read(HISTORY)
    if not rows or not os.path.isdir(SHARED_DIR):
        return
    tmp = MY_SHARED + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity"])
        for r in rows:
            w.writerow([r["date"], r["equity"]])
    os.replace(tmp, MY_SHARED)   # 原子替换：对方任何时候读到的都是完整文件


def spy_closes() -> dict:
    """SPY 每日收盘价（来自本地行情缓存），让每个系统都能按自己的起止日期和 SPY 比较。"""
    path = os.path.join(HERE, config.DATA_DIR, "SPY.csv") if not os.path.isabs(config.DATA_DIR) \
        else os.path.join(config.DATA_DIR, "SPY.csv")
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = (r.get("Date") or next(iter(r.values())))[:10]
                out[d] = float(r["Close"])
    except Exception:
        pass
    return out


def stats(rows: list, spy_by_date: dict) -> dict:
    eq = [float(r["equity"]) for r in rows]
    dates = [r["date"] for r in rows]
    if len(eq) < 2:
        return {"days": len(eq)}
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1))
    out = {"days": len(eq), "start": dates[0], "end": dates[-1],
           "total": eq[-1] / eq[0] - 1, "max_dd": mdd,
           "vol": sd * math.sqrt(252),
           "sharpe": (mean / sd * math.sqrt(252)) if sd > 0 and len(rets) >= 20 else None}
    def near(d):
        ks = [k for k in spy_by_date if k <= d]
        return spy_by_date[max(ks)] if ks else None
    s0, s1 = near(dates[0]), near(dates[-1])
    if s0 and s1:
        out["spy"] = s1 / s0 - 1
        out["excess"] = out["total"] - out["spy"]
    return out


def main():
    try:
        sync_shared()
    except Exception as e:
        print(f"同步共享成绩单失败（不影响交易）：{e}")
    mine = read(HISTORY)
    spy = spy_closes()
    spy.update({r["date"]: float(r["spy_close"]) for r in mine if r.get("spy_close")})
    systems = {"Noire 动量轮动（Claude，2倍杠杆）": mine}
    others = glob.glob(os.path.join(OTHERS_DIR, "*.csv")) + glob.glob(os.path.join(SHARED_DIR, "*.csv"))
    for p in sorted(others):
        name = os.path.splitext(os.path.basename(p))[0]
        if os.path.abspath(p) == os.path.abspath(MY_SHARED) or name in systems:
            continue
        label = {"gpt": "ChatGPT 盘中学习系统"}.get(name, name)
        systems[label] = read(p)

    lines = [f"Noire 成绩单　更新 {dt.datetime.now():%Y-%m-%d %H:%M}",
             "同一时期比较；超额 = 系统收益 − 同期 SPY；夏普需满 20 个交易日才计算", ""]
    for name, rows in systems.items():
        s = stats(rows, spy)   # 各系统都和同一份 SPY 价格比较（按各自的起止日期）
        if s.get("days", 0) < 2:
            lines.append(f"{name}：数据不足（{s.get('days', 0)} 天）")
            continue
        sp = f"{s['sharpe']:.2f}" if s.get("sharpe") is not None else "—"
        spy_txt = f"同期SPY {s['spy']:+.2%}　超额 {s['excess']:+.2%}　" if "spy" in s else ""
        lines.append(f"{name}：{s['start']}~{s['end']}（{s['days']}天）　收益 {s['total']:+.2%}　"
                     f"{spy_txt}最大回撤 {s['max_dd']:.1%}　年化波动 {s['vol']:.0%}　夏普 {sp}")
    lines += ["", "提醒：天数少的时候收益主要是运气和大盘方向，至少看 1–3 个月再下结论。"]
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return text


if __name__ == "__main__":
    main()
