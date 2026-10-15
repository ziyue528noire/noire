# -*- coding: utf-8 -*-
"""一键紧急停止 / 恢复（只作用于 Alpaca 模拟盘）

  python emergency.py stop    1) 写入 STOP 文件 → 所有程序拒绝交易
                              2) 停用 17:00 自动换仓计划任务
                              3) 撤销所有未成交订单
                              4) 市价卖出全部持仓（休市时会在下次开盘成交）
  python emergency.py resume  删除 STOP 文件，重新启用自动换仓
                              （不会立刻买回；下一次 17:00 运行时按信号重新建仓）
"""
import os
import sys
import time
import subprocess
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
import risk
from keys import load_keys
from paths import rt

ROTATION_TASK = "Noire Paper Rotation"
LOG = rt("logs", "emergency_log.txt")


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def set_task(enabled: bool):
    if os.name != "nt" or os.environ.get("EXC_RUNTIME_DIR"):
        return log(f"(非 Windows，跳过计划任务 {'启用' if enabled else '停用'})")
    r = subprocess.run(["schtasks", "/Change", "/TN", ROTATION_TASK, "/ENABLE" if enabled else "/DISABLE"],
                       capture_output=True, text=True)
    log(f"计划任务“{ROTATION_TASK}” {'已启用' if enabled else '已停用'}" if r.returncode == 0
        else f"计划任务操作失败（可能还没安装）：{(r.stderr or r.stdout).strip()[:150]}")


def stop():
    with open(risk.STOP_FILE, "w", encoding="utf-8") as f:
        f.write(f"紧急停止于 {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n删除本文件或运行 resume_trading.bat 恢复\n")
    log("已写入 STOP 文件：所有程序拒绝交易")
    set_task(False)
    key, secret = load_keys()
    if not key:
        return log("没有密钥，无法连接券商撤单/清仓")
    from alpaca.trading.client import TradingClient
    client = TradingClient(key, secret, paper=True)     # 只连模拟盘
    client.cancel_orders()
    log("已撤销所有未成交订单")
    time.sleep(2)
    positions = client.get_all_positions()
    if positions:
        client.close_all_positions(cancel_orders=True)
        for p in positions:
            log(f"已提交平仓：{p.symbol} {float(p.qty):g} 股（市值约 ${float(p.market_value):,.0f}）")
        clock = client.get_clock()
        if not clock.is_open:
            log("现在休市，平仓单会在下次开盘成交")
    else:
        log("没有持仓，无需平仓")
    log("紧急停止完成")


def resume():
    if os.path.exists(risk.STOP_FILE):
        os.remove(risk.STOP_FILE)
        log("已删除 STOP 文件")
    set_task(True)
    log("已恢复：下一次 17:00 自动换仓会按信号重新建仓")


if __name__ == "__main__":
    {"stop": stop, "resume": resume}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
