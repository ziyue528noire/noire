# -*- coding: utf-8 -*-
"""测试用的假券商：模拟 alpaca-py 的接口，完全离线，不会连到任何真实账户。

用法：在导入项目代码之前 `import fakebroker; fakebroker.install()`。
测试里通过 fakebroker.BROKER 设置账户状态、注入故障，并检查收到了哪些订单。
"""
import sys
import enum
import types
import uuid
import datetime as dt

NS = types.SimpleNamespace


class OrderSide(enum.Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(enum.Enum):
    DAY = "day"


class QueryOrderStatus(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class _Status:
    def __init__(self, v):
        self.value = v


class FakeAPIError(Exception):
    """模仿 alpaca-py 的 APIError：str() 是券商返回的 JSON，status_code 是 HTTP 状态码。"""

    def __init__(self, text, status_code):
        super().__init__(text)
        self.status_code = status_code


class _Req:
    def __init__(self, **kw):
        self.client_order_id = None
        self.__dict__.update(kw)


class MarketOrderRequest(_Req):
    order_type = "market"


class LimitOrderRequest(_Req):
    order_type = "limit"


class GetOrdersRequest(_Req):
    pass


class GetCalendarRequest(_Req):
    pass


class StockLatestTradeRequest(_Req):
    pass


OPEN_STATES = {"new", "accepted", "pending_new", "partially_filled"}


class Broker:
    """一个假账户。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.equity = 100000.0
        self.cash = 100000.0
        self.last_equity = 100000.0
        self.regt_buying_power = 200000.0
        self.buying_power = None     # None = 按 regt 的 2 倍；设了数字就用它（模拟券商“现在”的购买力）
        self.maintenance_margin = 0.0
        self.positions = {}          # symbol -> (qty, market_value)
        self.orders = []             # 订单对象列表
        self.fail_next_submit = None # None / "lost_response" / "not_received" / "rejected"（券商明确拒绝）
        self.lookup_error = False    # True = 按编号查询时网络出错
        self.is_open = False
        self.paper_flags = []
        self.price = 100.0

    # —— 测试辅助 ——
    def add_order(self, symbol, side, qty, status="accepted", client_order_id=None, when=None):
        o = NS(id=str(uuid.uuid4()), client_order_id=client_order_id or str(uuid.uuid4()),
               symbol=symbol, qty=str(qty), side=OrderSide(side), status=_Status(status),
               order_type=_Status("market"), limit_price=None, filled_qty="0", filled_avg_price=None,
               submitted_at=when or dt.datetime.now(dt.timezone.utc))
        self.orders.append(o)
        return o

    def submitted(self):
        return [o for o in self.orders]


BROKER = Broker()


class TradingClient:
    def __init__(self, key, secret, paper=True):
        BROKER.paper_flags.append(paper)
        if paper is not True:
            raise AssertionError("测试发现程序试图连接实盘！")

    def get_account(self):
        b = BROKER
        lmv = sum(v for q, v in b.positions.values())
        return NS(equity=str(b.equity), cash=str(b.cash), last_equity=str(b.last_equity),
                  buying_power=str(b.regt_buying_power * 2 if b.buying_power is None else b.buying_power),
                  regt_buying_power=str(b.regt_buying_power),
                  long_market_value=str(lmv), short_market_value="0",
                  maintenance_margin=str(b.maintenance_margin), status="ACTIVE", trading_blocked=False)

    def get_all_positions(self):
        return [NS(symbol=s, qty=str(q), market_value=str(v), avg_entry_price="100", current_price="100",
                   unrealized_pl="0", unrealized_plpc="0") for s, (q, v) in BROKER.positions.items()]

    def get_orders(self, req=None):
        st = getattr(req, "status", QueryOrderStatus.OPEN) if req else QueryOrderStatus.OPEN
        if st == QueryOrderStatus.OPEN:
            return [o for o in BROKER.orders if o.status.value in OPEN_STATES]
        if st == QueryOrderStatus.CLOSED:
            return [o for o in BROKER.orders if o.status.value not in OPEN_STATES]
        return list(BROKER.orders)

    def submit_order(self, req):
        b = BROKER
        cid = req.client_order_id or str(uuid.uuid4())
        if any(o.client_order_id == cid for o in b.orders):
            raise Exception('{"code":40010001,"message":"client_order_id must be unique"}')
        mode, b.fail_next_submit = b.fail_next_submit, None
        if mode == "not_received":
            raise TimeoutError("read timed out")
        if mode == "rejected":
            raise FakeAPIError('{"code":40310000,"message":"insufficient buying power"}', 403)
        o = b.add_order(req.symbol, req.side.value, req.qty, "accepted", cid)
        if getattr(req, "limit_price", None):
            o.limit_price = str(req.limit_price)
            o.order_type = _Status("limit")
        if mode == "lost_response":
            raise ConnectionError("connection reset by peer")
        return o

    def get_order_by_client_id(self, cid):
        if BROKER.lookup_error:
            raise ConnectionError("network unreachable")
        for o in BROKER.orders:
            if o.client_order_id == cid:
                return o
        raise Exception('{"code":40410000,"message":"order not found"}')

    def get_order_by_id(self, oid):
        for o in BROKER.orders:
            if o.id == oid:
                return o
        raise Exception("order not found")

    def get_calendar(self, req):
        d, out = req.start, []
        while d <= req.end:
            if d.weekday() < 5:
                out.append(NS(date=d))
            d += dt.timedelta(days=1)
        return out

    def get_clock(self):
        now = dt.datetime.now(dt.timezone.utc)
        return NS(is_open=BROKER.is_open, next_open=now, next_close=now, timestamp=now)

    def get_asset(self, sym):
        return NS(tradable=(sym != "XXXX"))

    def cancel_orders(self):
        for o in BROKER.orders:
            if o.status.value in OPEN_STATES:
                o.status = _Status("canceled")

    def close_all_positions(self, cancel_orders=True):
        for s, (q, v) in list(BROKER.positions.items()):
            BROKER.add_order(s, "sell", q, "accepted")


class StockHistoricalDataClient:
    def __init__(self, *a, **k):
        pass

    def get_stock_latest_trade(self, req):
        return {req.symbol_or_symbols: NS(price=BROKER.price)}


def install():
    """把假的 alpaca 模块装进 sys.modules（必须在项目代码导入 alpaca 之前调用）。"""
    mods = {
        "alpaca": types.ModuleType("alpaca"),
        "alpaca.trading": types.ModuleType("alpaca.trading"),
        "alpaca.trading.client": types.ModuleType("alpaca.trading.client"),
        "alpaca.trading.requests": types.ModuleType("alpaca.trading.requests"),
        "alpaca.trading.enums": types.ModuleType("alpaca.trading.enums"),
        "alpaca.data": types.ModuleType("alpaca.data"),
        "alpaca.data.historical": types.ModuleType("alpaca.data.historical"),
        "alpaca.data.requests": types.ModuleType("alpaca.data.requests"),
    }
    mods["alpaca.trading.client"].TradingClient = TradingClient
    for name in ("MarketOrderRequest", "LimitOrderRequest", "GetOrdersRequest", "GetCalendarRequest"):
        setattr(mods["alpaca.trading.requests"], name, globals()[name])
    for name in ("OrderSide", "TimeInForce", "QueryOrderStatus"):
        setattr(mods["alpaca.trading.enums"], name, globals()[name])
    mods["alpaca.data.historical"].StockHistoricalDataClient = StockHistoricalDataClient
    mods["alpaca.data.requests"].StockLatestTradeRequest = StockLatestTradeRequest
    sys.modules.update(mods)
