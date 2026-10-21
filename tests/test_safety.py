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
# 假行情固定长度、以今天结尾，而且是确定的（不用随机数）：每天的形状完全一样，只是日期往后挪。
# 以前是“随机走势画到今天”，每过一天就多一个随机数，测试依赖的排名和趋势会慢慢变——
# 模拟发现 2027-02-12 起测试会失败，而自检失败会让当天停止交易（2026-10-16 修正）。
N_DAYS = 4600


def fake_index():
    return pd.bdate_range(end=dt.date.today(), periods=N_DAYS)


def fake_path(mu, wiggle=0.004, n=N_DAYS):
    """每天固定涨 mu，再加一天 +wiggle、一天 −wiggle 的小波动（年化波动约 6%）；最后一天价格 = 100（整股取整误差小）。"""
    c = np.exp(np.cumsum(mu + np.where(np.arange(n) % 2 == 0, wiggle, -wiggle)))
    return c / c[-1] * 100


def write_prices(sym, c, idx=None):
    idx = fake_index() if idx is None else idx
    pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6},
                 index=idx).to_csv(os.path.join(DATA_DIR, f"{sym}.csv"))


def make_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    for s, mu in DRIFT.items():
        write_prices(s, fake_path(mu))


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
import paper_job                         # noqa: E402
paper_job.run_preflight = lambda: True    # 测试里不再递归运行整套测试
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
        out = run(paper_rotation.main, "--live")
        buys = [o for o in B.orders if o.side.value == "buy"]
        self.assertEqual({o.symbol for o in buys}, {"QQQ", "EEM"})
        notional = sum(float(o.qty) * last_price(o.symbol) for o in buys)
        # 假行情很平稳、SPY 在 200 日线上方 → 波动目标给出上限 2 倍
        self.assertAlmostEqual(notional / 100000, 2 * (1 - config.CASH_BUFFER), delta=0.02)
        self.assertTrue(all(o.client_order_id.startswith("noire-rot-") for o in buys))
        self.assertEqual(B.paper_flags and set(B.paper_flags), {True})
        self.assertIn("四批信号", out)
        self.assertIn("杠杆 2.00 倍", out)

    def test_dry_run_sends_nothing(self):
        out = run(paper_rotation.main)
        self.assertEqual(B.orders, [])
        self.assertIn("空跑", out)

    def test_dry_run_does_not_overwrite_live_status(self):
        run(paper_rotation.main, "--live")
        with open(paper_rotation.STATUS_FILE, encoding="utf-8") as f:
            live_text = f.read()
        run(paper_rotation.main)                         # 空跑（这时还有未成交订单）
        with open(paper_rotation.STATUS_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), live_text)        # 正式状态没有被空跑覆盖
        with open(paper_rotation.DRYRUN_STATUS_FILE, encoding="utf-8") as f:
            self.assertIn("空跑", f.read())

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

    def test_drawdown_alone_no_longer_forces_1x(self):
        # 2026-10-15 起取消“回撤 >20% 降到 1 倍”的熔断：回撤 25% 但市场平静、趋势向上 → 仍按 2 倍目标
        risk.save_state({"peak_equity": 100000})
        B.equity, B.cash, B.last_equity = 75000, -72000, 76000
        q, e = last_price("QQQ"), last_price("EEM")
        B.positions = {"QQQ": (int(73500 / q), 73500), "EEM": (int(73500 / e), 73500)}
        out = run(paper_rotation.main, "--live")
        self.assertNotIn("熔断", out)
        sold = sum(float(o.qty) * last_price(o.symbol) for o in B.orders if o.side.value == "sell")
        self.assertLess(sold, 10000)           # 不会再把 14.7 万持仓砍到 7 万多

    def test_downtrend_caps_leverage_at_1x(self):
        spy_path = os.path.join(DATA_DIR, "SPY.csv")
        with open(spy_path, encoding="utf-8") as f:
            original = f.read()
        write_prices("SPY", fake_path(-0.0015))              # SPY 一路下跌，远在 200 日均线下方
        try:
            out = run(paper_rotation.main, "--live")
        finally:
            with open(spy_path, "w", encoding="utf-8") as f:
                f.write(original)
        self.assertIn("低于 200 日均线", out)
        buys = [o for o in B.orders if o.side.value == "buy"]
        notional = sum(float(o.qty) * last_price(o.symbol) for o in buys)
        self.assertAlmostEqual(notional / 100000, 1 * (1 - config.CASH_BUFFER), delta=0.02)

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

    def test_broker_rejection_is_not_treated_as_unknown(self):
        B.fail_next_submit = "rejected"          # 券商明确拒绝第一笔（例如购买力不足）
        out = run(paper_rotation.main, "--live")
        self.assertIn("被券商拒绝", out)
        self.assertNotIn("结果未知", out)
        self.assertEqual(ledger.unresolved_ids(), [])        # 订单肯定没下成，不用核实，不挡下一次运行
        self.assertEqual(len(B.orders), 1)                   # 第一笔被拒，第二笔照常提交
        with open(os.path.join(paths.RUNTIME, "alerts.txt"), encoding="utf-8") as f:
            self.assertIn("被券商拒绝", f.read())
        out = run(paper_rotation.main, "--live")
        self.assertNotIn("无法确认", out)

    def test_buys_capped_by_current_buying_power(self):
        B.buying_power = 50000                   # 券商“现在”只给 5 万购买力（卖单成交前卖出的钱不算）
        out = run(paper_rotation.main, "--live")
        notional = sum(float(o.qty) * last_price(o.symbol) for o in B.orders if o.side.value == "buy")
        self.assertLessEqual(notional, 50000 * 0.97 + 1)     # 不会超过券商购买力而被拒
        self.assertGreater(notional, 40000)
        self.assertIn("购买力不够", out)

    def test_data_check_flags_stale_or_partial_data(self):
        c = fakebroker.TradingClient("k", "s", paper=True)
        fri, mon = dt.date(2026, 10, 16), dt.date(2026, 10, 19)
        notes, today = paper_rotation.check_data(c, {"SPY": mon, "QQQ": mon}, mon, dt.datetime(2026, 10, 19, 17, 0))
        self.assertEqual(notes, [])
        self.assertTrue(today)
        notes, _ = paper_rotation.check_data(c, {"SPY": fri, "QQQ": fri}, fri, dt.datetime(2026, 10, 19, 17, 0))
        self.assertIn("不是最新", notes[0])                  # 周一 17:00 还只有周五的数据
        notes, _ = paper_rotation.check_data(c, {"SPY": fri, "QQQ": fri}, fri, dt.datetime(2026, 10, 19, 10, 0))
        self.assertEqual(notes, [])                          # 周一上午还没收盘，周五就是最新的
        notes, _ = paper_rotation.check_data(c, {"SPY": mon, "QQQ": fri}, mon, dt.datetime(2026, 10, 19, 17, 0))
        self.assertIn("QQQ", notes[0])                       # 有一只缺最新一天
        _, today = paper_rotation.check_data(c, {"SPY": fri}, fri, dt.datetime(2026, 10, 17, 17, 0))
        self.assertFalse(today)                              # 周六不是交易日

    def test_bad_price_data_blocks_trading(self):
        path = os.path.join(DATA_DIR, "QQQ.csv")
        with open(path, encoding="utf-8") as f:
            original = f.read()
        c = fake_path(DRIFT["QQQ"])
        c[-1] = c[-2] / 2                                     # 例如拆股没调整：一天“跌了一半”
        write_prices("QQQ", c)
        try:
            with self.assertRaises(SystemExit) as cm:
                run(paper_rotation.main, "--live")
        finally:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
        self.assertIn("行情数据异常", str(cm.exception))
        self.assertEqual(B.orders, [])

    def test_sell_never_exceeds_holding(self):
        # 券商给的市值比本地收盘价高很多（例如盘后大涨），按市值算出的卖出股数可能超过持有数 → 必须封顶，绝不做空
        B.equity, B.cash, B.last_equity = 100000, -150000, 100000
        B.positions = {"QQQ": (100, 150000), "EEM": (1000, 100000)}
        run(paper_rotation.main, "--live")
        held = {s: q for s, (q, v) in B.positions.items()}
        for o in B.orders:
            if o.side.value == "sell":
                self.assertLessEqual(float(o.qty), held[o.symbol])

    def test_no_scoreboard_row_on_market_holiday(self):
        orig, today = fakebroker.TradingClient.get_calendar, dt.date.today()
        fakebroker.TradingClient.get_calendar = lambda self, req: [d for d in orig(self, req) if d.date != today]
        try:
            run(paper_rotation.main, "--live")
        finally:
            fakebroker.TradingClient.get_calendar = orig
        self.assertEqual(scoreboard.read(scoreboard.HISTORY), [])       # 休市日不记成绩单

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

    def test_status_shows_fill_time_not_queue_time(self):
        # 收盘后排队的单，券商的“提交时间”是第二天 08:00（UTC）；状态里应该显示成交时间（本地时间）
        sub = dt.datetime(2026, 10, 19, 8, 0, tzinfo=dt.timezone.utc)
        fil = dt.datetime(2026, 10, 19, 13, 35, tzinfo=dt.timezone.utc)
        self.assertEqual(trade.when_text(fakebroker.NS(submitted_at=sub, filled_at=fil)), f"{fil.astimezone():%m-%d %H:%M}")
        self.assertEqual(trade.when_text(fakebroker.NS(submitted_at=sub, filled_at=None)), f"{sub.astimezone():%m-%d %H:%M}")

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
        self.assertTrue(B.orders[0].client_order_id.startswith("noire-man-"))
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

    def _run_with_data_notes(self, notes):
        # 券商 17:00 的市值含盘后价格（QQQ 160、EEM 99），而当天收盘价都是 100
        B.equity, B.cash, B.last_equity = 100000, -15000, 100000
        B.positions = {"QQQ": (100, 16000), "EEM": (1000, 99000)}
        orig = paper_rotation.check_data
        paper_rotation.check_data = lambda *a, **k: (notes, True)      # 固定成“今天是交易日”，不依赖今天星期几
        try:
            run(paper_rotation.main, "--live")
        finally:
            paper_rotation.check_data = orig
        return scoreboard.read(scoreboard.HISTORY)[-1]

    def test_scoreboard_records_closing_equity(self):
        with open(paper_rotation.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": "2026-01-02", "start_equity": 100000, "spy_start": last_price("SPY")}, f)
        row = self._run_with_data_notes([])
        want = -15000 + 100 * last_price("QQQ") + 1000 * last_price("EEM")
        self.assertEqual(row["equity"], f"{want:.2f}")                   # 收盘净值：现金 + 持仓 × 收盘价
        self.assertEqual(row["broker_equity"], "100000.00")              # 17:00 的券商净值另记一列
        with open(paper_rotation.STATUS_FILE, encoding="utf-8") as f:
            status = f.read()
        self.assertIn(f"账户净值：${want:,.2f}（收盘净值；券商当时的读数是 $100,000.00", status)
        self.assertIn(f"策略 {want / 100000 - 1:+.2%}", status)           # “自起点”和成绩单用同一个净值

    def test_scoreboard_uses_broker_equity_when_data_not_current(self):
        row = self._run_with_data_notes(["⚠ 行情不是最新的：最新只有昨天"])
        self.assertEqual(row["equity"], "100000.00")                     # 收盘价不可靠 → 用券商净值

    def test_close_equity_falls_back_to_broker_value_for_unknown_symbols(self):
        last = pd.Series({"QQQ": 100.0})
        self.assertAlmostEqual(paper_rotation.close_equity(-500, {"QQQ": (10, 1200), "NVDA": (2, 300)}, last),
                               -500 + 10 * 100 + 300)

    def test_scoreboard_head_to_head_uses_common_dates(self):
        a = [{"date": "2026-01-02", "equity": "100"}, {"date": "2026-01-05", "equity": "110"},
             {"date": "2026-01-06", "equity": "121"}]
        b = [{"date": "2025-12-30", "equity": "50"}, {"date": "2026-01-02", "equity": "60"},
             {"date": "2026-01-05", "equity": "66"}]
        line = scoreboard.overlap_line({"甲（说明）": a, "乙": b}, {"2026-01-02": 500.0, "2026-01-05": 505.0})
        self.assertEqual(line, "同场比较（共同区间 2026-01-02~2026-01-05）：甲 +10.00%　乙 +10.00%　同期 SPY +1.00%")
        self.assertIsNone(scoreboard.overlap_line({"甲": a}, {}))                     # 只有一个系统：不比
        self.assertIsNone(scoreboard.overlap_line({"甲": a[:2], "乙": [{"date": "2026-01-05", "equity": "1"},
                                                                       {"date": "2026-01-09", "equity": "2"}]}, {}))

    def test_shared_scoreboard_writes_only_own_file(self):
        os.makedirs(scoreboard.SHARED_DIR, exist_ok=True)
        self.assertTrue(scoreboard.SHARED_DIR.startswith(TMP))           # 测试绝不碰真实 D:\\scoreboard
        other = os.path.join(scoreboard.SHARED_DIR, "gpt.csv")
        with open(other, "w", encoding="utf-8") as f:
            f.write("date,equity\n2026-01-02,99000\n2026-01-05,99100\n")
        with open(other, encoding="utf-8") as f:
            before = f.read()
        with open(paper_rotation.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": "2026-01-02", "start_equity": 100000, "spy_start": 500}, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            paper_rotation.record_score(dt.date(2026, 1, 5), 102000, 505, 1.9, 0.0)
        mine = scoreboard.read(scoreboard.MY_SHARED)
        self.assertEqual([r["equity"] for r in mine], ["100000.00", "102000.00"])
        with open(other, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)                          # 对方文件原封不动
        self.assertIn("ChatGPT", buf.getvalue())
        self.assertIn("同场比较（共同区间 2026-01-02~2026-01-05）：Noire 动量轮动 +2.00%　ChatGPT 盘中学习系统 +0.10%",
                      buf.getvalue())

    # ---------- 有限次数自动重试 ----------
    def _job(hour, minute):
        return run(lambda: paper_job.main(dt.datetime.combine(dt.date.today(), dt.time(hour, minute))))

    def _alerts_text(self):
        p = os.path.join(paths.RUNTIME, "alerts.txt")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_retry_skips_after_success(self):
        out = SafetyTests._job(17, 0)
        n = len(B.orders)
        self.assertGreater(n, 0)
        for o in B.orders:                                   # 模拟已成交，没有未成交订单
            o.status = fakebroker._Status("filled")
        out2 = SafetyTests._job(17, 30)
        self.assertIn("已经成功完成", out2)
        self.assertEqual(len(B.orders), n)                   # 重试时间点不会再下单

    def test_retry_only_alerts_on_final_failure(self):
        orig = paper_job.run_rotation
        paper_job.run_rotation = lambda: (_ for _ in ()).throw(ConnectionError("read timed out"))
        try:
            SafetyTests._job(17, 0)
            SafetyTests._job(17, 30)
            self.assertEqual(self._alerts_text(), "")         # 前两次不报警
            SafetyTests._job(18, 0)
            self.assertIn("3 次都失败", self._alerts_text())   # 第 3 次才报警
            out = SafetyTests._job(18, 30)
            self.assertIn("不再重试", out)
        finally:
            paper_job.run_rotation = orig

    def test_retry_recovers_after_network_error(self):
        orig, calls = paper_job.run_rotation, []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("read timed out")
            return orig()
        paper_job.run_rotation = flaky
        try:
            SafetyTests._job(17, 0)
            self.assertEqual(B.orders, [])
            out = SafetyTests._job(17, 30)
            self.assertIn("第 2 次尝试成功", out)
            self.assertTrue(B.orders)
            self.assertEqual(self._alerts_text(), "")
        finally:
            paper_job.run_rotation = orig

    def test_lock_prevents_overlap(self):
        os.makedirs(os.path.dirname(paper_job.LOCK), exist_ok=True)
        open(paper_job.LOCK, "w").close()                    # 另一次运行正在进行
        out = SafetyTests._job(17, 30)
        self.assertIn("还在运行", out)
        self.assertEqual(B.orders, [])
        old = dt.datetime.now().timestamp() - 30 * 60          # 30 分钟前的锁 = 上次异常退出残留
        os.utime(paper_job.LOCK, (old, old))
        SafetyTests._job(17, 30)
        self.assertTrue(B.orders)
        self.assertFalse(os.path.exists(paper_job.LOCK))       # 运行结束后锁被释放

    def test_unresolved_order_counts_as_failure_and_retries(self):
        B.fail_next_submit = "not_received"
        SafetyTests._job(17, 0)                              # 第一笔结果不明 → 本次算失败
        st = paper_job.load_state(f"{dt.date.today():%Y-%m-%d}")
        self.assertFalse(st["success"])
        SafetyTests._job(17, 30)                             # 重试：先核实（券商没收到）再正常下单
        self.assertTrue(B.orders)
        self.assertTrue(paper_job.load_state(f"{dt.date.today():%Y-%m-%d}")["success"])


import aplus                             # noqa: E402


def synthetic(rets, n=400, start="2024-01-01"):
    """按每只的“日收益序列生成器”造价格表：rets = {代码: 函数(i) -> 第 i 天收益}，没列出的保持不变。"""
    idx = pd.bdate_range(start, periods=n)
    cols = list(config.ROTATION_UNIVERSE) + [config.CASH_SYMBOL]
    out = {}
    for s in cols:
        f = rets.get(s, lambda i: 0.0)
        out[s] = 100 * np.cumprod([1 + f(i) for i in range(n)])
    return pd.DataFrame(out, index=idx)


def alternating(x, drift=0.0):
    return lambda i: drift + (x if i % 2 else -x)


class APlusTests(unittest.TestCase):
    """A+ 稳健版的规则：分 4 批、波动小的多买、波动目标、趋势开关、不借钱买现金。"""

    def test_tranche_dates(self):
        days = pd.bdate_range("2026-06-01", "2026-10-31")
        got = aplus.latest_signal_dates(days, dt.date(2026, 10, 15))
        self.assertEqual(got, {0: dt.date(2026, 9, 30), 5: dt.date(2026, 9, 23),
                               10: dt.date(2026, 9, 16), 15: dt.date(2026, 10, 9)})
        got = aplus.latest_signal_dates(days, dt.date(2026, 10, 30))      # 月末当天：4 批都是本月的
        self.assertEqual(got[0], dt.date(2026, 10, 30))
        self.assertEqual(got[15], dt.date(2026, 10, 9))

    def test_inverse_vol_weights_and_negative_score_to_cash(self):
        up = 0.004
        c = synthetic({"QQQ": alternating(0.02, up), "EEM": alternating(0.01, up)})
        w, _ = aplus.tranche_weights(c, c.index[-1])
        self.assertAlmostEqual(w["QQQ"], 1 / 3, delta=0.02)             # 波动大一倍 → 只拿一半的份额
        self.assertAlmostEqual(w["EEM"], 2 / 3, delta=0.02)
        c = synthetic({"QQQ": alternating(0.01, up), "EEM": alternating(0.01, -0.001),
                       **{s: alternating(0.01, -0.002) for s in ("SPY", "IWM", "EFA", "TLT", "GLD")}})
        w, _ = aplus.tranche_weights(c, c.index[-1])
        self.assertAlmostEqual(w["QQQ"], 0.5, delta=0.02)
        self.assertAlmostEqual(w[config.CASH_SYMBOL], 0.5, delta=0.02)  # 第二名也在跌 → 那一份拿现金

    def test_vol_target_floor_and_cap(self):
        spy_up = alternating(0.001, 0.001)                              # SPY 稳步上涨 → 趋势开关允许加杠杆
        base = pd.Series({"QQQ": 1.0})
        for x, expect in ((0.005, 2.0), (0.01, None), (0.03, 1.0)):
            c = synthetic({"QQQ": alternating(x), "SPY": spy_up})
            lev, msg, det = aplus.leverage(c, base)
            if expect is None:
                expect = config.VOL_TARGET / det["vol"]
                self.assertTrue(1.0 < expect < 2.0)
            self.assertAlmostEqual(lev, expect, places=6)
            self.assertTrue(det["trend_ok"])

    def test_trend_switch_caps_at_1x(self):
        c = synthetic({"QQQ": alternating(0.002), "SPY": alternating(0.001, -0.001)})
        lev, msg, det = aplus.leverage(c, pd.Series({"QQQ": 1.0}))
        self.assertFalse(det["trend_ok"])
        self.assertEqual(lev, 1.0)
        self.assertIn("最多 1 倍", msg)

    def test_never_borrow_to_hold_cash(self):
        base = pd.Series({"QQQ": 0.5, "EEM": 0.0, config.CASH_SYMBOL: 0.5})
        w = aplus.final_weights(base, 2.0)
        self.assertAlmostEqual(w["QQQ"], 1.0)
        self.assertNotIn(config.CASH_SYMBOL, w.index)                  # 风险资产已用满本金 → 不借钱买 SHY
        w = aplus.final_weights(base, 1.5)
        self.assertAlmostEqual(w["QQQ"], 0.75)
        self.assertAlmostEqual(w[config.CASH_SYMBOL], 0.25)
        self.assertLessEqual(float(aplus.final_weights(base, 2.0).sum()), config.LEVERAGE + 1e-9)

    def test_four_tranches_average(self):
        # 换仓日不同的几批会选出不同的组合：前半段 GLD 最强，后来 QQQ 最强
        n = 400
        c = synthetic({"GLD": lambda i: 0.004 if i < n - 12 else -0.03,
                       "QQQ": lambda i: 0.002 if i < n - 12 else 0.02,
                       "EEM": lambda i: 0.003})
        days = pd.bdate_range(c.index[0], c.index[-1] + pd.offsets.MonthEnd(0))
        base, notes = aplus.base_weights(c, days, c.index[-1])
        self.assertAlmostEqual(float(base.sum()), 1.0, places=9)
        self.assertEqual(len(notes), 4)
        dates = aplus.latest_signal_dates(days, c.index[-1].date())
        self.assertEqual(len(set(dates.values())), 4)                    # 4 批各有自己的换仓日


    def test_holiday_shifts_tranche_dates(self):
        days = [d for d in pd.bdate_range("2026-08-03", "2026-11-30") if d.date() != dt.date(2026, 11, 26)]
        got = aplus.latest_signal_dates(days, dt.date(2026, 11, 30))
        self.assertEqual(got[0], dt.date(2026, 11, 30))
        self.assertEqual(got[5], dt.date(2026, 11, 20))                  # 感恩节休市 → 往前多数一天


import fills                             # noqa: E402
import tracking                          # noqa: E402


class TrackingTests(unittest.TestCase):
    """对账工具：记录目标、记录成交价、实盘 vs 模型。"""

    def setUp(self):
        shutil.rmtree(paths.RUNTIME, ignore_errors=True)
        os.makedirs(paths.RUNTIME)
        B.reset()

    def test_live_run_logs_targets_dry_run_does_not(self):
        run(paper_rotation.main)
        self.assertFalse(os.path.exists(paper_rotation.TARGETS_LOG))
        run(paper_rotation.main, "--live")
        with open(paper_rotation.TARGETS_LOG, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("QQQ=", text)
        self.assertIn("EEM=", text)

    def test_calendar_failure_falls_back_to_weekdays(self):
        orig = fakebroker.TradingClient.get_calendar
        fakebroker.TradingClient.get_calendar = lambda self, req: (_ for _ in ()).throw(ConnectionError("x"))
        try:
            out = run(paper_rotation.main)
        finally:
            fakebroker.TradingClient.get_calendar = orig
        self.assertIn("交易日历获取失败", out)
        self.assertIn("四批信号", out)

    def test_fills_recorded_once_and_only_ours(self):
        o1 = B.add_order("QQQ", "buy", 10, "filled", client_order_id="noire-rot-20261019-QQQ-buy")
        o1.filled_qty, o1.filled_avg_price = "10", "100.5"
        o2 = B.add_order("TSLA", "buy", 1, "filled", client_order_id="web-1")
        o2.filled_qty, o2.filled_avg_price = "1", "250"
        self.assertEqual(fills.record(B.orders), 1)
        self.assertEqual(fills.record(B.orders), 0)                          # 不会重复记
        rows = fills.read()
        self.assertEqual([r["symbol"] for r in rows], ["QQQ"])

    def test_monitor_records_fills(self):
        run(paper_rotation.main, "--live")
        for o in B.orders:
            o.status = fakebroker._Status("filled")
            o.filled_qty, o.filled_avg_price = o.qty, "100.0"
        run(monitor.main)
        self.assertEqual(len(fills.read()), len(B.orders))

    def test_tracking_matches_model_and_measures_slippage(self):
        root = os.path.join(TMP, "trackroot")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(os.path.join(root, "logs"))
        os.makedirs(os.path.join(root, "data"))
        idx = pd.bdate_range("2026-01-05", periods=12)
        rng = np.random.default_rng(3)
        px = {}
        for s in ("SPY", "QQQ"):
            c = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx)))
            o = c * (1 + rng.normal(0, 0.003, len(idx)))
            pd.DataFrame({"Open": o, "High": c, "Low": c, "Close": c, "Volume": 1},
                         index=idx).to_csv(os.path.join(root, "data", f"{s}.csv"))
            px[s] = (o, c)
        with open(os.path.join(root, "logs", "targets.csv"), "w", encoding="utf-8") as f:
            f.write("as_of,run_time,leverage,cash_buffer,weights\n")
            for d in idx:
                f.write(f"{d.date()},x,1.5,0,QQQ=1.500000\n")
        o, c = px["QQQ"]
        eq = [100000.0, 100000.0]
        for i in range(2, len(idx)):                # 净值完全按模型走 → 对账相差应为 0
            eq.append(eq[-1] * (1 + 1.5 * (o[i] / c[i - 1] - 1)) * (1 + 1.5 * (c[i] / o[i] - 1)))
        with open(os.path.join(root, "equity_history.csv"), "w", encoding="utf-8") as f:
            f.write("date,equity\n" + "".join(f"{d.date()},{e:.6f}\n" for d, e in zip(idx, eq)))
        with open(os.path.join(root, "logs", "fills.csv"), "w", encoding="utf-8") as f:
            f.write("filled_at,date,symbol,side,qty,price,client_order_id,broker_id,status\n")
            f.write(f"{idx[3].date()}T13:30:01Z,{idx[3].date()},QQQ,buy,4,{o[3] * 1.001:.6f},noire-rot-x,b1,partially_filled\n")
            f.write(f"{idx[3].date()}T13:30:05Z,{idx[3].date()},QQQ,buy,10,{o[3] * 1.001:.6f},noire-rot-x,b1,filled\n")
        tr, _ = tracking.daily_tracking(root)
        self.assertEqual(len(tr), len(idx) - 2)
        self.assertLess(float(tr["diff"].abs().max()), 1e-6)
        sl = tracking.slippage(root)
        self.assertEqual(len(sl), 1)                                          # 部分成交 + 全部成交只算一笔
        self.assertAlmostEqual(float(sl["bps"].iloc[0]), 10.0, places=3)     # 比开盘价贵 0.1% = 10 个基点
        text = tracking.report(root)
        self.assertIn("对账天数 10", text)
        self.assertIn("按金额加权 +10.0 个基点", text)

    def test_tracking_attributes_second_leg(self):
        root = os.path.join(TMP, "trackroot2")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(os.path.join(root, "logs"))
        os.makedirs(os.path.join(root, "data"))
        idx = pd.bdate_range("2026-03-02", periods=12)
        rng = np.random.default_rng(5)
        px = {}
        for s in ("SPY", "QQQ"):
            c = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx)))
            o = c * (1 + rng.normal(0, 0.003, len(idx)))
            pd.DataFrame({"Open": o, "High": c, "Low": c, "Close": c, "Volume": 1},
                         index=idx).to_csv(os.path.join(root, "data", f"{s}.csv"))
            px[s] = (o, c)
        dips = [0.5 if 4 <= i <= 6 else 0.0 for i in range(len(idx))]     # 第 4~6 天收盘时第二条腿持有 0.5 倍
        with open(os.path.join(root, "logs", "targets.csv"), "w", encoding="utf-8") as f:
            f.write("as_of,run_time,leverage,cash_buffer,weights,dip\n")
            for d, x in zip(idx, dips):
                f.write(f"{d.date()},x,1.0,0,QQQ=1.000000|SPY={x:.6f},{x:.6f}\n")
        (qo, qc), (so, sc) = px["QQQ"], px["SPY"]
        eq, want = [100000.0, 100000.0], 0.0
        for i in range(2, len(idx)):
            night = (qo[i] / qc[i - 1] - 1) + dips[i - 2] * (so[i] / sc[i - 1] - 1)
            day = (qc[i] / qo[i] - 1) + dips[i - 1] * (sc[i] / so[i] - 1)
            eq.append(eq[-1] * (1 + night) * (1 + day))
            want += dips[i - 2] * (so[i] / sc[i - 1] - 1) + dips[i - 1] * (sc[i] / so[i] - 1)
        with open(os.path.join(root, "equity_history.csv"), "w", encoding="utf-8") as f:
            f.write("date,equity\n" + "".join(f"{d.date()},{e:.6f}\n" for d, e in zip(idx, eq)))
        tr, _ = tracking.daily_tracking(root)
        self.assertLess(float(tr["diff"].abs().max()), 1e-6)                  # 合并后的目标仍然对得上
        self.assertEqual(int(tr["dip_on"].sum()), 4)
        self.assertAlmostEqual(float(tr["dip_model"].sum()), want, places=9)
        self.assertIn("第二条腿", tracking.report(root))


