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
        idx = pd.bdate_range("2009-01-01", dt.date.today())
        c = np.exp(np.cumsum(np.random.default_rng(42).normal(-0.0015, 0.004, len(idx))))
        c = c / c[-1] * 100
        pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6}, index=idx).to_csv(spy_path)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
