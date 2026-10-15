# -*- coding: utf-8 -*-
"""手动下单通道（只连 Alpaca 模拟盘）

  python trade.py status   查看账户、持仓、盈亏、未成交订单 → orders/status.txt
  python trade.py place    执行 orders/order.json 里的订单   → orders/result.txt

order.json 格式示例：
{
  "orders": [
    {"symbol": "NVDA", "side": "buy",  "qty": 10},                                  # 市价买入
    {"symbol": "EEM",  "side": "sell", "qty": "all"},                               # 全部卖出
    {"symbol": "AAPL", "side": "buy",  "qty": 5, "limit_price": 200.5},             # 限价单
    {"symbol": "AAPL", "side": "buy",  "qty": 5, "limit_price": 200.5, "extended_hours": true}  # 盘前盘后
  ],
  "cancel_open_orders": false
}

安全规则（写死在代码里）：
  - 只连模拟盘（paper=True）
  - 不借钱：买入金额不能超过可用现金
  - 不做空：卖出数量不能超过持有数量
  - order.json 执行后立刻移到 orders/done/，不会被重复执行
"""
import os
import sys
import json
import time
import datetime as dt

from keys import load_keys

import hashlib
import ledger
import alerts
from paths import rt

ORD_DIR = rt("orders")
ORDER_FILE = os.path.join(ORD_DIR, "order.json")
TRADE_LOG = rt("logs", "trades.csv")


def get_clients():
    key, secret = load_keys()
    if not key or not secret:
        sys.exit("没找到密钥：请先双击 set_keys.bat")
    from alpaca.trading.client import TradingClient
    return TradingClient(key, secret, paper=True), (key, secret)   # 只连模拟盘


def latest_price(sym, keys):
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        dc = StockHistoricalDataClient(*keys)
        return float(dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sym))[sym].price)
    except Exception:
        import yfinance as yf
        return float(yf.Ticker(sym).history(period="5d")["Close"].iloc[-1])


def fmt_order(o):
    px = f" 限价${float(o.limit_price):.2f}" if getattr(o, "limit_price", None) else " 市价"
    filled = ""
    if getattr(o, "filled_qty", None) and float(o.filled_qty) > 0:
        filled = f"，已成交 {float(o.filled_qty):g} 股 @ ${float(o.filled_avg_price):.2f}"
    side = "买入" if o.side.value == "buy" else "卖出"
    return f"{side} {o.symbol} {float(o.qty or 0):g} 股{px}　状态：{o.status.value}{filled}"


def status(client, keys, out=None):
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    clock = client.get_clock()
    acct = client.get_account()
    lines = [f"===== 模拟盘状态 {dt.datetime.now():%Y-%m-%d %H:%M} =====",
             f"美股现在：{'开盘中' if clock.is_open else '休市'}　"
             f"下次开盘 {clock.next_open:%m-%d %H:%M}　下次收盘 {clock.next_close:%m-%d %H:%M}（美东时间）",
             f"账户净值：${float(acct.equity):,.2f}　现金：${float(acct.cash):,.2f}",
             f"今日盈亏：{day_pnl_text(acct)}", "", "持仓："]
    pos = client.get_all_positions()
    for p in pos:
        lines.append(f"  {p.symbol:<6} {float(p.qty):>8g} 股　成本 ${float(p.avg_entry_price):,.2f}　"
                     f"现价 ${float(p.current_price):,.2f}　市值 ${float(p.market_value):,.0f}　"
                     f"浮动盈亏 ${float(p.unrealized_pl):+,.0f} ({float(p.unrealized_plpc):+.2%})")
    if not pos:
        lines.append("  无")
    lines += ["", "未成交订单："]
    opens = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    lines += [f"  {fmt_order(o)}" for o in opens] or ["  无"]
    lines += ["", "最近 10 笔已结束订单："]
    closed = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=10))
    lines += [f"  {o.submitted_at:%m-%d %H:%M} {fmt_order(o)}" for o in closed] or ["  无"]
    text = "\n".join(lines)
    print(text)
    os.makedirs(ORD_DIR, exist_ok=True)
    with open(out or os.path.join(ORD_DIR, "status.txt"), "w", encoding="utf-8") as f:
        f.write(text)