import dipbuy                            # noqa: E402


@contextlib.contextmanager
def dip_switch(on):
    """测试里临时打开/关闭第二条腿（不管 config 里现在是开还是关，测试结果都一样）。"""
    old = config.DIP_ENABLED
    config.DIP_ENABLED = on
    try:
        yield
    finally:
        config.DIP_ENABLED = old


def spy_path(moves, n_up=260, up=0.001):
    """先稳步上涨 n_up 天（在 200 日线上方），再按 moves 里的日涨跌走。"""
    r = [up] * n_up + list(moves)
    idx = pd.bdate_range("2024-01-01", periods=len(r))
    return pd.Series(100 * np.cumprod([1 + x for x in r]), index=idx)


class DipTests(unittest.TestCase):
    """第二条腿（RSI(2) 超跌反弹）：信号、只用 A+ 剩余额度、总仓位不超过上限、开关。"""

    def setUp(self):
        shutil.rmtree(paths.RUNTIME, ignore_errors=True)
        os.makedirs(paths.RUNTIME)
        B.reset()

    def test_rsi2_matches_hand_calculation(self):
        r = dipbuy.rsi2(pd.Series([100.0, 101.0, 100.0, 99.0]))
        self.assertTrue(np.isnan(r.iloc[1]))                 # 只涨不跌：没有定义，不会触发
        self.assertAlmostEqual(r.iloc[2], 50.0)
        self.assertAlmostEqual(r.iloc[3], 25.0)

    def test_entry_hold_exit(self):
        n = 260
        c = spy_path([-0.015, -0.01, 0.005, 0.03, 0.0], n_up=n)
        pos = dipbuy.positions(c)
        self.assertEqual(float(pos.iloc[:n].sum()), 0.0)      # 稳步上涨时不买
        self.assertEqual(pos.iloc[n], 1.0)                    # 急跌当天收盘触发（下一个交易日开盘买）
        sma5 = c.rolling(5).mean()
        exit_day = next(i for i in range(n + 1, len(c)) if c.iloc[i] > sma5.iloc[i])
        self.assertEqual(exit_day, n + 3)
        self.assertTrue((pos.iloc[n:exit_day] == 1.0).all())  # 没反弹前一直拿着
        self.assertEqual(pos.iloc[exit_day], 0.0)             # 收盘高于 5 日均线 → 卖

    def test_no_entry_below_200_day_line(self):
        c = spy_path([0.005, -0.009] * 60 + [-0.02, -0.02])   # 慢慢跌到 200 日线下方，最后两天急跌
        self.assertLess(float(dipbuy.rsi2(c).iloc[-1]), config.DIP_RSI_ENTRY)
        self.assertLess(float(c.iloc[-1]), float(c.iloc[-200:].mean()))
        self.assertEqual(float(dipbuy.positions(c).sum()), 0.0)   # 超跌但大趋势向下 → 不买

    def test_allocate_only_uses_spare_capacity(self):
        cash = config.CASH_SYMBOL
        new, spare = dipbuy.allocate(pd.Series({"QQQ": 1.2, "EEM": 0.4}))
        self.assertAlmostEqual(spare, 0.4)                    # 2 倍 − A+ 已用的 1.6 倍
        self.assertAlmostEqual(new["SPY"], 0.4)
        new, spare = dipbuy.allocate(pd.Series({"QQQ": 0.5, cash: 0.5}))
        self.assertAlmostEqual(spare, 1.0)                    # 最多 1 倍
        self.assertNotIn(cash, new.index)                     # 先用 SHY 那部分自有资金
        new, spare = dipbuy.allocate(pd.Series({"SPY": 0.9, "QQQ": 0.6}))
        self.assertAlmostEqual(new["SPY"], 1.4)               # 和 A+ 本来就有的 SPY 合并
        new, spare = dipbuy.allocate(pd.Series({"QQQ": 1.0, "EEM": 1.0}))
        self.assertEqual(spare, 0.0)                          # A+ 已用满 2 倍 → 不加
        self.assertNotIn("SPY", new.index)
        for t in ({"QQQ": 1.2, "EEM": 0.4}, {"QQQ": 0.5, cash: 0.5}, {"QQQ": 2.0}, {cash: 1.0}, {"EEM": 0.3, cash: 0.7}):
            new, spare = dipbuy.allocate(pd.Series(t))
            self.assertLessEqual(float(new.sum()), config.LEVERAGE + 1e-9)   # 总仓位永远不超过上限
            self.assertGreaterEqual(float(new.min()), 0.0)

    def test_switch_off_leaves_aplus_unchanged(self):
        closes = pd.DataFrame({"SPY": spy_path([-0.015])})
        target = pd.Series({"QQQ": 0.5, config.CASH_SYMBOL: 0.5})
        with dip_switch(False):
            new, spare, lines = dipbuy.compute(closes, target)
        self.assertEqual(spare, 0.0)
        self.assertTrue(new.equals(target))
        self.assertIn("未启用", lines[0])

    def test_status_lines(self):
        c = spy_path([-0.015])
        closes = pd.DataFrame({"SPY": c})
        with dip_switch(True):
            _, spare, lines = dipbuy.compute(closes.iloc[:-1], pd.Series({"QQQ": 1.5}))
            self.assertEqual(spare, 0.0)
            self.assertIn("未持有", lines[0])
            _, spare, lines = dipbuy.compute(closes, pd.Series({"QQQ": 1.5}))
            self.assertAlmostEqual(spare, 0.5)
            self.assertIn("持有中（今天收盘新触发）", lines[0])
            _, spare, lines = dipbuy.compute(closes, pd.Series({"QQQ": 2.0}))
            self.assertEqual(spare, 0.0)
            self.assertIn("已用满额度", lines[0])
            c2 = spy_path([-0.015, -0.01])
            _, spare, lines = dipbuy.compute(pd.DataFrame({"SPY": c2}), pd.Series({"QQQ": 1.5}))
            self.assertIn(f"{c2.index[-2].date()} 收盘触发", lines[0])        # 写明哪天触发的
            c3 = spy_path([-0.015, -0.01, 0.005, 0.03])                       # 最后一天反弹到 5 日均线上方
            _, spare, lines = dipbuy.compute(pd.DataFrame({"SPY": c3}), pd.Series({"QQQ": 1.5}))
            self.assertEqual(spare, 0.0)
            self.assertIn("卖出信号", lines[0])

    def test_live_run_adds_dip_within_2x_and_logs_it(self):
        """SPY 在 200 日线上方急跌 → 触发；第二天跌破 200 日线 → A+ 最多 1 倍，第二条腿用剩下的 1 倍买 SPY。"""
        path = os.path.join(DATA_DIR, "SPY.csv")
        with open(path, encoding="utf-8") as f:
            original = f.read()
        k = np.arange(N_DAYS)
        c = np.where(k < N_DAYS - 150, 100 * np.exp(0.001 * (k - (N_DAYS - 150))), 100.0)
        c[-2], c[-1] = 99.9, 98.5
        write_prices("SPY", c)
        try:
            with dip_switch(True):
                out = run(paper_rotation.main, "--live")
        finally:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
        px = {s: (98.5 if s == "SPY" else last_price(s)) for s in DRIFT}
        buys = {o.symbol: float(o.qty) * px[o.symbol] for o in B.orders if o.side.value == "buy"}
        self.assertIn("低于 200 日均线", out)                                  # A+ 部分最多 1 倍
        self.assertIn("第二条腿（RSI(2) 超跌反弹）：持有中", out)
        self.assertAlmostEqual(buys.get("SPY", 0) / 100000, 1 - config.CASH_BUFFER, delta=0.02)
        self.assertAlmostEqual(sum(buys.values()) / 100000, 2 * (1 - config.CASH_BUFFER), delta=0.03)
        self.assertNotIn(config.CASH_SYMBOL, buys)
        with open(paper_rotation.TARGETS_LOG, encoding="utf-8") as f:
            last = f.read().strip().splitlines()[-1]
        self.assertTrue(last.endswith(",1.000000"))                          # 第二条腿单独记账


