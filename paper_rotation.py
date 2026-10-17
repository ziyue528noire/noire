# -*- coding: utf-8 -*-
"""动量轮动（A+ 稳健版）· Alpaca 模拟盘自动换仓

  python paper_rotation.py          空跑：只打印打算怎么做，不下单
  python paper_rotation.py --live   真正向【模拟盘】提交订单

设计成每个交易日收盘后运行一次（由 Windows 计划任务自动执行）：
  - 目标仓位和杠杆由 aplus.py 计算：分 4 批换仓（月末、月末前第 5/10/15 个交易日）、
    波动小的多买、杠杆 = 30% 波动目标（1~2 倍），SPY 在 200 日线下方时最多 1 倍
  - 每天按最新净值把仓位调回目标（相差不到总资产 5% 不动）
  - 订单是市价单，收盘后提交 → 下一个交易日开盘成交（和回测假设一致）
  - 每笔订单有唯一编号，先记台账再提交；结果不明时停止并报警，绝不自动重试
  - 已有未成交订单、台账里有未核实的订单、STOP 文件存在时都不会下单
  - 永远只连模拟盘（paper=True），代码里没有实盘开关
"""
import os
import sys
import json
import datetime as dt
import numpy as np
import pandas as pd

import config
import risk
import ledger
import alerts
import scoreboard
import aplus
import dipbuy
from paths import rt, HERE
from keys import load_keys
from rotation import load_panel

os.chdir(HERE)   # 不管从哪里启动，相对路径（data/）都指向本文件夹
STATE_FILE = rt("paper_state.json")
TRADE_LOG = rt("logs", "trades.csv")
TARGETS_LOG = rt("logs", "targets.csv")
STATUS_FILE = rt(config.REPORT_DIR, "paper_status.txt")
DRYRUN_STATUS_FILE = rt(config.REPORT_DIR, "paper_status_dryrun.txt")   # 空跑写这里，不覆盖正式状态


def trading_calendar(client, as_of: dt.date):
    """约 4 个月前到本月底的交易日（Alpaca 交易日历，会考虑节假日）；获取失败时用工作日近似。
    需要“本月底”是为了知道月末和月末前第 5/10/15 个交易日是哪天。"""
    start = (pd.Timestamp(as_of) - pd.DateOffset(months=4)).replace(day=1).date()
    end = (pd.Timestamp(as_of) + pd.offsets.MonthEnd(0)).date()
    try:
        from alpaca.trading.requests import GetCalendarRequest
        cal = client.get_calendar(GetCalendarRequest(start=start, end=end))
        days = sorted({aplus._as_date(c.date) for c in cal})
        if days:
            return days
    except Exception as e:
        print(f"  (交易日历获取失败，改用工作日近似：{e})")
    return [d.date() for d in pd.bdate_range(start, end)]


def price_sanity(closes: pd.DataFrame, max_jump=0.25):
    """数据出错的保护：最新价格缺失、≤0，或比前一天涨跌超过 25%（这几只 ETF 历史上从来没有过，
    多半是数据源出错，例如拆股没调整）→ 返回问题列表，调用方不交易。"""
    problems = []
    last, prev = closes.iloc[-1], closes.iloc[-2]
    for s in closes.columns:
        x, p = float(last[s]), float(prev[s])
        if not np.isfinite(x) or x <= 0:
            problems.append(f"{s} 最新价格是 {x}")
        elif p > 0 and abs(x / p - 1) > max_jump:
            problems.append(f"{s} 一天{'涨' if x > p else '跌'} {abs(x / p - 1):.0%}（{p:.2f} → {x:.2f}）")
    return problems


