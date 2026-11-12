# -*- coding: utf-8 -*-
"""实盘模式（真钱）的安全测试：三道闸、资金和杠杆上限、按金额下单、只读连接、紧急停止、记录分开。

每个场景在单独的进程里运行（NOIRE_PROFILE=live + 临时目录 + 假券商，见 live_scenario.py），
完全离线，不会连接任何真实账户，也不会碰真实的日志、状态和密钥。
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)
import test_safety as base       # noqa: E402  复用它的假行情（也保证测试环境已经装好假券商、临时目录）

PROJECT = os.path.dirname(TESTS)
SCENARIO = os.path.join(TESTS, "live_scenario.py")
LIVE_ON = {"LIVE_ENABLED": True, "LIVE_MAX_CAPITAL": 3000, "LIVE_MAX_LEVERAGE": 1.0}


def scenario(name, **args):
    """在单独进程里跑一个实盘场景，返回结果字典。"""
    tmp = tempfile.mkdtemp(prefix="noire_live_")
    try:
        runtime = os.path.join(tmp, "runtime")
        os.makedirs(runtime)
        args = {"scenario": name, "data_dir": base.DATA_DIR, **args}
        arg_file = os.path.join(tmp, "args.json")
        with open(arg_file, "w", encoding="utf-8") as f:
            json.dump(args, f)
        env = dict(os.environ, NOIRE_PROFILE="live", EXC_RUNTIME_DIR=runtime, PYTHONUTF8="1",
                   PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, SCENARIO, arg_file], cwd=PROJECT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180)
        lines = [l for l in (r.stdout or "").splitlines() if l.startswith("@@RESULT@@")]
        if r.returncode != 0 or not lines:
            raise AssertionError(f"场景 {name} 运行失败：\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}")
        return json.loads(lines[-1][len("@@RESULT@@"):])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


EMPTY_10K = {"equity": 10000, "cash": 10000, "regt": 20000}


class LiveModeTests(unittest.TestCase):
    # ---------- 三道闸 ----------
    def test_live_is_off_by_default(self):
        # 总开关 LIVE_ENABLED = False 时绝不下单。这里明确写出 False，不依赖 config.py 当前的值：
        # 用户打开实盘后 config.py 里是 True，如果靠默认值，这个测试会失败、挡住每天的自检（2026-10-31）
        r = scenario("rotation", submit=True, flag=True, config={"LIVE_ENABLED": False, "LIVE_MAX_CAPITAL": 3000},
                     account=EMPTY_10K)
        self.assertEqual(r["result"], "disabled")
        self.assertEqual(r["orders"], [])
        self.assertEqual(r["paper_flags"], [])          # 连实盘账户都没连

    def test_live_needs_enable_file_from_user(self):
        r = scenario("rotation", submit=True, flag=False, config=LIVE_ON, account=EMPTY_10K)
        self.assertEqual(r["result"], "disabled")
        self.assertEqual(r["orders"], [])
        self.assertIn("enable_live.bat", r["stdout"])

    def test_live_needs_capital_limit(self):
        r = scenario("rotation", submit=True, flag=True, config={**LIVE_ON, "LIVE_MAX_CAPITAL": 0},
                     account=EMPTY_10K)
        self.assertEqual(r["result"], "disabled")
        self.assertEqual(r["orders"], [])

    def test_live_dry_run_is_read_only_and_needs_no_switch(self):
        r = scenario("rotation", submit=False, flag=False, config=LIVE_ON, account=EMPTY_10K)
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["orders"], [])
        self.assertEqual(r["paper_flags"], [False])      # 连的是实盘账户，但只读
        self.assertIn("空跑", r["stdout"])
        self.assertIn("实盘", r["stdout"])
        self.assertIn("paper_status_dryrun.txt", r["report_files"])     # 空跑结果写在实盘文件夹里
        self.assertNotIn("paper_status.txt", r["report_files"])

    # ---------- 打开以后：金额、杠杆、零股 ----------
    def test_live_caps_capital_and_leverage_and_buys_by_amount(self):
        r = scenario("rotation", submit=True, flag=True, config=LIVE_ON, account=EMPTY_10K)
        self.assertEqual(r["result"], "ok")
        self.assertEqual(r["paper_flags"], [False])
        buys = [o for o in r["orders"] if o["side"] == "buy"]
        self.assertTrue(buys)
        self.assertTrue(all(o["qty"] is None and o["notional"] for o in buys))   # 按金额下单（零股）
        total = sum(float(o["notional"]) for o in buys)
        self.assertLessEqual(total, 3000 * 0.98 * 1.0 + 0.05)          # 只管 3000 美元、不借钱
        self.assertGreater(total, 3000 * 0.98 * 0.95)
        self.assertIn("实盘上限 1.00 倍", r["stdout"])
        self.assertFalse(r["shared_written"])           # 不写和 ChatGPT 共享的成绩单
        self.assertIn("Noire 实盘", r["scoreboard"])
        self.assertIn("模拟盘（同期对照", r["scoreboard"])   # 附上模拟盘同一时期的成绩
        self.assertIn("2026-01-02~2026-01-05（2天）　收益 +2.00%", r["scoreboard"])      # 从实盘开始那天算起
        self.assertNotIn("ChatGPT", r["scoreboard"])
        self.assertIn("logs", r["runtime_files"])       # 记录在实盘自己的文件夹里

    def test_live_leverage_cap_1_5(self):
        r = scenario("rotation", submit=True, flag=True, config={**LIVE_ON, "LIVE_MAX_LEVERAGE": 1.5},
                     account=EMPTY_10K)
        total = sum(float(o["notional"]) for o in r["orders"] if o["side"] == "buy")
        self.assertLessEqual(total, 3000 * 0.98 * 1.5 + 0.05)
        self.assertGreater(total, 3000 * 0.98 * 1.5 * 0.95)

    def test_live_sells_fractional_shares_never_more_than_held(self):
        # IWM 不在目标里 → 零股全部卖出；QQQ 券商市值比本地收盘价算出的高很多（例如盘后大涨）
        # → 按本地价格算要卖 10 股左右，但只持有 1 股 → 最多卖 1 股（绝不做空）
        acct = {"equity": 3000, "cash": 100, "regt": 200,
                "positions": {"IWM": [2.5, 250.0], "QQQ": [1.0, 2650.0]}}
        r = scenario("rotation", submit=True, flag=True, config=LIVE_ON, account=acct)
        sells = [o for o in r["orders"] if o["side"] == "sell"]
        self.assertEqual([(o["symbol"], float(o["qty"])) for o in sells], [("IWM", 2.5), ("QQQ", 1.0)])
        self.assertTrue(all(o["notional"] is None for o in sells))

    def test_live_leaves_tiny_leftovers_alone(self):
        # 不在目标里、但只剩几分钱的零头（例如 0.00002 股 IWM）：不去卖（券商可能拒绝，天天报警）
        acct = {"equity": 3000, "cash": 3000, "regt": 6000, "positions": {"IWM": [0.00002, 0.002]}}
        r = scenario("rotation", submit=True, flag=True, config=LIVE_ON, account=acct)
        self.assertFalse([o for o in r["orders"] if o["symbol"] == "IWM"])
        self.assertIn("太零碎", r["stdout"])
        self.assertTrue([o for o in r["orders"] if o["side"] == "buy"])    # 其他照常

    def test_live_refuses_orders_bigger_than_the_limit(self):
        # 账户里有远超管理金额的 QQQ（例如用户自己买的）：程序不会去动这么大的金额，而是报警
        acct = {"equity": 10000, "cash": 2000, "regt": 4000, "positions": {"QQQ": [80, 8000.0]}}
        r = scenario("rotation", submit=True, flag=True, config=LIVE_ON, account=acct)
        self.assertFalse([o for o in r["orders"] if o["symbol"] == "QQQ"])
        self.assertIn("超过单笔上限", r["stdout"])

    # ---------- 其他入口 ----------
    def test_live_manual_order_channel_is_refused(self):
        r = scenario("trade_place", config=LIVE_ON, flag=True)
        self.assertIn("实盘不支持", r["error"] or "")
        self.assertEqual(r["orders"], [])

    def test_live_emergency_stop_cancels_but_keeps_positions(self):
        acct = {"equity": 3000, "cash": 0, "positions": {"QQQ": [4.0, 3000.0]}}
        opens = [{"symbol": "EEM", "side": "buy", "qty": 3}]
        r = scenario("emergency_stop", account=acct, open_orders=opens)
        self.assertTrue(r["stop_file"])
        self.assertEqual([o["status"] for o in r["orders"]], ["canceled"])          # 撤了单
        self.assertFalse([o for o in r["orders"] if o["side"] == "sell"])           # 没有卖持仓
        r = scenario("liquidate", account=acct, open_orders=opens)
        self.assertEqual([(o["symbol"], o["side"], o["status"]) for o in r["orders"]],
                         [("EEM", "buy", "canceled"), ("QQQ", "sell", "accepted")])
        self.assertTrue(r["stop_file"])          # 全部卖出后自动交易保持停止（不会 17:10 又买回来）

    def test_live_job_skips_self_test_and_broker_when_switched_off(self):
        r = scenario("job", flag=False, config=LIVE_ON)
        self.assertEqual(r["result"], 0)
        self.assertEqual(r["calls"], [])                 # 开关没开：不跑自检、不跑换仓
        self.assertFalse(r["state_file"])
        self.assertEqual(r["paper_flags"], [])           # 也没连券商
        self.assertIn("enable_live.bat", r["stdout"])
        r = scenario("job", flag=True, config=LIVE_ON)
        self.assertEqual(r["result"], 0)
        self.assertEqual(r["calls"], ["preflight", "rotation"])

    def test_live_job_refuses_to_trade_during_the_trading_day(self):
        # 1.0.2：白天手动双击 run_live.bat → 不换仓、不算一次尝试，17:10 照常（以前会按盘中价格换仓，17:10 反而跳过）
        r = scenario("job", flag=True, config=LIVE_ON, now="2026-11-09T11:00:00")     # 星期一上午
        self.assertEqual(r["result"], 0)
        self.assertEqual(r["calls"], [])                 # 不跑自检、不换仓
        self.assertFalse(r["state_file"])                # 也不算一次尝试
        self.assertEqual(r["paper_flags"], [])           # 没连券商
        self.assertIn("今天 17:10 会自动运行", r["stdout"])
        r = scenario("job", flag=True, config=LIVE_ON, now="2026-11-09T17:10:00")
        self.assertEqual(r["calls"], ["preflight", "rotation"])

    def test_live_waits_for_todays_close_before_trading(self):
        # 1.0.2：行情还没更新到今天的收盘价 → 前两次不下单、不报警；最后一次（不加 --retry-if-stale）照常换仓并报警
        base.write_prices("GLD", base.fake_path(base.DRIFT["GLD"])[:-1], base.fake_index()[:-1])   # GLD 少最新一天
        try:
            r = scenario("rotation", submit=True, flag=True, config=LIVE_ON, account=EMPTY_10K,
                         extra_argv=["--retry-if-stale"])
            self.assertIsNone(r["error"])
            self.assertEqual(r["result"], "stale")
            self.assertEqual(r["orders"], [])
            self.assertEqual(r["alerts"], "")
            r = scenario("rotation", submit=True, flag=True, config=LIVE_ON, account=EMPTY_10K)
            self.assertTrue(r["orders"])
            self.assertIn("【实盘】自动换仓：行情不完整：GLD", r["alerts"])
        finally:
            base.make_data()

    def test_live_monitor_is_read_only(self):
        r = scenario("monitor", account={"equity": 3000, "cash": 3000})
        self.assertIsNone(r["error"])
        self.assertEqual(r["orders"], [])
        self.assertIn("logs", r["runtime_files"])
        self.assertIn("实盘自动交易没有打开", r["stdout"])     # 状态里写明开关没开

    def test_live_read_only_connection_cannot_place_orders(self):
        r = scenario("readonly_submit")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["orders"], [])

    def test_live_keys_never_fall_back_to_paper_keys(self):
        r = scenario("keys")
        self.assertEqual(r["without_live_file"], [None, None])
        self.assertEqual(r["with_live_file"], ["LIVEKEY", "LIVESECRET"])

    def test_live_records_go_to_live_subfolder(self):
        env = {k: v for k, v in os.environ.items() if k != "EXC_RUNTIME_DIR"}
        env["NOIRE_PROFILE"] = "live"
        out = subprocess.run([sys.executable, "-c", "import paths; print(paths.RUNTIME)"], cwd=PROJECT, env=env,
                             capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(out, os.path.join(PROJECT, "live"))

    # ---------- 出入金（2026-11-05）：存钱不算赚、取钱不算亏，只读查询，不重复记账 ----------
    HIST_3000 = [[-3, 3000.0], [-2, 3000.0], [-1, 3000.0]]

    def _today_row(self, r):
        return r["history"][-1]

    def test_live_deposit_is_not_counted_as_profit(self):
        acct = {"equity": 3300, "cash": 3300, "last_equity": 3000, "regt": 3300, "bp": 3300}
        acts = [{"type": "CSD", "off": -10, "amount": 3000, "id": "old"},       # 起点以前存的：已在起点净值里
                {"type": "CSD", "off": 0, "amount": 300, "id": "new"},
                {"type": "FEE", "off": 0, "amount": -0.01, "id": "fee"},          # 费用是真实成本，不是出入金
                {"type": "CSD", "off": 0, "amount": 999, "id": "bounced", "status": "canceled"}]
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=self.HIST_3000, activities=acts)
        self.assertIsNone(r["error"])
        row = self._today_row(r)
        self.assertEqual(row["equity"], "3300.00")                     # 净值照旧是账户里真实的钱
        self.assertEqual(row["flow"], "300.00")                        # 跑了两次也只记一次
        self.assertEqual(sorted((x["id"], x["assigned_to"] == "start") for x in r["ledger"]),
                         [("new", False), ("old", True)])
        self.assertIn("策略 +0.00%　同期 SPY", r["paper_status"])           # 仪表盘按这个格式读
        self.assertIn("（已扣除期间出入金 +$300.00（存钱））", r["paper_status"])
        self.assertIn("收益 +0.00%", r["scoreboard"])
        self.assertIn("出入金：实盘期间共 +$300.00（存钱）", r["scoreboard"])
        self.assertIn("今日 +0.00%", r["monitor_stdout"])
        self.assertIn("今天出入金 +$300.00（存钱）", r["monitor_stdout"])
        self.assertIn("今日盈亏：$+0.00（+0.00%）（已扣除今天的出入金 +$300.00（存钱））", r["status_txt"])
        self.assertTrue(r["orders"])                                    # 新钱照常投进去
        self.assertTrue(all(c.get("activity_types") == "CSD,CSW" for c in r["activity_calls"]))

    def test_live_withdrawal_is_not_a_loss_and_never_locks_trading(self):
        acct = {"equity": 2400, "cash": 2400, "last_equity": 3000, "regt": 2400, "bp": 2400}
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=self.HIST_3000,
                     activities=[{"type": "CSW", "off": 0, "amount": -600, "id": "out"}])
        self.assertIsNone(r["error"])
        self.assertNotIn("今天只允许卖出", r["stdout"])                   # 以前会被当成单日亏 20% → 只卖不买
        self.assertTrue(any(o["side"] == "buy" for o in r["orders"]))
        self.assertNotIn("今天已亏损", r["alerts"])                       # 以前盘中会误报
        self.assertNotIn("回撤", r["alerts"])
        self.assertEqual(self._today_row(r)["flow"], "-600.00")
        self.assertEqual(self._today_row(r)["drawdown"], "0.0000")
        self.assertIn("收益 +0.00%", r["scoreboard"])
        self.assertIn("最大回撤 0.0%", r["scoreboard"])
        self.assertAlmostEqual(r["risk_state"]["peak_equity"], 2400.0, places=2)

    def test_live_wrong_deposit_record_never_locks_trading(self):
        # 券商记录说存了钱，但净值里还没有：扣掉会像亏了 10%——风控绝不因此只卖不买
        acct = {"equity": 3000, "cash": 3000, "last_equity": 3000, "regt": 3000, "bp": 3000}
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=self.HIST_3000,
                     activities=[{"type": "CSD", "off": 0, "amount": 300, "id": "x"}], runs=1)
        self.assertIsNone(r["error"])
        self.assertNotIn("今天只允许卖出", r["stdout"])
        self.assertNotIn("今天已亏损", r["alerts"])

    def test_live_flow_lookup_failure_does_not_stop_trading(self):
        acct = {"equity": 3300, "cash": 3300, "last_equity": 3000, "regt": 3300, "bp": 3300}
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=self.HIST_3000,
                     activities=[{"type": "CSD", "off": 0, "amount": 300}], activities_error=True, runs=1)
        self.assertIsNone(r["error"])
        self.assertEqual(r["result"], "ok")
        self.assertTrue(r["orders"])
        self.assertEqual(len(r["history"]), 3)                          # 不知道存取了多少 → 今天先不记成绩单
        self.assertIsNone(r["ledger"])
        self.assertIn("没查到出入金记录", r["paper_status"])
        self.assertEqual(r["alerts"], "")                               # 网络问题不推送警报

    def test_live_old_withdrawal_does_not_hide_a_real_loss(self):
        # 上一个交易日收盘前取的钱：券商的“昨日净值”已经扣过了。就算那天没记成绩单，今天也不能再扣一次——
        # 否则真实的 -8% 会被算成 +3%，单日亏损 5% 的“只卖不买”就失效了
        acct = {"equity": 2484, "cash": 2484, "last_equity": 2700, "regt": 2484, "bp": 2484}
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=[[-10, 3000.0], [-9, 3000.0]],
                     activities=[{"type": "CSW", "prev_weekday_noon": True, "amount": -300, "id": "w"}], runs=1)
        self.assertIsNone(r["error"])
        self.assertIn("今天只允许卖出", r["stdout"])
        self.assertIn("今天已亏损", r["alerts"])
        self.assertEqual(self._today_row(r)["flow"], "-300.00")         # 成绩单：漏记那几天的钱算进今天这一行

    def test_live_broken_flow_file_does_not_stop_trading(self):
        acct = {"equity": 3300, "cash": 3300, "last_equity": 3000, "regt": 3300, "bp": 3300}
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=self.HIST_3000,
                     activities=[{"type": "CSD", "off": 0, "amount": 300, "id": "new"}], runs=1,
                     ledger_raw="id,created_at,date,type,amount,assigned_to\n\"unclosed,quote\n" + "x" * 200000)
        self.assertIsNone(r["error"])
        self.assertEqual(r["result"], "ok")
        self.assertTrue(r["orders"])                                    # 照常下单
        self.assertEqual(len(r["history"]), 3)                          # 今天先不记成绩单
        self.assertIn("出入金记录出错", r["alerts"])                      # 推送提醒去检查

    def test_live_first_run_counts_only_money_after_start(self):
        # 第一次用（还没有出入金记录文件）：起点那天及以前存的钱都算在起点里，只有之后的才记
        acct = {"equity": 3000, "cash": 3000, "last_equity": 3000, "regt": 3000, "bp": 3000}
        acts = [{"type": "CSD", "off": -3, "amount": 1000, "id": "a"},
                {"type": "CSD", "off": -20, "amount": 2000, "id": "b"}]
        r = scenario("flows", flag=True, config=LIVE_ON, account=acct, history=self.HIST_3000, activities=acts,
                     runs=1)
        self.assertEqual(self._today_row(r)["flow"], "0.00")
        self.assertEqual({x["assigned_to"] for x in r["ledger"]}, {"start"})
        self.assertNotIn("出入金：", r["scoreboard"])

    # ---------- 实盘警报抄送到本文件夹的 alerts.txt（手机推送的定时任务只读这个文件）----------
    def test_live_alerts_are_copied_for_phone_push(self):
        mirror = os.path.join(tempfile.mkdtemp(prefix="noire_mirror_"), "alerts.txt")
        try:
            r = scenario("alert", mirror=mirror)
            self.assertIn("【实盘】测试警报", r["live_alerts"])
            self.assertIsNotNone(r["mirror_text"])
            self.assertIn("【实盘】测试警报", r["mirror_text"])
        finally:
            shutil.rmtree(os.path.dirname(mirror), ignore_errors=True)

    def test_alert_copy_never_touches_real_files_in_tests(self):
        r = scenario("alert")                          # 没给临时抄送位置 → 不抄送
        self.assertIsNone(r["mirror_path"])
        self.assertIn("【实盘】测试警报", r["live_alerts"])

    def test_paper_alerts_are_not_copied(self):
        import alerts
        self.assertIsNone(alerts._mirror_path())

    def test_daily_self_test_always_runs_in_paper_mode(self):
        import preflight
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, "Ran 1 test\nOK\n", "")

        old_run, old_env = preflight.subprocess.run, os.environ.get("NOIRE_PROFILE")
        preflight.subprocess.run = fake_run
        os.environ["NOIRE_PROFILE"] = "live"
        try:
            self.assertEqual(preflight.main(), 0)
        finally:
            preflight.subprocess.run = old_run
            if old_env is None:
                os.environ.pop("NOIRE_PROFILE", None)
            else:
                os.environ["NOIRE_PROFILE"] = old_env
        self.assertNotIn("NOIRE_PROFILE", seen)
        self.assertNotIn("EXC_RUNTIME_DIR", seen)


if __name__ == "__main__":
    unittest.main()
