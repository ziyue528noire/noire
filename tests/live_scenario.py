# -*- coding: utf-8 -*-
"""实盘模式的测试场景：由 test_live.py 在单独的进程里运行（NOIRE_PROFILE=live、临时目录、假券商）。

用法：python live_scenario.py <参数 json 文件>
最后一行输出 "@@RESULT@@" + JSON（ASCII 转义），由 test_live.py 解析。
这个文件名不以 test 开头，所以不会被 unittest 当成测试直接运行。
"""
import os
import sys
import io
import json
import contextlib

TESTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(TESTS)
sys.path.insert(0, TESTS)
sys.path.insert(0, PROJECT)

with open(sys.argv[1], encoding="utf-8") as f:
    ARGS = json.load(f)

# 安全检查：必须是实盘模式 + 临时目录，否则立即退出（绝不碰真实文件）
assert os.environ.get("NOIRE_PROFILE") == "live", "场景必须在 NOIRE_PROFILE=live 下运行"
assert os.environ.get("EXC_RUNTIME_DIR"), "场景必须在临时目录里运行"

import fakebroker                 # noqa: E402
fakebroker.install()
B = fakebroker.BROKER
B.allow_live = ARGS.get("allow_live", True)

import config                     # noqa: E402
import runmode                    # noqa: E402
import paths                      # noqa: E402
assert runmode.IS_LIVE and paths.RUNTIME == os.environ["EXC_RUNTIME_DIR"]
for k, v in ARGS.get("config", {}).items():
    setattr(config, k, v)
if ARGS.get("data_dir"):
    config.DATA_DIR = ARGS["data_dir"]

import data                       # noqa: E402
import keys                       # noqa: E402
_orig = data.load_prices
data.load_prices = lambda s, refresh=False: _orig(s, False)     # 绝不联网

os.makedirs(paths.RUNTIME, exist_ok=True)
if ARGS.get("flag"):
    with open(os.path.join(paths.RUNTIME, "LIVE_ENABLED"), "w", encoding="utf-8") as f:
        f.write("test")

acct = ARGS.get("account", {})
if acct:
    B.equity = float(acct.get("equity", 10000))
    B.cash = float(acct.get("cash", B.equity))
    B.last_equity = float(acct.get("last_equity", B.equity))
    B.regt_buying_power = float(acct.get("regt", 2 * B.equity))
    B.buying_power = acct.get("bp")
    B.positions = {s: (float(q), float(v)) for s, (q, v) in acct.get("positions", {}).items()}
for o in ARGS.get("open_orders", []):
    B.add_order(o["symbol"], o["side"], o["qty"], "accepted", client_order_id=o.get("cid"))


def run(fn, argv):
    old = sys.argv
    sys.argv = ["x", *argv]
    buf = io.StringIO()
    result, error = None, None
    try:
        with contextlib.redirect_stdout(buf):
            result = fn()
    except SystemExit as e:
        error = f"SystemExit: {e}"
    finally:
        sys.argv = old
    return result, error, buf.getvalue()


out = {}
scenario = ARGS["scenario"]
if scenario == "rotation":
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import paper_rotation
    import scoreboard
    os.makedirs(scoreboard.SHARED_DIR, exist_ok=True)     # 共享成绩单文件夹存在时，实盘也不能往里写
    paper_rotation.load_keys = keys.load_keys
    out["result"], out["error"], out["stdout"] = run(paper_rotation.main, (["--live"] if ARGS.get("submit") else [])
                                                     + ARGS.get("extra_argv", []))
    alerts_file = os.path.join(paths.RUNTIME, "alerts.txt")
    out["alerts"] = open(alerts_file, encoding="utf-8").read() if os.path.exists(alerts_file) else ""
    # 成绩单：直接记两天再生成一次（不依赖今天是不是交易日），检查实盘有没有写进共享成绩单
    import datetime as dt
    with open(scoreboard.PAPER_HISTORY, "w", encoding="utf-8") as f:      # 模拟盘的记录（对照用，只读）
        f.write("date,equity\n2025-12-31,90000\n2026-01-02,100000\n2026-01-05,102000\n")
    scoreboard.record(dt.date(2026, 1, 2), 3000.0, 100.0, 1.0, 0.0)
    scoreboard.record(dt.date(2026, 1, 5), 3030.0, 101.0, 1.0, 0.0)
    run(scoreboard.main, [])
    out["shared_written"] = os.path.exists(scoreboard.MY_SHARED)
    with open(scoreboard.REPORT, encoding="utf-8") as f:
        out["scoreboard"] = f.read()
