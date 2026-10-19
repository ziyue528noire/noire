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
    out["result"], out["error"], out["stdout"] = run(paper_rotation.main, ["--live"] if ARGS.get("submit") else [])
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
    keys.load_keys = lambda: ("LIVE_K", "LIVE_S")
    import paper_job
    calls = []
    paper_job.run_preflight = lambda: calls.append("preflight") or True
    paper_job.run_rotation = lambda: calls.append("rotation") or "ok"
    out["result"], out["error"], out["stdout"] = run(paper_job.main, [])
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
else:
    raise SystemExit(f"unknown scenario {scenario}")

out["orders"] = [{"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "notional": getattr(o, "notional", None),
                  "status": o.status.value, "cid": o.client_order_id} for o in B.orders]
out["paper_flags"] = B.paper_flags
out["runtime_files"] = sorted(os.listdir(paths.RUNTIME))
rep = os.path.join(paths.RUNTIME, config.REPORT_DIR)
out["report_files"] = sorted(os.listdir(rep)) if os.path.isdir(rep) else []
print("@@RESULT@@" + json.dumps(out, ensure_ascii=True))
