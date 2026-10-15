# -*- coding: utf-8 -*-
"""动量轮动 · Alpaca 模拟盘自动换仓

  python paper_rotation.py          空跑：只打印打算怎么做，不下单
  python paper_rotation.py --live   真正向【模拟盘】提交订单

设计成每个交易日收盘后运行一次（由 Windows 计划任务自动执行）：
  - 只有在“每月最后一个交易日”才会产生新信号；其他日子沿用上个月末的信号
  - 每天按最新净值把仓位调回目标杠杆（亏了会自动减仓）
  - 订单是市价单，收盘后提交 → 下一个交易日开盘成交（和回测假设一致）
  - 每笔订单有唯一编号，先记台账再提交；结果不明时停止并报警，绝不自动重试
  - 已有未成交订单、台账里有未核实的订单、STOP 文件存在时都不会下单
  - 永远只连模拟盘（paper=True），代码里没有实盘开关
"""
import os
import sys
import json
import datetime as dt
import pandas as pd

import config
import risk
import ledger
import alerts
import scoreboard
from paths import rt, HERE
from keys import load_keys
from rotation import load_panel, target_weights

os.chdir(HERE)   # 不管从哪里启动，相对路径（data/）都指向本文件夹
STATE_FILE = rt("paper_state.json")
TRADE_LOG = rt("logs", "trades.csv")
STATUS_FILE = rt(config.REPORT_DIR, "paper_status.txt")


def last_trading_day_of_month(client, day: dt.date) -> dt.date:
    """用 Alpaca 的交易日历找出本月最后一个交易日（会考虑节假日）。"""
    month_end = (pd.Timestamp(day) + pd.offsets.MonthEnd(0)).date()
    try:
        from alpaca.trading.requests import GetCalendarRequest
        cal = client.get_calendar(GetCalendarRequest(start=day, end=month_end))
        days = [c.date if isinstance(c.date, dt.date) else pd.Timestamp(c.date).date() for c in cal]
        if days:
            return max(days)
    except Exception as e:
        print(f"  (交易日历获取失败，改用工作日近似：{e})")
    return (pd.Timestamp(day) + pd.offsets.BMonthEnd(0)).date()


def current_signal(closes: pd.DataFrame, client):
    """返回 (目标权重, 信号日期)。只用“已经结束的月份”的月末收盘来定信号。"""
    last_day = closes.index[-1].date()
    if last_day == last_trading_day_of_month(client, last_day):
        cutoff = closes.index[-1]                       # 今天就是月末 → 用今天收盘出新信号
    else:
        this_month = closes.index.to_period("M") == closes.index[-1].to_period("M")
        cutoff = closes.index[~this_month][-1]          # 否则用上个月最后一个交易日
    w = target_weights(closes.loc[:cutoff], config.TOP_N).iloc[-1]
    return w[w > 0], cutoff.date()


