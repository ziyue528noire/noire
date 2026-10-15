# -*- coding: utf-8 -*-
"""安全测试：用假券商 + 临时文件夹，验证所有“写死在代码里”的安全规则。

运行：双击 run_tests.bat，或在本文件夹上一级运行  python -m unittest discover -s tests -v
完全离线，不会连接任何真实账户，也不会改动真实的日志、状态和台账文件。
"""
import os
import sys
import io
import json
import shutil
import tempfile
import unittest
import contextlib
import datetime as dt

# —— 必须在导入项目代码之前：把运行文件指向临时目录，并装上假券商 ——
TMP = tempfile.mkdtemp(prefix="exc_test_")
os.environ["EXC_RUNTIME_DIR"] = os.path.join(TMP, "runtime")
TESTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(TESTS)
sys.path.insert(0, TESTS)
sys.path.insert(0, PROJECT)

import fakebroker                        # noqa: E402
fakebroker.install()
B = fakebroker.BROKER

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402
import config                            # noqa: E402
import data                              # noqa: E402
import keys                              # noqa: E402
import risk                              # noqa: E402
import ledger                            # noqa: E402
import paths                             # noqa: E402

assert paths.RUNTIME.startswith(TMP), "测试没有使用临时目录，停止以免碰到真实文件"

# 假行情：QQQ 最强、EEM 第二，其余较弱，TLT 下跌 → 目标应是 QQQ + EEM
DATA_DIR = os.path.join(TMP, "data")
DRIFT = {"QQQ": 0.0012, "EEM": 0.0009, "SPY": 0.0004, "IWM": 0.0002, "EFA": 0.0001,
         "GLD": 0.0001, "TLT": -0.0004, "SHY": 0.00005}


def make_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    idx = pd.bdate_range("2009-01-01", dt.date.today())
    for i, (s, mu) in enumerate(DRIFT.items()):
        r = np.random.default_rng(i).normal(mu, 0.004, len(idx))
        c = np.exp(np.cumsum(r))
        c = c / c[-1] * 100          # 最新价格固定在 100 附近，整股取整误差小
        pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6},
                     index=idx).to_csv(os.path.join(DATA_DIR, f"{s}.csv"))


make_data()
config.DATA_DIR = DATA_DIR
_orig_load = data.load_prices
data.load_prices = lambda s, refresh=False: _orig_load(s, False)   # 测试中绝不联网下载
keys.load_keys = lambda: ("TEST_KEY", "TEST_SECRET")

import paper_rotation                    # noqa: E402
import trade                             # noqa: E402
import monitor                           # noqa: E402
import emergency                         # noqa: E402
import scoreboard                        # noqa: E402
for m in (paper_rotation, trade, monitor, emergency):
    if hasattr(m, "load_keys"):
        m.load_keys = keys.load_keys


def run(fn, *argv):
    """运行某个入口，返回打印出来的文字。"""
    old = sys.argv
    sys.argv = ["x", *argv]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn()
    finally:
        sys.argv = old
    return buf.getvalue()


def last_price(sym):
    return float(pd.read_csv(os.path.join(DATA_DIR, f"{sym}.csv"))["Close"].iloc[-1])


class SafetyTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(paths.RUNTIME, ignore_errors=True)
        os.makedirs(paths.RUNTIME)
        B.reset()

    # ---------- 自动换仓 ----------
    def test_builds_2x_from_empty_account(self):
        run(paper_rotation.main, "--live")
        buys = [o for o in B.orders if o.side.value == "buy"]
        self.assertEqual({o.symbol for o in buys}, {"QQQ", "EEM"})
        notional = sum(float(o.qty) * last_price(o.symbol) for o in buys)
        self.assertAlmostEqual(notional / 100000, 2 * (1 - config.CASH_BUFFER), delta=0.02)
        self.assertTrue(all(o.client_order_id.startswith("exc-rot-") for o in buys))
        self.assertEqual(B.paper_flags and set(B.paper_flags), {True})

    def test_dry_run_sends_nothing(self):
        out = run(paper_rotation.main)
        self.assertEqual(B.orders, [])
        self.assertIn("空跑", out)

    def test_open_orders_block_second_run(self):
        run(paper_rotation.main, "--live")
        n = len(B.orders)
        out = run(paper_rotation.main, "--live")
        self.assertEqual(len(B.orders), n)
        self.assertIn("还没成交", out)

    def test_same_client_id_never_duplicates(self):
        req = fakebroker.MarketOrderRequest(symbol="QQQ", qty=1, side=fakebroker.OrderSide.BUY,
                                            time_in_force=fakebroker.TimeInForce.DAY,
                                            client_order_id=ledger.make_id("rot", "QQQ", "buy"))
        c = fakebroker.TradingClient("k", "s", paper=True)
        self.assertIsNotNone(ledger.submit(c, req, {}))
        self.assertIsNone(ledger.submit(c, req, {}))          # 第二次被券商拒绝 → 返回 None
        self.assertEqual(len(B.orders), 1)

    def test_delevers_after_loss(self):
        B.equity, B.cash, B.last_equity = 60000, -90000, 62000
        q, e = last_price("QQQ"), last_price("EEM")
        B.positions = {"QQQ": (int(75000 / q), 75000), "EEM": (int(75000 / e), 75000)}
        run(paper_rotation.main, "--live")
        self.assertTrue(B.orders)
        self.assertTrue(all(o.side.value == "sell" for o in B.orders))

    def test_circuit_breaker_drops_to_1x(self):
        risk.save_state({"peak_equity": 100000})
        B.equity, B.cash, B.last_equity = 75000, -75000, 76000
        q, e = last_price("QQQ"), last_price("EEM")
        B.positions = {"QQQ": (int(75000 / q), 75000), "EEM": (int(75000 / e), 75000)}
        out = run(paper_rotation.main, "--live")
        self.assertIn("熔断", out)
        sold = sum(float(o.qty) * last_price(o.symbol) for o in B.orders if o.side.value == "sell")
        self.assertGreater(sold, 60000)        # 15 万持仓要降到约 7.35 万
        self.assertFalse(any(o.side.value == "buy" for o in B.orders))

    def test_daily_loss_blocks_buys(self):
        B.equity, B.cash, B.last_equity = 94000, 94000, 100000
        out = run(paper_rotation.main, "--live")
        self.assertIn("只允许卖出", out)
        self.assertEqual(B.orders, [])

    def test_stop_file_blocks_everything(self):
        open(risk.STOP_FILE, "w").close()
        run(paper_rotation.main, "--live")
        self.assertEqual(B.orders, [])

    # ---------- 结果不明的订单 ----------
    def test_lost_response_stops_and_resolves_next_run(self):
        B.fail_next_submit = "lost_response"     # 券商收到了，但回复丢了
        out = run(paper_rotation.main, "--live")
        self.assertEqual(len(B.orders), 1)       # 只发出第一笔，随即停止
        self.assertIn("结果未知", out)
        self.assertIn("其余订单都没有提交", out)
        self.assertTrue(ledger.unresolved_ids())
        with open(os.path.join(paths.RUNTIME, "alerts.txt"), encoding="utf-8") as f:
            self.assertIn("结果未知", f.read())
        run(paper_rotation.main, "--live")      # 下次运行：先按编号查到 → 落实
        self.assertEqual(ledger.unresolved_ids(), [])

    def test_unverifiable_order_blocks_trading(self):
        B.fail_next_submit = "not_received"
        run(paper_rotation.main, "--live")
        self.assertEqual(B.orders, [])
        B.lookup_error = True                   # 查询也失败 → 不能交易
        out = run(paper_rotation.main, "--live")
        self.assertIn("无法确认", out)
        self.assertEqual(B.orders, [])
        B.lookup_error = False                  # 查询恢复：券商确认没收到 → 恢复交易
        run(paper_rotation.main, "--live")
        self.assertTrue(B.orders)

    # ---------- 手动下单 ----------
    def _place(self, orders):
        os.makedirs(trade.ORD_DIR, exist_ok=True)
        with open(trade.ORDER_FILE, "w", encoding="utf-8") as f:
            json.dump({"orders": orders}, f)
        c = fakebroker.TradingClient("k", "s", paper=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trade.place(c, ("k", "s"))
        return buf.getvalue()

    def test_manual_rules(self):
        B.cash = 5000
        B.positions = {"NVDA": (2, 400)}
        out = self._place([{"symbol": "AAPL", "side": "buy", "qty": 1000},     # 超出现金
                           {"symbol": "TSLA", "side": "sell", "qty": 3},       # 没持有 → 不做空
                           {"symbol": "NVDA", "side": "sell", "qty": 5},       # 卖得比持有多
                           {"symbol": "AAPL", "side": "buy", "qty": 5, "extended_hours": True},  # 盘后需限价
                           {"symbol": "XXXX", "side": "buy", "qty": 1},        # 不可交易
                           {"symbol": "AAPL", "side": "buy", "qty": 10}])      # 合法
        self.assertEqual(out.count("✗ 拒绝"), 5)
        self.assertEqual(len(B.orders), 1)
        self.assertTrue(B.orders[0].client_order_id.startswith("exc-man-"))
        self.assertFalse(os.path.exists(trade.ORDER_FILE))                  # 执行后移走

    def test_manual_same_file_twice_blocked(self):
        order = [{"symbol": "AAPL", "side": "buy", "qty": 1}]
        self._place(order)
        out = self._place(order)                                             # 同一内容再放一次
        self.assertEqual(len(B.orders), 1)
        self.assertIn("之前已经提交过", out)

    def test_manual_refused_when_stopped(self):
        open(risk.STOP_FILE, "w").close()
        out = self._place([{"symbol": "AAPL", "side": "buy", "qty": 1}])
        self.assertIn("拒绝下单", out)
        self.assertEqual(B.orders, [])

    # ---------- 风控细节 ----------
    def test_first_day_daily_loss_uses_first_seen_equity(self):
        self.assertAlmostEqual(risk.daily_loss(100000, 0), 0.0)
        self.assertAlmostEqual(risk.daily_loss(94000, 0), -0.06)          # 不再恒为 0

    def test_margin_distance(self):
        self.assertAlmostEqual(risk.margin_distance(60000, 150000, 0.3), 1 / 7, places=3)
        self.assertEqual(risk.margin_distance(100000, 50000), 1.0)

    # ---------- 监控与核对 ----------
    def test_monitor_flags_foreign_order_and_unknown_position(self):
        ledger.record("NOTE", text="start")                                  # 启用台账
        B.add_order("TSLA", "buy", 1, "filled", client_order_id="web-12345")
        B.positions = {"TSLA": (1, 250)}
        run(monitor.main)
        with open(os.path.join(paths.RUNTIME, "alerts.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("不是本系统提交的订单", text)
        self.assertIn("不明来源的持仓", text)

    def test_monitor_quiet_when_normal(self):
        run(paper_rotation.main, "--live")
        for o in B.orders:
            o.status = fakebroker._Status("filled")
            o.filled_qty = o.qty
        run(monitor.main)
        self.assertFalse(os.path.exists(os.path.join(paths.RUNTIME, "alerts.txt")))

    # ---------- 紧急停止 ----------
    def test_emergency_stop(self):
        B.positions = {"QQQ": (10, 7000)}
        B.add_order("EEM", "buy", 5, "accepted")
        run(emergency.stop)
        self.assertTrue(risk.is_stopped())
        self.assertTrue(any(o.status.value == "canceled" for o in B.orders))
        self.assertTrue(any(o.symbol == "QQQ" and o.side.value == "sell" for o in B.orders))
        run(emergency.resume)
        self.assertFalse(risk.is_stopped())

    # ---------- 成绩单 ----------
    def test_scoreboard_seeds_start_and_compares_spy(self):
        with open(paper_rotation.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": "2026-01-02", "start_equity": 100000, "spy_start": 500}, f)
        paper_rotation.record_score(dt.date(2026, 1, 5), 102000, 505, 1.9, 0.0)
        rows = scoreboard.read(scoreboard.HISTORY)
        self.assertEqual([r["date"] for r in rows], ["2026-01-02", "2026-01-05"])
        s = scoreboard.stats(rows, {r["date"]: float(r["spy_close"]) for r in rows})
        self.assertAlmostEqual(s["total"], 0.02)
        self.assertAlmostEqual(s["spy"], 0.01)

    def test_shared_scoreboard_writes_only_own_file(self):
        os.makedirs(scoreboard.SHARED_DIR, exist_ok=True)
        self.assertTrue(scoreboard.SHARED_DIR.startswith(TMP))           # 测试绝不碰真实 D:\\scoreboard
        other = os.path.join(scoreboard.SHARED_DIR, "gpt.csv")
        with open(other, "w", encoding="utf-8") as f:
            f.write("date,equity\n2026-01-02,99000\n2026-01-05,99100\n")
        before = open(other, encoding="utf-8").read()
        with open(paper_rotation.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": "2026-01-02", "start_equity": 100000, "spy_start": 500}, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            paper_rotation.record_score(dt.date(2026, 1, 5), 102000, 505, 1.9, 0.0)
        mine = scoreboard.read(scoreboard.MY_SHARED)
        self.assertEqual([r["equity"] for r in mine], ["100000.00", "102000.00"])
        self.assertEqual(open(other, encoding="utf-8").read(), before)  # 对方文件原封不动
        self.assertIn("ChatGPT", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
