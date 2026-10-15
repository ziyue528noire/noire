# -*- coding: utf-8 -*-
"""盘中监控：只检查，不交易。由计划任务在交易日 9:45–15:45 每小时运行一次。

检查项目：
  - 连不上券商 / 密钥失效
  - 低于维持保证金、离追缴保证金不到 15%
  - 实际杠杆超过 2.3 倍
  - 单日亏损超过 5%、从最高点回撤超过 15%
  - 订单被拒 / 被撤 / 过期，开盘半小时后仍未成交
  - 上一次自动换仓报错，或者没有按时运行（电脑关机、睡眠）
  - 和券商逐笔核对：
      · 台账里有结果不明的订单
      · 账户里出现不是本系统提交的订单（例如在网页上手动下的单）
      · 券商那边成交了、但台账里没有记录的订单
      · 账户里出现不明来源的持仓
有异常：弹 Windows 通知 + 写入 alerts.txt（同一异常一天只提醒一次）
没异常：只在 logs/monitor_log.txt 记一行，不打扰你

  python monitor.py        正常检查
  python monitor.py test   只发一条测试通知
"""
import os
import sys
import json
import traceback
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import config
import risk
import ledger
import alerts
from paths import rt
from keys import load_keys

LOG = rt("logs", "monitor_log.txt")
MON_STATE = rt("logs", "monitor_state.json")
SNAPSHOT = rt(config.REPORT_DIR, "monitor_last.txt")
PAPER_LOG = rt("logs", "paper_log.txt")
EMERGENCY_LOG = rt("logs", "emergency_log.txt")
MANAGED = set(config.ROTATION_UNIVERSE + [config.CASH_SYMBOL])


def append(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def check_last_run(client, found):
    """上一次 17:00 自动换仓：有没有报错、有没有按时运行。"""
    if not os.path.exists(PAPER_LOG):
        return   # 还没正式运行过（第一次运行前）
    with open(PAPER_LOG, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith("===== ") and "动量轮动" in l]
    last = lines[starts[-1]:] if starts else lines[-30:]
    if any("Traceback" in l or "Error" in l for l in last):
        err = [l for l in last if l.strip()][-1][:150]
        found.append(("paper_error", f"上次自动换仓出错：{err}"))
    try:
        from alpaca.trading.requests import GetCalendarRequest
        today = dt.date.today()
        cal = client.get_calendar(GetCalendarRequest(start=today - dt.timedelta(days=10),
                                                     end=today - dt.timedelta(days=1)))
        prev = max(c.date if isinstance(c.date, dt.date) else dt.date.fromisoformat(str(c.date)[:10])
                   for c in cal)
        due = dt.datetime.combine(prev, dt.time(17, 10))
        ran = dt.datetime.fromtimestamp(os.path.getmtime(PAPER_LOG))
        if ran < due:
            found.append(("paper_missed", f"{prev} 17:00 的自动换仓没有运行（电脑可能关机或睡眠），"
                                          f"最后一次运行是 {ran:%m-%d %H:%M}"))
    except Exception as e:
        print(f"检查运行记录失败：{e}")


def reconcile(client, positions, found):
    """和券商逐笔核对订单与持仓。"""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    still = ledger.resolve(client) if ledger.unresolved_ids() else []
    for cid in still:
        found.append((f"unknown_{cid}", f"订单 {cid} 提交结果无法确认，自动交易会暂停，请核对"))

    start = ledger.ledger_start()
    if start is None:
        return   # 台账还没启用（启用前的订单没有编号，不核对）
    since = max(start, dt.datetime.now() - dt.timedelta(days=5))
    emergency_recent = (os.path.exists(EMERGENCY_LOG) and
                        dt.datetime.fromtimestamp(os.path.getmtime(EMERGENCY_LOG)) > since)
    try:
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=200,
                                                    after=since.astimezone(dt.timezone.utc)))
    except Exception as e:
        print(f"读取订单失败：{e}")
        return
    known = ledger.submitted_ids()
    for o in orders:
        cid = getattr(o, "client_order_id", "") or ""
        sub = o.submitted_at
        if sub is not None and sub.tzinfo is not None and sub < since.astimezone(dt.timezone.utc):
            continue
        status = getattr(o.status, "value", str(o.status))
        if not cid.startswith(ledger.PREFIX):
            if emergency_recent:
                continue   # 紧急停止的清仓单由券商生成编号，属于正常
            found.append((f"foreign_{o.id}", f"发现不是本系统提交的订单：{o.side.value} {o.qty} {o.symbol}"
                                             f"（{status}）。是你在网页/App 上手动下的吗？"))
        elif status in ("filled", "partially_filled") and cid not in known:
            found.append((f"unlogged_{cid}", f"订单 {cid} 已成交，但本地台账没有提交记录，请核对"))

    manual_syms = {e.get("symbol") for e in ledger.entries()
                   if str(e.get("client_order_id", "")).startswith(ledger.PREFIX + "man-")}
    for sym in positions:
        if sym not in MANAGED and sym not in manual_syms:
            found.append((f"pos_{sym}", f"账户里有不明来源的持仓：{sym}（不是策略标的，也不是通过本系统手动下单买的）"))