def place(client, keys):
    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

    out = [f"===== 下单结果 {dt.datetime.now():%Y-%m-%d %H:%M} ====="]
    import risk
    if risk.is_stopped():
        out.append("紧急停止中（存在 STOP 文件），拒绝下单。双击 resume_trading.bat 可恢复。")
        return finish(out)
    if not os.path.exists(ORDER_FILE):
        out.append("没有找到 orders/order.json，什么都没做")
        return finish(out)
    with open(ORDER_FILE, "rb") as f:
        raw = f.read()
    req = json.loads(raw.decode("utf-8-sig"))
    file_tag = hashlib.sha256(raw).hexdigest()[:8]   # 同一个订单文件的编号固定 → 重复执行会被券商拒绝
    # 先移走，保证同一个订单文件绝不会被执行两次
    os.makedirs(os.path.join(ORD_DIR, "done"), exist_ok=True)
    os.replace(ORDER_FILE, os.path.join(ORD_DIR, "done", f"{dt.datetime.now():%Y%m%d_%H%M%S}.json"))

    if req.get("cancel_open_orders"):
        client.cancel_orders()
        out.append("已撤销所有未成交订单")
        time.sleep(1)

    still = ledger.resolve(client)
    if still:
        out.append(f"有订单提交结果仍无法确认（{', '.join(still)}），为防重复下单，本次拒绝下单")
        return finish(out)

    clock = client.get_clock()
    out.append(f"美股现在：{'开盘中' if clock.is_open else '休市（市价单会在下次开盘成交）'}")
    acct = client.get_account()
    cash = float(acct.cash)
    # 已挂着的买单会占用现金
    for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
        if o.side == OrderSide.BUY:
            px = float(o.limit_price) if o.limit_price else latest_price(o.symbol, keys)
            cash -= (float(o.qty or 0) - float(o.filled_qty or 0)) * px
    held = {p.symbol: float(p.qty) for p in client.get_all_positions()}

    submitted = []
    for idx, item in enumerate(req.get("orders", [])):
        sym = str(item.get("symbol", "")).upper().strip()
        side = str(item.get("side", "")).lower()
        qty = item.get("qty")
        limit = item.get("limit_price")
        ext = bool(item.get("extended_hours", False))
        tag = f"{sym} {side} {qty}"
        try:
            if side not in ("buy", "sell"):
                raise ValueError("side 只能是 buy 或 sell")
            if ext and not limit:
                raise ValueError("盘前盘后只能用限价单，请加 limit_price")
            asset = client.get_asset(sym)
            if not asset.tradable:
                raise ValueError(f"{sym} 不能交易")
            if side == "sell":
                have = held.get(sym, 0)
                qty = have if qty == "all" else float(qty)
                if qty <= 0 or qty > have:
                    raise ValueError(f"持有 {have:g} 股，不能卖 {qty:g} 股（不做空）")
            else:
                qty = int(qty)
                if qty <= 0:
                    raise ValueError("买入数量必须大于 0")
                px = float(limit) if limit else latest_price(sym, keys)
                if qty * px > cash:
                    raise ValueError(f"需要约 ${qty * px:,.0f}，可用现金只有 ${cash:,.0f}（不借钱）")
                cash -= qty * px

            osd = OrderSide.BUY if side == "buy" else OrderSide.SELL
            cid = ledger.make_id("man", sym, side, extra=f"{file_tag}{idx}")
            if limit:
                r = LimitOrderRequest(symbol=sym, qty=qty, side=osd, time_in_force=TimeInForce.DAY,
                                      limit_price=round(float(limit), 2), extended_hours=ext,
                                      client_order_id=cid)
            else:
                r = MarketOrderRequest(symbol=sym, qty=qty, side=osd, time_in_force=TimeInForce.DAY,
                                       client_order_id=cid)
            o = ledger.submit(client, r, {"symbol": sym, "side": side, "qty": qty, "why": "手动下单"})
            if o is None:
                out.append(f"– 跳过 {tag}：这笔订单之前已经提交过（编号 {cid}），不重复下单")
                continue
            submitted.append(o.id)
            log_trade(sym, side, qty, limit or "市价", item.get("note", "手动下单"))
        except ledger.UncertainOrder as e:
            msg = f"手动下单：{e}。已停止后续订单，下次会先向券商核实"
            out.append(f"✗ {msg}")
            alerts.add(msg)
            break
        except Exception as e:
            out.append(f"✗ 拒绝 {tag}：{e}")

    if submitted:
        time.sleep(3)   # 开盘时间市价单通常几秒内成交
        out.append("已提交：")
        out += [f"  ✓ {fmt_order(client.get_order_by_id(i))}" for i in submitted]
    finish(out)


def day_pnl_text(acct) -> str:
    import risk
    eq, last = float(acct.equity), float(acct.last_equity or 0)
    base = risk.day_base(eq, last)
    note = "" if last > 0 else "（券商没有昨日净值，按今天首次记录的净值算）"
    return f"${eq - base:+,.2f}（{eq / base - 1:+.2%}）{note}" if base else "无数据"


def log_trade(sym, side, qty, price, why):
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    new = not os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", encoding="utf-8-sig") as f:
        if new:
            f.write("时间,标的,方向,股数,参考价,原因\n")
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M},{sym},{side},{qty:g},{price},{why}\n")


def finish(lines):
    text = "\n".join(lines)
    print(text)
    os.makedirs(ORD_DIR, exist_ok=True)
    with open(os.path.join(ORD_DIR, "result.txt"), "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    c, k = get_clients()
    if mode == "place":
        place(c, k)
        print()
    status(c, k)
