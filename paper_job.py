# -*- coding: utf-8 -*-
"""每日换仓的“调度外壳”：有限次数自动重试 + 运行锁。

计划任务在 17:00、17:30、18:00 各调用一次（同一个任务，每 30 分钟重复，共 3 次）：
  - 今天已经成功过 → 直接退出，什么都不做
  - 另一次运行还没结束（锁未释放且不到 25 分钟）→ 退出，避免同时跑两份
  - 否则：先跑 20 项安全自检，通过后再运行换仓（换仓程序内部还会核实台账、检查 STOP）
  - 失败：前两次只记日志，等下一次重试；第 3 次仍失败才报警（推送到手机）
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
from paths import rt

STATE = rt("logs", "paper_job_state.json")
LOCK = rt("logs", "paper_job.lock")
MAX_ATTEMPTS = 3
LOCK_STALE_MINUTES = 25


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
    """运行一次换仓。返回 'ok' / 'stopped' / 'blocked'；出错抛异常。"""
    import paper_rotation
    old = sys.argv
    sys.argv = ["paper_rotation.py", "--live"]
    try:
        return paper_rotation.main() or "ok"
    except SystemExit as e:          # 例如数据太旧时 sys.exit("...")
        raise RuntimeError(f"换仓程序中止：{e}") from None
    finally:
        sys.argv = old


def main(now: dt.datetime = None) -> int:
    now = now or dt.datetime.now()
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
        final = attempt >= MAX_ATTEMPTS
        print(f"----- 第 {attempt}/{MAX_ATTEMPTS} 次尝试（{now:%H:%M}）-----")
        try:
            if not run_preflight():
                raise RuntimeError("每日自检没通过")
            result = run_rotation()
            if result == "blocked":
                raise RuntimeError("有订单结果无法确认，未交易")
            st["success"] = True
            st["history"].append({"time": f"{now:%H:%M}", "result": result})
            save_state(st)
            if attempt > 1:
                print(f"第 {attempt} 次尝试成功（前面 {attempt - 1} 次失败，详见上方日志）")
            return 0
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
            print(traceback.format_exc())
            st["history"].append({"time": f"{now:%H:%M}", "result": "failed", "error": err})
            save_state(st)
            if final:
                alerts.add(f"今天的自动换仓 {MAX_ATTEMPTS} 次都失败了（最后一次：{err}），今天没有换仓，请检查")
            else:
                print(f"本次失败：{err}。30 分钟后自动重试（第 {attempt + 1}/{MAX_ATTEMPTS} 次）")
            return 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