def main():
    now = dt.datetime.now()
    found, notes = [], []
    key, secret = load_keys()
    if not key or not secret:
        found.append(("no_keys", "找不到 Alpaca 密钥，所有自动交易都无法运行"))
        return finish(now, found, ["无法连接"])

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    try:
        client = TradingClient(key, secret, paper=True)
        clock = client.get_clock()
        acct = client.get_account()
        pos_list = client.get_all_positions()
    except Exception as e:
        found.append(("connect", f"连不上 Alpaca：{str(e)[:120]}"))
        return finish(now, found, ["无法连接"])

    if risk.is_stopped():
        notes.append("紧急停止中（STOP 文件存在）")

    equity = float(acct.equity)
    last_equity = float(acct.last_equity or 0)
    gross = float(acct.long_market_value or 0) + abs(float(acct.short_market_value or 0))
    maint = float(getattr(acct, "maintenance_margin", 0) or 0)
    lev_now = gross / equity if equity > 0 else 0
    dd = risk.update_peak(equity)["drawdown"]
    day = risk.daily_loss(equity, last_equity)
    dist = risk.margin_distance(equity, gross, maint / gross if gross and maint else None)

    if maint and equity < maint:
        found.append(("below_maint", f"净值 ${equity:,.0f} 已低于维持保证金 ${maint:,.0f}，可能被强制平仓！"))
    elif gross > equity and dist < config.ALERT_MARGIN_DISTANCE:
        found.append(("margin_near", f"持仓再跌 {dist:.0%} 就会被追缴保证金"))
    if lev_now > config.ALERT_LEVERAGE:
        found.append(("lev_high", f"实际杠杆 {lev_now:.2f} 倍，超过 {config.ALERT_LEVERAGE:g} 倍警戒线"))
    if day < -config.DAILY_LOSS_LIMIT:
        found.append(("day_loss", f"今天已亏损 {day:.1%}"))
    if dd > config.ALERT_DRAWDOWN:
        found.append(("drawdown", f"账户从最高点回撤 {dd:.1%}；超过 {config.DELEVER_DRAWDOWN:.0%} 会自动降到 1 倍杠杆"))

    # 订单状态
    try:
        recent = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=50))
        cutoff = clock.timestamp - dt.timedelta(days=2)
        for o in recent:
            if o.submitted_at and o.submitted_at > cutoff and o.status.value in ("rejected", "canceled", "expired"):
                found.append((f"order_{o.id}", f"订单{o.status.value}：{o.side.value} {o.qty} {o.symbol}"))
        et = clock.timestamp
        if clock.is_open and et.hour * 60 + et.minute >= 600:   # 美东 10:00 以后
            for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
                if o.order_type.value == "market" and o.submitted_at < et - dt.timedelta(minutes=30):
                    found.append((f"stuck_{o.id}", f"市价单开盘后仍未成交：{o.side.value} {o.qty} {o.symbol}"))
    except Exception as e:
        print(f"检查订单失败：{e}")

    reconcile(client, {p.symbol for p in pos_list}, found)
    check_last_run(client, found)

    summary = (f"净值 ${equity:,.0f}　今日 {day:+.2%}　杠杆 {lev_now:.2f}x　"
               f"回撤 {dd:.1%}　距追缴 {'—' if gross <= equity else f'{dist:.0%}'}　"
               f"{'开盘中' if clock.is_open else '休市'}")
    try:
        import trade
        trade.status(client, (key, secret))     # 顺便刷新 orders/status.txt，给日报用
    except Exception as e:
        print(f"刷新状态失败：{e}")
    finish(now, found, [summary] + notes)


def finish(now, found, info_lines):
    state = {}
    if os.path.exists(MON_STATE):
        try:
            with open(MON_STATE, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    today = f"{now:%Y-%m-%d}"
    sent = state.get(today, [])
    new = [(k, m) for k, m in found if k not in sent]
    for k, m in new:
        alerts.add(m, popup=False)
    if new:
        alerts.notify("Excaliber 交易警报", "；".join(m for _, m in new))
    state = {today: sent + [k for k, _ in new]}
    os.makedirs(os.path.dirname(MON_STATE), exist_ok=True)
    with open(MON_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

    status = f"⚠ {len(found)} 项异常" if found else "正常"
    line = f"{now:%m-%d %H:%M}  {status}  " + "  ".join(info_lines)
    append(LOG, line)
    os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        f.write(line + "\n" + "\n".join(f"  - {m}" for _, m in found))
    print(line)
    for _, m in found:
        print("  - " + m)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        alerts.notify("Excaliber 测试通知", "如果你看到这条消息，说明异常提醒能正常弹出。")
        print("测试通知已发送")
        sys.exit(0)
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        print(err)
        append(LOG, f"{dt.datetime.now():%m-%d %H:%M}  监控程序出错：{err.strip().splitlines()[-1]}")
        alerts.notify("Excaliber 监控出错", err.strip().splitlines()[-1][:200])
