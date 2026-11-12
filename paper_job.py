# -*- coding: utf-8 -*-
"""每日换仓的“调度外壳”：有限次数自动重试 + 运行锁。

计划任务在 17:00、17:30、18:00 各调用一次（同一个任务，每 30 分钟重复，共 3 次）：
  - 今天已经成功过 → 直接退出，什么都不做
  - 另一次运行还没结束（锁未释放且不到 25 分钟）→ 退出，避免同时跑两份
  - 否则：先跑全部安全自检（tests 文件夹），通过后再运行换仓（换仓程序内部还会核实台账、检查 STOP）
  - 失败：前两次只记日志，等下一次重试；第 3 次仍失败才报警（推送到手机）
  - 行情还没更新到今天的收盘价：前两次先不换仓，等下一次再试；最后一次还没有，才按前一天的价格换仓并报警（1.0.2）
  - 工作日 16:30 以前（白天手动双击）：不换仓，也不算一次尝试——当天的收盘价还没出来（1.0.2）
重试不会造成重复下单：订单编号按“日期+标的+方向”固定，券商会拒绝同编号订单；
有未成交订单、台账里有结果不明的订单时，换仓程序本身也不会再下单。
"""
import os
import sys
import json
import traceback
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import alerts
import runmode
from paths import rt

STATE = rt("logs", "paper_job_state.json")
LOCK = rt("logs", "paper_job.lock")
MAX_ATTEMPTS = 3
LOCK_STALE_MINUTES = 25
# 1.0.2（2026-11-07）：
TRADE_AFTER = dt.time(16, 30)  # 工作日这个时间以前不换仓：16:00 收盘，当天的收盘价要过一会儿才出来
LAST_CALL = dt.time(18, 0)     # 这个时间以后开始的运行 = 今天最后一次自动运行（模拟盘 18:00、实盘 18:10），后面没有重试了
RETRY_IF_STALE = False         # main() 设置：后面还有自动运行时，行情没更新到今天的收盘价就等下一次（见 run_rotation）


class Retry(Exception):
    """这次先不换仓、等下一次自动运行（不是程序出错）。"""


def load_state(today: str) -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            return st
    except Exception:
        pass
    return {"date": today, "attempts": 0, "success": False, "history": []}


def save_state(st: dict):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def acquire_lock() -> bool:
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    if os.path.exists(LOCK):
        age = (dt.datetime.now().timestamp() - os.path.getmtime(LOCK)) / 60
        if age < LOCK_STALE_MINUTES:
            return False
        os.remove(LOCK)   # 超过 25 分钟的锁视为上次异常退出留下的
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {dt.datetime.now():%Y-%m-%d %H:%M:%S}".encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock():
    try:
        os.remove(LOCK)
    except FileNotFoundError:
        pass


def run_preflight() -> bool:
    import preflight
    return preflight.main() == 0


def run_rotation() -> str:
    """运行一次换仓。返回 'ok' / 'stopped' / 'blocked' / 'stale'（行情还没更新，只在 RETRY_IF_STALE 时）；出错抛异常。"""
    import paper_rotation
    old = sys.argv
    sys.argv = ["paper_rotation.py", "--live"] + (["--retry-if-stale"] if RETRY_IF_STALE else [])
    try:
        return paper_rotation.main() or "ok"
    except SystemExit as e:          # 例如数据太旧时 sys.exit("...")
        raise RuntimeError(f"换仓程序中止：{e}") from None
    finally:
        sys.argv = old


def live_gate():
    """实盘（NOIRE_PROFILE=live）：三道闸没全过 → 返回原因（这时不跑自检、不连券商、不下单）。模拟盘返回 None。"""
    import runmode
    if not runmode.IS_LIVE:
        return None
    import broker
    ok, why = broker.live_trading_ready()
    return None if ok else why


