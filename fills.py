# -*- coding: utf-8 -*-
"""成交记录：把本系统订单（编号以 noire- 开头）的实际成交价记到 logs/fills.csv。

由 monitor.py 每小时调用（只读券商数据，不交易）；同一笔订单只记一次。
tracking.py 用它来算“成交价比开盘价吃亏多少”。
"""
import os
import csv

from paths import rt

FILLS = rt("logs", "fills.csv")
FIELDS = ["filled_at", "date", "symbol", "side", "qty", "price", "client_order_id", "broker_id", "status"]


def read() -> list:
    if not os.path.exists(FILLS):
        return []
    with open(FILLS, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def record(orders, prefix="noire-") -> int:
    """orders：券商订单对象列表。只记已（部分）成交、且以前没记过的。返回新记录条数。
    部分成交的订单完全成交后会再记一次最终状态（按 broker_id + status 去重）。"""
    seen = {(r["broker_id"], r["status"]) for r in read()}
    new = []
    for o in orders:
        cid = str(getattr(o, "client_order_id", "") or "")
        status = getattr(getattr(o, "status", None), "value", str(getattr(o, "status", "")))
        if not cid.startswith(prefix) or status not in ("filled", "partially_filled"):
            continue
        price = getattr(o, "filled_avg_price", None)
        qty = getattr(o, "filled_qty", None)
        if price in (None, "", "0") or qty in (None, "", "0"):
            continue
        key = (str(o.id), status)
        if key in seen:
            continue
        seen.add(key)
        when = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
        new.append({"filled_at": str(when), "date": str(when)[:10], "symbol": o.symbol,
                    "side": getattr(o.side, "value", str(o.side)), "qty": str(qty), "price": str(price),
                    "client_order_id": cid, "broker_id": str(o.id), "status": status})
    if new:
        os.makedirs(os.path.dirname(FILLS), exist_ok=True)
        exists = os.path.exists(FILLS)
        with open(FILLS, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerows(new)
    return len(new)
