# -*- coding: utf-8 -*-
"""风控熔断：独立于策略的硬规则，所有下单程序都必须先过这里。

  - STOP 文件存在           → 禁止任何交易（紧急停止后生效，resume_trading.bat 解除）
  - 账户回撤 > 20%          → 杠杆强制降到 1 倍；回撤收窄到 10% 以内才恢复
  - 单日亏损 > 5%           → 当天不再买入
状态保存在 risk_state.json（记录账户历史最高净值、每天的起始净值）。
"""
import json
import datetime as dt
import os

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


def update_peak(equity: float) -> dict:
    """更新历史最高净值，返回 {peak, drawdown}。"""
    st = load_state()
    peak = max(float(st.get("peak_equity", 0)), equity)
    st["peak_equity"] = peak
    st["last_equity_seen"] = equity
    st["updated"] = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    save_state(st)
    return {"peak": peak, "drawdown": 1 - equity / peak if peak > 0 else 0.0}


def effective_leverage(equity: float):
    """根据回撤决定今天允许的杠杆。返回 (杠杆, 说明)。带“滞后区间”，避免来回切换。"""
    info = update_peak(equity)
    st = load_state()
    dd = info["drawdown"]
    delevered = bool(st.get("delevered", False))
    if not delevered and dd > config.DELEVER_DRAWDOWN:
        delevered = True
    elif delevered and dd < config.RELEVER_DRAWDOWN:
        delevered = False
    st["delevered"] = delevered
    save_state(st)
    if delevered and config.LEVERAGE > 1:
        return 1.0, (f"熔断：账户回撤 {dd:.1%}（最高 ${info['peak']:,.0f}），杠杆降为 1 倍；"
                     f"回撤收窄到 {config.RELEVER_DRAWDOWN:.0%} 以内后恢复")
    return config.LEVERAGE, f"回撤 {dd:.1%}，杠杆 {config.LEVERAGE:g} 倍正常"


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


def margin_distance(equity: float, gross: float, maintenance: float = None) -> float:
    """持仓整体再跌多少就会被追缴保证金（估算）。没借钱时返回 1（不会追缴）。"""
    if gross <= 0 or equity >= gross:
        return 1.0
    m = maintenance if maintenance else config.MAINTENANCE_MARGIN
    # 跌幅 x 后：净值 = E - G·x，持仓 = G·(1-x)；追缴条件 净值 < m·持仓
    x = (equity - m * gross) / (gross * (1 - m))
    return max(x, 0.0)