def too_early(now: dt.datetime) -> bool:
    """工作日（周一到周五）16:30 以前：当天的收盘价还没出来，不换仓（1.0.2）。
    白天手动双击 run_live.bat / run_paper.bat 会被挡住：不挡的话会按盘中价格换仓，而且今天算“已经成功”，
    傍晚用收盘价的正式运行反而会跳过。周末可以运行（用的是星期五的收盘价）。"""
    return now.weekday() < 5 and now.time() < TRADE_AFTER


def main(now: dt.datetime = None) -> int:
    global RETRY_IF_STALE
    now = now or dt.datetime.now()
    why_off = live_gate()
    if why_off:
        print(f"{now:%Y-%m-%d %H:%M} 实盘自动交易没有打开（{why_off}），本次不运行。"
              "只想看看会怎么买卖，可以双击 live_dryrun.bat")
        return 0
    if too_early(now):
        print(f"{now:%Y-%m-%d %H:%M} 工作日 {TRADE_AFTER:%H:%M} 以前不换仓：今天的收盘价还没出来（策略要用收盘价决定买什么）。"
              f"不用手动运行，今天 {'17:10' if runmode.IS_LIVE else '17:00'} 会自动运行。"
              + ("只想看看会怎么买卖，可以双击 live_dryrun.bat" if runmode.IS_LIVE else ""))
        return 0
    today = f"{now:%Y-%m-%d}"
    st = load_state(today)
    if st["success"]:
        print(f"{now:%H:%M} 今天的换仓已经成功完成（{st['history'][-1]['time'] if st['history'] else ''}），本次跳过")
        return 0
    if st["attempts"] >= MAX_ATTEMPTS:
        print(f"{now:%H:%M} 今天已尝试 {st['attempts']} 次，不再重试")
        return 1
    if not acquire_lock():
        print(f"{now:%H:%M} 另一次换仓还在运行，本次跳过")
        return 0
    try:
        st["attempts"] += 1
        attempt = st["attempts"]
        last_today = now.time() >= LAST_CALL          # 例如电脑 18:10 才醒：这就是今天最后一次自动运行
        final = attempt >= MAX_ATTEMPTS or last_today
        RETRY_IF_STALE = not final
        print(f"----- 第 {attempt}/{MAX_ATTEMPTS} 次尝试（{now:%H:%M}）-----")
        try:
            if not run_preflight():
                raise RuntimeError("每日自检没通过")
            result = run_rotation()
            if result == "blocked":
                raise RuntimeError("有订单结果无法确认，未交易")
            if result == "stale":
                raise Retry("行情还没更新到今天的收盘价")
            st["success"] = True
            st["history"].append({"time": f"{now:%H:%M}", "result": result})
            save_state(st)
            if attempt > 1:
                waited = all(h.get("result") == "retry" for h in st["history"][:-1])
                print(f"第 {attempt} 次尝试成功（前面 {attempt - 1} 次{'在等行情更新' if waited else '失败'}，详见上方日志）")
            return 0
        except Exception as e:
            waiting = isinstance(e, Retry)
            err = str(e)[:200] if waiting else f"{type(e).__name__}: {str(e)[:200]}"
            if not waiting:
                print(traceback.format_exc())
            st["history"].append({"time": f"{now:%H:%M}", "result": "retry" if waiting else "failed", "error": err})
            save_state(st)
            if final and attempt >= MAX_ATTEMPTS:
                alerts.add(f"今天的自动换仓 {MAX_ATTEMPTS} 次都失败了（最后一次：{err}），今天没有换仓，请检查")
            elif final:
                alerts.add(f"今天的自动换仓失败了（{now:%H:%M} 第 {attempt} 次，今天后面没有自动运行了；错误：{err}），"
                           "今天没有换仓，请检查")
            elif waiting:
                print(f"{err}，这次先不换仓。30 分钟后自动再试（第 {attempt + 1}/{MAX_ATTEMPTS} 次）；"
                      "今天最后一次自动运行时还没有，就按前一天的收盘价换仓并报警")
            else:
                print(f"本次失败：{err}。30 分钟后自动重试（第 {attempt + 1}/{MAX_ATTEMPTS} 次）")
            return 1
    finally:
        RETRY_IF_STALE = False
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
