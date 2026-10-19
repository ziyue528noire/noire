# -*- coding: utf-8 -*-
"""每日自检：自动换仓前先跑全部安全测试。任何一项不通过 → 返回 1，当天不交易并报警。

测试在单独的进程里运行（使用临时目录和假券商），不会碰真实账户和文件。
"""
import os
import sys
import subprocess
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import alerts
from paths import rt

LOG = rt("logs", "tests_last.txt")


def main() -> int:
    # 测试永远在模拟盘模式、临时目录里跑（实盘任务调用自检时也一样），绝不碰真实账户和文件
    env = {k: v for k, v in os.environ.items() if k not in ("EXC_RUNTIME_DIR", "NOIRE_PROFILE")}
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}\n{out}")
    summary = [l for l in out.splitlines() if l.startswith("Ran ") or l.startswith("OK") or l.startswith("FAILED")]
    if r.returncode != 0:
        msg = f"每日自检没通过（{' '.join(summary) or '测试无法运行'}），今天不自动交易。详情见 logs\\tests_last.txt"
        print(msg)
        alerts.add(msg)
        return 1
    print(f"每日自检通过：{' '.join(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