def check_data(client, last_bar: dict, as_of: dt.date, now: dt.datetime):
    """行情是不是最新、完整？返回 (提醒行列表, 今天是不是交易日 True/False/None=不知道)。只提醒，不阻止交易。"""
    notes = []
    behind = sorted(s for s, d in last_bar.items() if d < as_of)
    if behind:
        notes.append(f"⚠ 行情不完整：{'、'.join(behind)} 没有 {as_of} 的数据（用前一天的价格代替）")
    try:
        from alpaca.trading.requests import GetCalendarRequest
        cal = sorted({aplus._as_date(c.date) for c in client.get_calendar(GetCalendarRequest(start=as_of, end=now.date()))})
    except Exception:
        return notes, None
    closed = [d for d in cal if d < now.date() or (d == now.date() and now.time() >= dt.time(16, 15))]
    expected = closed[-1] if closed else as_of
    if as_of < expected:
        notes.append(f"⚠ 行情不是最新的：最新只有 {as_of}，应该已有 {expected} 的收盘价（本次按 {as_of} 计算）")
    return notes, now.date() in cal


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
        return "stopped"

    # ---- 0. 先核实台账里结果不明的订单；查不清楚就不交易 ----
    still = ledger.resolve(client)
    if still:
        msg = f"有 {len(still)} 笔订单提交结果仍无法确认（{', '.join(still)}），为防重复下单，本次不交易"
        print(msg)
        if live:
            alerts.add(msg)
        return "blocked"

    # ---- 1. 最新数据 + 信号 ----
    symbols = config.ROTATION_UNIVERSE + [config.CASH_SYMBOL]
    import data
    last_bar = {s: data.load_prices(s, refresh=True).index[-1].date() for s in symbols}
    opens, closes = load_panel(symbols)
    age = (pd.Timestamp(now.date()) - closes.index[-1]).days
    if age > 5:
        sys.exit(f"数据太旧（最新一天是 {closes.index[-1].date()}），为安全起见不操作")
    bad_px = price_sanity(closes)
    if bad_px:
        sys.exit("行情数据异常，为安全起见不操作：" + "；".join(bad_px))
    as_of = closes.index[-1].date()
    data_notes, trading_today = check_data(client, last_bar, as_of, now)
    target, lev, sig_lines = aplus.compute(closes, trading_calendar(client, as_of))
    target, dip, dip_lines = dipbuy.compute(closes, target)      # 第二条腿：只用 A+ 没用完的额度
    sig_lines = data_notes + sig_lines + dip_lines
    if data_notes and live:
        alerts.add("自动换仓：" + "；".join(n.lstrip("⚠ ") for n in data_notes))
    print(f"最新数据：{as_of}　策略：{config.STRATEGY_NAME}")
    for line in sig_lines:
        print(line)
    print(f"目标持仓（占本金，已含杠杆）：{', '.join(f'{s} {w:.0%}' for s, w in target.items())}")
    gross_target = float(sum(w for s, w in target.items() if s != config.CASH_SYMBOL))
    if live:
        log_targets(as_of, lev, target, dip)

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

    # 风控：单日亏损过大当天不买（杠杆已由 aplus 的波动目标 + 趋势开关决定，上限 2 倍）
    day_chg = risk.daily_loss(equity, float(acct.last_equity or 0))
    no_buy = day_chg < -config.DAILY_LOSS_LIMIT
    if no_buy:
        print(f"风控：今天亏损 {day_chg:.1%}，超过 {config.DAILY_LOSS_LIMIT:.0%} 上限，今天只允许卖出")
    gross_now = sum(v for q, v in all_pos.values())
    maint = float(getattr(acct, "maintenance_margin", 0) or 0)
    dist = risk.margin_distance(equity, gross_now, maint / gross_now if gross_now and maint else None)
    risk_line = (f"实际杠杆 {gross_now / equity if equity else 0:.2f}x　"
                 f"距追缴保证金 {'—（没借钱）' if gross_now <= equity else f'再跌 {dist:.0%}'}")
    print(f"风控：{risk_line}")

    if live and trading_today is not False:   # 成绩单：每个交易日记一行（休市日不记；第一次会先补上起点那一天）
        gross = sum(v for q, v in all_pos.values())
        record_score(now.date(), equity, float(closes["SPY"].iloc[-1]),
                     gross / equity if equity else 0, risk.update_peak(equity)["drawdown"])

    open_orders = [o for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
                   if o.symbol in managed]
    if open_orders:
        print(f"有 {len(open_orders)} 笔订单还没成交，本次不再下单（等它们成交后再检查）")
        for o in open_orders:
            print(f"  待成交：{o.side.value} {o.qty} {o.symbol}")
        write_status(equity, positions, target, sig_lines, ["有未成交订单，跳过"], closes, live, risk_line=risk_line)
        return "ok"

    # ---- 3. 算出要买卖什么 ----
    strategy_equity = max(equity - manual_value, 0)
    capital = strategy_equity * (1 - config.CASH_BUFFER)      # 目标权重是“占本金的比例”，已含杠杆
    print(f"A+ 杠杆 {lev:.2f} 倍{f' + 第二条腿 {dip:.2f} 倍' if dip > 0 else ''}：本金 ${strategy_equity:,.0f} → "
          f"目标持仓合计 ${capital * float(target.sum()):,.0f}（风险资产 {gross_target:.2f} 倍）")
    price = closes.iloc[-1]
    orders = []
    for sym in sorted(set(positions) | set(target.index)):
        qty_now, value_now = positions.get(sym, (0.0, 0.0))
        value_target = capital * target.get(sym, 0.0)
        diff = value_target - value_now
        if sym not in target.index and qty_now > 0:
            orders.append((OrderSide.SELL, sym, qty_now, "不在目标里，全部卖出"))
        elif abs(diff) > equity * config.REBALANCE_THRESHOLD and sym in price:
            qty = int(abs(diff) // price[sym])
            if diff < 0:
                qty = min(qty, int(qty_now))      # 卖出永远不超过持有的股数（绝不做空）
            if qty > 0:
                side = OrderSide.BUY if diff > 0 else OrderSide.SELL
                why = f"调到目标 {target.get(sym, 0):.0%}"
                if sym == config.DIP_SYMBOL and dip > 0:
                    why += f"（其中第二条腿 {dip:.0%}）"
                orders.append((side, sym, qty, why))
    orders.sort(key=lambda o: o[0] != OrderSide.SELL)   # 先卖后买

    # 资金上限：1 倍时只能用现金；加杠杆时不超过券商隔夜购买力（Reg T，卖单成交后）
    sell_value = sum(q * price.get(s, 0) for side, s, q, _ in orders if side == OrderSide.SELL)
    if gross_target > 1.0001:                                  # 目标里有借钱的部分（A+ 杠杆或第二条腿）
        regt = getattr(acct, "regt_buying_power", None) or acct.buying_power
        budget = float(regt) + sell_value
    else:
        budget = cash + sell_value - equity * config.CASH_BUFFER / 2
    why_short = "资金不够"
    # 券商检查买单时，还没成交的卖单不算进购买力（Alpaca 规则）→ 买单总额也不能超过券商“现在”的购买力，
    # 否则会被拒绝。超出的部分等卖单成交后，下一次运行自动补上。
    bp_now = getattr(acct, "buying_power", None)
    if bp_now is not None and float(bp_now) * 0.97 < budget:
        budget = max(float(bp_now) * 0.97, 0.0)
        why_short = "券商现在的购买力不够（卖单成交前，卖出的钱不算进购买力）"
    buy_value = sum(q * price.get(s, 0) for side, s, q, _ in orders if side == OrderSide.BUY)
    if buy_value > budget > 0:
        ratio = budget / buy_value
        print(f"{why_short}（需要 ${buy_value:,.0f}，可用 ${budget:,.0f}），买单按 {ratio:.0%} 缩小，剩下的下一次运行再补")
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
        except ledger.RejectedOrder as e:
            msg = f"自动换仓：{e}。这笔没有下成（其余订单照常），下一次运行会按最新仓位重新计算"
            print("  ✗ " + msg)
            alerts.add(msg)
            actions.append(f"{line}　✗ 被券商拒绝")
            continue
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

    write_status(equity, positions, target, sig_lines, actions, closes, live, risk_line=risk_line)
    return "blocked" if stopped else "ok"


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


def log_targets(as_of, lev, target, dip=0.0):
    """每次正式运行记下当天算出的目标（tracking.py 用它算“按策略应该赚多少”）。失败不影响交易。
    weights 是合并后的目标（含第二条腿）；dip 是其中第二条腿另外加的 SPY（占本金），单独记账用。"""
    try:
        os.makedirs(os.path.dirname(TARGETS_LOG), exist_ok=True)
        new = not os.path.exists(TARGETS_LOG)
        with open(TARGETS_LOG, "a", encoding="utf-8") as f:
            if new:
                f.write("as_of,run_time,leverage,cash_buffer,weights,dip\n")
            weights = "|".join(f"{s}={float(w):.6f}" for s, w in target.items())     # 例：QQQ=0.870000|EEM=0.570000
            f.write(f"{as_of},{dt.datetime.now():%Y-%m-%d %H:%M},{lev:.4f},{config.CASH_BUFFER},{weights},{dip:.6f}\n")
    except Exception as e:
        print(f"记录目标失败（不影响交易）：{e}")


def log_trade(when, sym, side, qty, ref_price, why):
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    new = not os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", encoding="utf-8-sig") as f:
        if new:
            f.write("时间,标的,方向,股数,参考价,原因\n")
        f.write(f"{when:%Y-%m-%d %H:%M},{sym},{side},{qty:g},{ref_price:.2f},{why}\n")


def write_status(equity, positions, target, sig_lines, actions, closes, live=False, risk_line=""):
    """生成一页状态报告，并和“同期一直持有 SPY”比较。
    正式运行写 paper_status.txt；空跑写 paper_status_dryrun.txt（以前空跑会覆盖正式状态，日报/周报会读错）。"""
    spy_now = float(closes["SPY"].iloc[-1])
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    elif live:   # 第一次真正运行时记下起点
        state = {"start_date": str(closes.index[-1].date()), "start_equity": equity, "spy_start": spy_now}
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    lines = [f"更新时间：{dt.datetime.now():%Y-%m-%d %H:%M}　策略：{config.STRATEGY_NAME}"
             + ("" if live else "　【空跑，没有下单】"),
             f"账户净值：${equity:,.2f}",
             f"目标（占本金，已含杠杆）：{', '.join(f'{s} {w:.0%}' for s, w in target.items())}",
             *sig_lines,
             f"当前持仓：{', '.join(f'{s} {q:g}股' for s, (q, v) in positions.items()) or '无'}",
             *([f"风险：{risk_line}"] if risk_line else []),
             "本次操作：", *[f"  {a}" for a in actions]]
    if state:
        mine = equity / state["start_equity"] - 1
        spy = spy_now / state["spy_start"] - 1
        lines += ["", f"自 {state['start_date']} 起：策略 {mine:+.2%}　同期 SPY {spy:+.2%}　"
                      f"{'领先' if mine >= spy else '落后'} {abs(mine - spy):.2%}"]
    path = STATUS_FILE if live else DRYRUN_STATUS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