class DataAndMonitorTests(unittest.TestCase):
    """行情下载重试、不完整数据不覆盖缓存；监控以运行记录判断成败。"""

    def setUp(self):
        shutil.rmtree(paths.RUNTIME, ignore_errors=True)
        os.makedirs(paths.RUNTIME)
        B.reset()
        self.waits = []
        self._sleep, data.time.sleep = data.time.sleep, self.waits.append      # 测试里不真的等

    def tearDown(self):
        data.time.sleep = self._sleep
        sys.modules.pop("yfinance", None)

    def fake_yf(self, results):
        """假的 yfinance：每次 download 依次返回 results 里的东西（异常就抛出）。"""
        import types
        calls = []

        def download(sym, **kw):
            r = results[min(len(calls), len(results) - 1)]
            calls.append(sym)
            if isinstance(r, Exception):
                raise r
            return r
        sys.modules["yfinance"] = types.SimpleNamespace(download=download)
        return calls

    @staticmethod
    def frame(start, n):
        idx = pd.bdate_range(start, periods=n)
        c = np.linspace(100, 110, n)
        return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=idx)

    def test_download_retries_after_rate_limit(self):
        good = self.frame("2010-01-04", 300)
        calls = self.fake_yf([Exception("Too Many Requests. Rate limited."), good])
        df = data._download("SPY")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.waits, [20])
        self.assertEqual(len(df), 300)

    def test_truncated_download_is_rejected(self):
        old = self.frame("2010-01-04", 300)
        calls = self.fake_yf([self.frame("2010-06-01", 150)])       # Yahoo 只给了后一半
        with self.assertRaises(RuntimeError) as cm:
            data._download("SPY", old)
        self.assertEqual(len(calls), data.DOWNLOAD_TRIES)
        self.assertIn("本地缓存没有改动", str(cm.exception))
        self.assertIsNone(data._problem(self.frame("2010-01-04", 301), old))   # 多一天新数据是正常的

    def test_cache_write_is_atomic_and_leaves_no_temp_file(self):
        # 模拟盘和实盘共用 data\ 缓存：先写临时文件再整体替换，别的程序读到的永远是完整文件
        d = tempfile.mkdtemp(dir=TMP)
        path = os.path.join(d, "SPY.csv")
        self.frame("2010-01-04", 5).to_csv(path)
        self.fake_yf([self.frame("2010-01-04", 300)])
        old_dir, config.DATA_DIR = config.DATA_DIR, d
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                df = _orig_load("SPY", True)              # 真的走一遍“下载 → 写缓存”（假 yfinance）
        finally:
            config.DATA_DIR = old_dir
        self.assertEqual(len(df), 300)
        self.assertEqual(len(pd.read_csv(path, index_col=0)), 300)
        self.assertEqual(os.listdir(d), ["SPY.csv"])           # 没有留下临时文件

    def test_cache_write_retries_when_file_is_busy(self):
        d = tempfile.mkdtemp(dir=TMP)
        path = os.path.join(d, "SPY.csv")
        real, calls = os.replace, []

        def busy_once(a, b):
            calls.append(b)
            if len(calls) == 1:
                raise PermissionError("另一个程序正在读这个文件")
            return real(a, b)
        data.os.replace = busy_once
        try:
            data._save(self.frame("2010-01-04", 10), path)
        finally:
            data.os.replace = real
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.waits, [1])
        self.assertEqual(len(pd.read_csv(path, index_col=0)), 10)
        self.assertEqual(os.listdir(d), ["SPY.csv"])

    def _last_run_findings(self, state):
        os.makedirs(os.path.dirname(monitor.PAPER_LOG), exist_ok=True)
        with open(monitor.PAPER_LOG, "w", encoding="utf-8") as f:
            f.write("===== 2026-10-16 17:00 动量轮动模拟盘 【提交订单】 =====\nTraceback (most recent call last):\nX\n")
        with open(monitor.JOB_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        found = []
        with contextlib.redirect_stdout(io.StringIO()):
            monitor.check_last_run(fakebroker.TradingClient("k", "s", paper=True), found)
        return [k for k, _ in found]

    def test_monitor_trusts_job_state_over_log_words(self):
        ok = {"date": "2026-10-16", "attempts": 2, "success": True,
              "history": [{"time": "17:00", "result": "failed", "error": "ConnectionError"}, {"time": "17:30", "result": "ok"}]}
        self.assertNotIn("paper_error", self._last_run_findings(ok))     # 第 1 次失败、第 2 次成功 → 不报警
        bad = {"date": "2026-10-16", "attempts": 3, "success": False,
               "history": [{"time": "18:00", "result": "failed", "error": "RuntimeError: 每日自检没通过"}]}
        self.assertIn("paper_error", self._last_run_findings(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
