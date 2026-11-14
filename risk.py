# -*- coding: utf-8 -*-
"""风控硬规则：独立于策略，所有下单程序都必须先过这里。

  - STOP 文件存在           → 禁止任何交易（紧急停止后生效，resume_trading.bat 解除）
  - 单日亏损 > 5%           → 当天不再买入
  - 杠杆上限 2 倍、只按券商隔夜购买力（Reg T）下单（在 paper_rotation.py 里执行）
原来的“回撤 >20% 降到 1 倍”熔断已于 2026-10-15 取消（研究显示它在低点降杠杆、长期卡在 1 倍，
反而比不加杠杆差）；杠杆改由 aplus.py 的波动目标 + 趋势开关提前控制。回撤仍会记录并报警。
状态保存在 risk_state.json（记录账户历史最高净值、每天的起始净值）。
"""
import csv
import json
import datetime as dt
import os

import cashflow
import config
from paths import rt

STOP_FILE = rt("STOP")
STATE_FILE = rt("risk_state.json")


def is_stopped() -> bool:
    return os.path.exists(STOP_FILE)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


HISTORY_FILE = rt("equity_history.csv")


def close_peak(exclude_date: str = None) -> float:
    """历史最高收盘净值 = equity_history.csv 的 equity 列最大值（2026-10-24 起统一用它算回撤）。
    10-19 及以前的老行没有收盘净值，equity 列就是当时的券商读数，照旧使用。
    exclude_date：同一天重跑时不算当天旧的那一行。没有记录时返回 0。"""
    peak = 0.0
    try:
        with open(HISTORY_FILE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("date") and r.get("equity") and r["date"] != exclude_date:
                    peak = max(peak, float(r["equity"]))
    except (OSError, ValueError):
        pass
    return peak


def _drawdown(equity: float, peak: float) -> float:
    return max(1 - equity / peak, 0.0) if peak > 0 else 0.0


def _history(exclude_date: str = None) -> list:
    """equity_history.csv 的行（按日期排好；exclude_date 那天不算）。"""
    try:
        with open(HISTORY_FILE, encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") and r.get("equity") and r["date"] != exclude_date]
    except OSError:
        return []
    return sorted(rows, key=lambda r: r["date"])


def _flow_peak(rows: list, equity: float, flow: float) -> dict:
    """有出入金时的回撤：用按时间加权的净值指数算（存钱不会掩盖亏损，取钱不会被当成回撤），
    最高点换算成“今天的美元”方便显示。flow = 最后一行之后、今天净值里已经包含的出入金。"""
    idx = cashflow.index_values(rows)
    last = float(rows[-1]["equity"])
    now_idx = idx[-1] * ((equity - flow) / last) if last > 0 else idx[-1]
    peak_idx = max(max(idx), now_idx)
    peak = equity * peak_idx / now_idx if now_idx > 0 else equity
    return {"peak": peak, "drawdown": max(1 - now_idx / peak_idx, 0.0) if peak_idx > 0 else 0.0}


def update_peak(equity: float, day: dt.date = None, flow: float = 0.0) -> dict:
    """收盘后（17:00 换仓）调用，equity 传当天的收盘净值。最高点 = 以前的最高收盘净值和今天取大，
    记进 risk_state.json（仪表盘读它）；返回 {peak, drawdown}。
    还没有净值记录时（新账户）退回用 risk_state.json 里存的最高点。
    flow：今天的出入金（实盘存钱/取钱，2026-11-05 起）。有出入金时按净值指数算回撤，见 _flow_peak。"""
    st = load_state()
    rows = _history(f"{day:%Y-%m-%d}" if day else None)
    if rows and (abs(flow or 0.0) >= 0.005 or cashflow.has_flows(rows)):
        info = _flow_peak(rows, equity, float(flow or 0.0))
        peak = info["peak"]
    else:
        hist_peak = close_peak(f"{day:%Y-%m-%d}" if day else None)
        base = hist_peak if hist_peak > 0 else float(st.get("peak_equity", 0))
        peak = max(base, equity)
        info = {"peak": peak, "drawdown": _drawdown(equity, peak)}
    st["peak_equity"] = peak
    st["last_equity_seen"] = equity
    st["updated"] = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    save_state(st)
    return info


def drawdown_now(equity: float, pending_flow: float = 0.0) -> dict:
    """盘中监控用：当前读数相对“最高收盘净值”的回撤。只看不改——盘中读数不再抬高最高点。
    pending_flow：最后一次记成绩单以后新存/取的钱（实盘）。有出入金时按净值指数算，见 _flow_peak。"""
    rows = _history()
    if rows and (abs(pending_flow or 0.0) >= 0.005 or cashflow.has_flows(rows)):
        info = _flow_peak(rows, equity, float(pending_flow or 0.0))
        return info
    peak = close_peak() or float(load_state().get("peak_equity", 0))
    return {"peak": peak, "drawdown": _drawdown(equity, peak)}


def day_base(equity: float, last_equity: float) -> float:
    """今天的起始净值。券商的“昨日净值”可用就用它；
    账户第一天券商给 0 → 用今天第一次看到的净值代替（记在 risk_state.json）。"""
    if last_equity and last_equity > 0:
        return last_equity
    st = load_state()
    today = f"{dt.date.today():%Y-%m-%d}"
    base = st.get("day_base", {})
    if today not in base:
        base = {today: equity}            # 只保留今天，文件不会越变越大
        st["day_base"] = base
        save_state(st)
    return float(base[today])


def daily_loss(equity: float, last_equity: float) -> float:
    """今天的涨跌幅（负数 = 亏损）。"""
    base = day_base(equity, last_equity)
    return equity / base - 1 if base else 0.0


def day_change(equity: float, last_equity: float, day_flow: float = 0.0, base: float = None) -> float:
    """今天的涨跌幅，扣除今天存取的钱（显示用：今日盈亏）。没有出入金时和 daily_loss 一样。
    base = 今天的起点净值（实盘有出入金时由 cashflow.day_basis 给出：上一个交易日的收盘净值）；不给 = 券商昨日净值。"""
    if not day_flow and not base:
        return daily_loss(equity, last_equity)
    start = base if base and base > 0 else day_base(equity, last_equity)
    return (equity - day_flow) / start - 1 if start else 0.0


def daily_loss_guard(equity: float, last_equity: float, day_flow: float = 0.0, base: float = None) -> float:
    """风控用的单日涨跌（单日亏损超过 5% 只卖不买、盘中报警）。只往“少误报”的方向扣出入金：
    取钱不会被当成亏损；存钱照旧按券商原始数字算——万一入金记录有误，也绝不会因此误锁成只卖不买。
    base：见 day_change（1.0.3：收盘后存取的钱不再扣两次）。"""
    raw = daily_loss(equity, last_equity)
    if not day_flow and not base:
        return raw
    return max(raw, day_change(equity, last_equity, day_flow, base))


def margin_distance(equity: float, gross: float, maintenance: float = None) -> float:
    """持仓整体再跌多少就会被追缴保证金（估算）。没借钱时返回 1（不会追缴）。"""
    if gross <= 0 or equity >= gross:
        return 1.0
    m = maintenance if maintenance else config.MAINTENANCE_MARGIN
    # 跌幅 x 后：净值 = E - G·x，持仓 = G·(1-x)；追缴条件 净值 < m·持仓
    x = (equity - m * gross) / (gross * (1 - m))
    return max(x, 0.0)