def main():
    live = "--live" in sys.argv
    now = dt.datetime.now()
    print(f"===== {now:%Y-%m-%d %H:%M} 动量轮动模拟盘 {'【提交订单】' if live else '【空跑，不下单】'} =====")

    key, secret = load_keys()
    if not key or not secret:
        sys.exit("没找到密钥：请先双击 set_keys.bat")
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    client = TradingClient(key, secret, paper=True)     # 只连模拟盘
    if risk.is_stopped():
        print("检测到 STOP 文件（紧急停止中），本次不做任何操作。双击 resume_trading.bat 可恢复。")
        return

    # ---- 0. 先核实台账里结果不明的订单；查不清楚就不交易 ----
    still = ledger.resolve(client)
    if still:
        msg = f"有 {len(still)} 笔订单提交结果仍无法确认（{', '.join(still)}），为防重复下单，本次不交易"
        print(msg)
        if live:
            alerts.add(msg)
        return

    # ---- 1. 最新数据 + 信号 ----
    symbols = config.ROTATION_UNIVERSE + [config.CASH_SYMBOL]
    import data
    for s in symbols:
        data.load_prices(s, refresh=True)
    opens, closes = load_panel(symbols)
    age = (pd.Timestamp(now.date()) - closes.index[-1]).days
    if age > 5:
        sys.exit(f"数据太旧（最新一天是 {closes.index[-1].date()}），为安全起见不操作")
    target, signal_day = current_signal(closes, client)
    print(f"最新数据：{closes.index[-1].date()}　信号日期：{signal_day}")
    print(f"目标持仓：{', '.join(f'{s} {w:.0%}' for s, w in target.items())}")

    # ---- 2. 账户现状 ----
    acct = client.get_account()
    equity, cash = float(acct.equity), float(acct.cash)
    all_pos = {p.symbol: (float(p.qty), float(p.market_value)) for p in client.get_all_positions()}
    managed = set(symbols)
    # 本策略只管理自己的 ETF；手动买的其他股票不碰，也不算进本策略的资金
    positions = {s: v for s, v in all_pos.items() if s in managed}
    manual_value = sum(v for s, (q, v) in all_pos.items() if s not in managed)
    print(f"账户净值：${equity:,.2f}　现金：${cash:,.2f}")
    print(f"策略持仓：{', '.join(f'{s} {q:g}股(${v:,.0f})' for s, (q, v) in positions.items()) or '无'}")
    if manual_value:
        print(f"手动持仓（策略不管）：${manual_value:,.0f}")

    # 风控熔断：回撤过大降杠杆；单日亏损过大当天不买
    lev, lev_msg = risk.effective_leverage(equity)
    print(f"风控：{lev_msg}")
    day_chg = risk.daily_loss(equity, float(acct.last_equity or 0))
    no_buy = day_chg < -config.DAILY_LOSS_LIMIT
    if no_buy:
        print(f"风控：今天亏损 {day_chg:.1%}，超过 {config.DAILY_LOSS_LIMIT:.0%} 上限，今天只允许卖出")

    if live:   # 成绩单：每天记一行（第一次会先补上起点那一天）
        gross = sum(v for q, v in all_pos.values())
        record_score(now.date(), equity, float(closes["SPY"].iloc[-1]),
                     gross / equity if equity else 0, risk.update_peak(equity)["drawdown"])

    open_orders = [o for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
                   if o.symbol in managed]
    if open_orders:
        print(f"有 {len(open_orders)} 笔订单还没成交，本次不再下单（等它们成交后再检查）")
        for o in open_orders:
            print(f"  待成交：{o.side.value} {o.qty} {o.symbol}")
        write_status(equity, positions, target, signal_day, ["有未成交订单，跳过"], closes)
        return

    # ---- 3. 算出要买卖什么 ----
    strategy_equity = max(equity - manual_value, 0)
    investable = strategy_equity * lev * (1 - config.CASH_BUFFER)
    if lev > 1:
        print(f"杠杆 {lev:g} 倍：本金 ${strategy_equity:,.0f} → 目标持仓 ${investable:,.0f}")
    price = closes.iloc[-1]
    orders = []
    for sym in sorted(set(positions) | set(target.index)):
        qty_now, value_now = positions.get(sym, (0.0, 0.0))
        value_target = investable * target.get(sym, 0.0)
        diff = value_target - value_now
        if sym not in target.index and qty_now > 0:
            orders.append((OrderSide.SELL, sym, qty_now, "不在目标里，全部卖出"))
        elif abs(diff) > equity * config.REBALANCE_THRESHOLD and sym in price:
            qty = int(abs(diff) // price[sym])
            if qty > 0:
                side = OrderSide.BUY if diff > 0 else OrderSide.SELL
                orders.append((side, sym, qty, f"调到目标 {target.get(sym, 0):.0%}"))
    orders.sort(key=lambda o: o[0] != OrderSide.SELL)   # 先卖后买

    # 资金上限：1 倍时只能用现金；加杠杆时不超过券商隔夜购买力（Reg T）
    sell_value = sum(q * price.get(s, 0) for side, s, q, _ in orders if side == OrderSide.SELL)
    if lev > 1:
        regt = getattr(acct, "regt_buying_power", None) or acct.buying_power
        budget = float(regt) + sell_value
    else:
        budget = cash + sell_value - equity * config.CASH_BUFFER / 2
    buy_value = sum(q * price.get(s, 0) for side, s, q, _ in orders if side == OrderSide.BUY)
    if buy_value > budget > 0:
        ratio = budget / buy_value
        print(f"资金不够（需要 ${buy_value:,.0f}，可用 ${budget:,.0f}），买单按 {ratio:.0%} 缩小")
        orders = [(sd, s, int(q * ratio) if sd == OrderSide.BUY else q, w) for sd, s, q, w in orders]
        orders = [o for o in orders if o[2] > 0]
    elif buy_value > 0 and budget <= 0:
        print("没有可用资金，跳过买入")
        orders = [o for o in orders if o[0] == OrderSide.SELL]
    if no_buy:
        orders = [o for o in orders if o[0] == OrderSide.SELL]

    # ---- 4. 下单（每笔带唯一编号；结果不明立即停止）----
    actions = []
    stopped = False
    if not orders:
        actions.append("持仓已符合信号，无需操作")
        print(actions[-1])
    for side, sym, qty, why in orders:
        line = f"{'买入' if side == OrderSide.BUY else '卖出'} {sym} {qty:g} 股（约 ${qty * price.get(sym, 0):,.0f}）— {why}"
        print("  → " + line)
        if not live:
            actions.append(line)
            continue
        cid = ledger.make_id("rot", sym, side.value)
        req = MarketOrderRequest(symbol=sym, qty=qty, side=side, time_in_force=TimeInForce.DAY,
                                 client_order_id=cid)
        try:
            o = ledger.submit(client, req, {"symbol": sym, "side": side.value, "qty": qty, "why": why})
        except ledger.UncertainOrder as e:
            msg = f"自动换仓：{e}。已停止后续下单，下次运行会先向券商核实"
            print("  ✗ " + msg)
            alerts.add(msg)
            actions.append(f"{line}　✗ 结果不明，已停止")
            stopped = True
            break
        if o is None:
            print(f"    （编号 {cid} 今天已经下过，券商拒绝重复，跳过）")
            actions.append(f"{line}　（今天已下过，跳过）")
            continue
        actions.append(line)
        log_trade(now, sym, side.value, qty, float(price.get(sym, 0.0)), why)
    if stopped:
        print("因提交结果不明已停止，其余订单都没有提交")
    elif orders and live:
        print("已提交到模拟盘；收盘后提交的订单会在下一个交易日开盘成交")
    elif orders:
        print("（空跑，没有下单。加 --live 才会提交）")

    write_status(equity, positions, target, signal_day, actions, closes, live)


def record_score(day, equity, spy_close, leverage, drawdown):
    try:
        if not scoreboard.read(scoreboard.HISTORY) and os.path.exists(STATE_FILE):
            with open(STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
            start = dt.date.fromisoformat(st["start_date"])
            if start < day:
                scoreboard.record(start, float(st["start_equity"]), float(st["spy_start"]), 0.0, 0.0)
        scoreboard.record(day, equity, spy_close, leverage, drawdown)
        scoreboard.main()
    except Exception as e:
        print(f"成绩单记录失败（不影响交易）：{e}")


def log_trade(when, sym, side, qty, ref_price, why):
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    new = not os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", encoding="utf-8-sig") as f:
        if new:
            f.write("时间,标的,方向,股数,参考价,原因\n")
        f.write(f"{when:%Y-%m-%d %H:%M},{sym},{side},{qty:g},{ref_price:.2f},{why}\n")


def write_status(equity, positions, target, signal_day, actions, closes, live=False):
    """生成一页状态报告，并和“同期一直持有 SPY”比较。"""
    spy_now = float(closes["SPY"].iloc[-1])
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    elif live:   # 第一次真正运行时记下起点
        state = {"start_date": str(closes.index[-1].date()), "start_equity": equity, "spy_start": spy_now}
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    lines = [f"更新时间：{dt.datetime.now():%Y-%m-%d %H:%M}",
             f"账户净值：${equity:,.2f}",
             f"信号日期：{signal_day}　目标：{', '.join(f'{s} {w:.0%}' for s, w in target.items())}",
             f"当前持仓：{', '.join(f'{s} {q:g}股' for s, (q, v) in positions.items()) or '无'}",
             "本次操作：", *[f"  {a}" for a in actions]]
    if state:
        mine = equity / state["start_equity"] - 1
        spy = spy_now / state["spy_start"] - 1
        lines += ["", f"自 {state['start_date']} 起：策略 {mine:+.2%}　同期 SPY {spy:+.2%}　"
                      f"{'领先' if mine >= spy else '落后'} {abs(mine - spy):.2%}"]
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