elif scenario == "flows":
    # 出入金（2026-11-05）：先写好前几天的成绩单和券商的入金/出金记录，再跑两次正式换仓、一次监控、一次状态
    import datetime as dt
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import paper_rotation
    import scoreboard
    import monitor
    import trade
    today = dt.date.today()
    day = lambda off: f"{today + dt.timedelta(days=off):%Y-%m-%d}"
    hist = ARGS.get("history", [])
    if hist:
        with_flow = any(len(h) > 2 for h in hist)       # [偏移天数, 净值, 出入金]：带 flow 列（1.0.3 的场景）
        with open(scoreboard.HISTORY, "w", encoding="utf-8") as f:
            f.write("date,equity,spy_close,leverage,drawdown,broker_equity" + (",flow" if with_flow else "") + "\n")
            for h in hist:
                off, eq = h[0], h[1]
                fl = (f",{h[2]:.2f}" if len(h) > 2 and h[2] is not None else ",") if with_flow else ""
                f.write(f"{day(off)},{eq:.2f},500.0000,1.000,0.0000,{eq:.2f}{fl}\n")
        with open(paper_rotation.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": day(hist[0][0]), "start_equity": hist[0][1], "spy_start": 500.0}, f)
    def prev_weekday():
        d = today - dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        return d

    def when(a):
        """(日期, 到账时间 UTC)：默认当天美东 02:20；prev_weekday_noon = 上一个工作日中午（上一个收盘之前）；
        prev_weekday_after_close = 上一个工作日 17:03（收盘以后、17:10 换仓以前，1.0.3）；
        prev_weekday_evening = 上一个工作日 20:00（当晚的换仓已经记完成绩单以后，1.0.3）。"""
        for flag, clock in (("prev_weekday_noon", dt.time(12, 0)), ("prev_weekday_after_close", dt.time(17, 3)),
                            ("prev_weekday_evening", dt.time(20, 0))):
            if a.get(flag):
                d = prev_weekday()
                t = dt.datetime.combine(d, clock).astimezone().astimezone(dt.timezone.utc)
                return f"{d:%Y-%m-%d}", t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return day(a["off"]), f"{day(a['off'])}T07:20:52.48Z"
    B.activities = [{"id": a.get("id", f"act{i}"), "activity_type": a["type"], "date": when(a)[0],
                     "created_at": when(a)[1], "net_amount": str(a["amount"]),
                     "status": a.get("status", "executed"), "description": "type: instant_ach"}
                    for i, a in enumerate(ARGS.get("activities", []))]
    if ARGS.get("ledger_rows"):                 # 事先记好的出入金（assigned = "prev_weekday" / "start" / 偏移天数）
        import cashflow
        acts = {a["id"]: a for a in B.activities}
        with open(cashflow.LEDGER, "w", encoding="utf-8") as f:
            f.write("id,created_at,date,type,amount,assigned_to\n")
            for x in ARGS["ledger_rows"]:
                a, asg = acts[x["id"]], x["assigned"]
                asg = (f"{prev_weekday():%Y-%m-%d}" if asg == "prev_weekday" else
                       asg if asg == "start" else day(asg))
                t = cashflow._local(a["created_at"])
                f.write(f"{a['id']},{t},{a['date']},{a['activity_type']},{float(a['net_amount']):.2f},{asg}\n")
    if ARGS.get("ledger_raw") is not None:
        import cashflow
        with open(cashflow.LEDGER, "w", encoding="utf-8") as f:
            f.write(ARGS["ledger_raw"])
    B.activities_error = bool(ARGS.get("activities_error"))
    paper_rotation.load_keys = keys.load_keys
    paper_rotation.check_data = lambda *a, **k: ([], True)      # 固定成“今天是交易日”，不依赖今天星期几
    runs = ARGS.get("runs", 2)
    outs = []
    for _ in range(runs):
        r, e, o = run(paper_rotation.main, ["--live"])
        outs.append(o)
        if e:
            out["error"] = e
    out.setdefault("error", None)
    out["result"], out["stdout"] = r, "\n".join(outs)
    monitor.load_keys = keys.load_keys
    _, _, out["monitor_stdout"] = run(monitor.main, [])
    trade.status(trade.broker.client("LIVE_K", "LIVE_S"), ("LIVE_K", "LIVE_S"))
    with open(os.path.join(trade.ORD_DIR, "status.txt"), encoding="utf-8") as f:
        out["status_txt"] = f.read()
    out["history"] = scoreboard.read(scoreboard.HISTORY)
    import cashflow
    try:
        out["ledger"] = cashflow.read_ledger()
    except Exception as e:                       # 故意弄坏的记录文件
        out["ledger"] = f"unreadable: {type(e).__name__}"
    with open(paper_rotation.STATUS_FILE, encoding="utf-8") as f:
        out["paper_status"] = f.read()
    out["scoreboard"] = ""                       # 今天没记成绩单时没有这个文件
    if os.path.exists(scoreboard.REPORT):
        with open(scoreboard.REPORT, encoding="utf-8") as f:
            out["scoreboard"] = f.read()
    import risk
    out["risk_state"] = risk.load_state()
    alerts_file = os.path.join(paths.RUNTIME, "alerts.txt")
    out["alerts"] = open(alerts_file, encoding="utf-8").read() if os.path.exists(alerts_file) else ""
    out["activity_calls"] = B.activity_calls
elif scenario == "trade_place":
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import runpy
    def go():
        sys.argv = ["trade.py", "place"]
        runpy.run_path(os.path.join(PROJECT, "trade.py"), run_name="__main__")
    out["result"], out["error"], out["stdout"] = run(go, ["place"])
elif scenario in ("emergency_stop", "liquidate"):
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import emergency
    emergency.load_keys = keys.load_keys
    emergency.time.sleep = lambda s: None
    fn = emergency.stop if scenario == "emergency_stop" else emergency.liquidate
    out["result"], out["error"], out["stdout"] = run(fn, [])
    out["stop_file"] = os.path.exists(os.path.join(paths.RUNTIME, "STOP"))
elif scenario == "monitor":
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import monitor
    monitor.load_keys = keys.load_keys
    out["result"], out["error"], out["stdout"] = run(monitor.main, [])
elif scenario == "job":
    import datetime as dt
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import paper_job
    calls = []
    paper_job.run_preflight = lambda: calls.append("preflight") or True
    paper_job.run_rotation = lambda: calls.append("rotation") or "ok"
    # 默认今天 17:10（自动运行的时间）；以前用“现在”，白天跑测试会被 1.0.2 的盘中保护挡住
    now = (dt.datetime.fromisoformat(ARGS["now"]) if ARGS.get("now")
           else dt.datetime.combine(dt.date.today(), dt.time(17, 10)))
    out["result"], out["error"], out["stdout"] = run(lambda: paper_job.main(now), [])
    out["calls"] = calls
    out["state_file"] = os.path.exists(paper_job.STATE)
elif scenario == "readonly_submit":
    import broker
    c = broker.client("LIVE_K", "LIVE_S", trading=False)
    try:
        c.submit_order(fakebroker.MarketOrderRequest(symbol="SPY", qty=1, side=fakebroker.OrderSide.BUY,
                                                     time_in_force=fakebroker.TimeInForce.DAY))
        out["blocked"] = False
    except broker.LiveDisabled:
        out["blocked"] = True
    out["result"], out["error"], out["stdout"] = None, None, ""
elif scenario == "keys":
    import importlib
    env_dir = os.environ["EXC_RUNTIME_DIR"]
    with open(os.path.join(env_dir, ".env"), "w", encoding="utf-8") as f:
        f.write("ALPACA_KEY=PAPERKEY\nALPACA_SECRET=PAPERSECRET\n")
    os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"] = "ENVKEY", "ENVSECRET"
    out["without_live_file"] = list(keys.load_keys())
    with open(os.path.join(env_dir, ".env.live"), "w", encoding="utf-8") as f:
        f.write("ALPACA_LIVE_KEY=LIVEKEY\nALPACA_LIVE_SECRET=LIVESECRET\n")
    out["with_live_file"] = list(keys.load_keys())
elif scenario == "alert":
    if ARGS.get("mirror"):
        os.environ["EXC_ALERTS_MIRROR"] = ARGS["mirror"]
    import alerts
    out["mirror_path"] = alerts._mirror_path()
    out["result"], out["error"], out["stdout"] = run(lambda: alerts.add("测试警报", popup=False), [])
    with open(alerts.ALERTS, encoding="utf-8") as f:
        out["live_alerts"] = f.read()
    m = ARGS.get("mirror")
    out["mirror_text"] = open(m, encoding="utf-8").read() if m and os.path.exists(m) else None
else:
    raise SystemExit(f"unknown scenario {scenario}")

out["orders"] = [{"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "notional": getattr(o, "notional", None),
                  "status": o.status.value, "cid": o.client_order_id} for o in B.orders]
out["paper_flags"] = B.paper_flags
out["runtime_files"] = sorted(os.listdir(paths.RUNTIME))
rep = os.path.join(paths.RUNTIME, config.REPORT_DIR)
out["report_files"] = sorted(os.listdir(rep)) if os.path.isdir(rep) else []
print("@@RESULT@@" + json.dumps(out, ensure_ascii=True))
