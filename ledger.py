# -*- coding: utf-8 -*-
"""订单台账：防止重复下单、处理“不知道到底下没下成功”的情况。

规则（借鉴 ChatGPT 版的做法）：
  1. 每笔订单有固定的唯一编号 client_order_id（同一天、同一标的、同一方向的策略单编号相同）
     → 就算程序被重复运行，券商也会拒绝第二笔同编号订单，不会重复下单。
  2. 提交之前先把“下单意图”写进台账（logs/order_ledger.jsonl），提交后再记结果。
  3. 提交时如果出错且无法确认结果（比如网络超时）→ 记为 UNKNOWN，立即停止后续下单，
     绝不自动重试；下次运行先向券商按编号查询，弄清楚之后才继续交易。
  4. 券商明确拒绝（HTTP 400/403/422，例如购买力不足）→ 记为 REJECTED：订单肯定没下成，
     报警说明原因，其余订单照常处理（2026-10-16 加；以前会被当成“结果未知”而停下）。
"""
import os
import json
import datetime as dt

from paths import rt

LEDGER = rt("logs", "order_ledger.jsonl")
PREFIX = "noire-"


class UncertainOrder(Exception):
    """提交结果未知：可能下单成功也可能没有。必须先核实，不能重试。"""


class RejectedOrder(Exception):
    """券商明确拒绝（例如购买力不足、数量不对）：订单肯定没有下成，不用核实，可以继续处理其他订单。"""


# 券商返回这些 HTTP 状态码 = 明确拒绝、订单没有建立（400 请求有误 / 403 购买力不足或没有权限 / 422 参数不对）。
# 超时、断线、429（太频繁）、5xx 等都不算“明确”，仍按“结果未知”处理。
DEFINITE_REJECT_STATUS = (400, 403, 422)


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def record(event: str, **fields):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    if not os.path.exists(LEDGER):
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": _now(), "event": "LEDGER_START"}, ensure_ascii=False) + "\n")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": _now(), "event": event, **fields}, ensure_ascii=False, default=str) + "\n")


def entries() -> list:
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def ledger_start():
    """台账开始的时间（之前的订单是旧系统下的，没有编号，核对时忽略）。"""
    for e in entries():
        if e.get("event") == "LEDGER_START":
            return dt.datetime.fromisoformat(e["time"])
    return None


def make_id(kind: str, symbol: str, side: str, day: dt.date = None, extra: str = "") -> str:
    """kind: rot=策略换仓, man=手动下单。最长 128 字符（券商限制）。"""
    day = day or dt.date.today()
    cid = f"{PREFIX}{kind}-{day:%Y%m%d}-{symbol}-{side}" + (f"-{extra}" if extra else "")
    return cid[:128]


def _is_duplicate_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "client_order_id" in msg and ("unique" in msg or "duplicate" in msg or "already" in msg)


def _reason(e: Exception) -> str:
    """券商错误信息里的 message（拿不到就用原文）。"""
    try:
        return str(json.loads(str(e)).get("message") or str(e))[:150]
    except Exception:
        return str(e)[:150]


def submit(client, request, meta: dict):
    """先记意图，再提交。返回券商订单对象；重复编号被拒时返回 None（说明之前已经下过）。
    券商明确拒绝 → RejectedOrder；结果不明 → UncertainOrder。"""
    cid = request.client_order_id
    record("INTENT", client_order_id=cid, **meta)
    try:
        o = client.submit_order(request)
    except Exception as e:
        if _is_duplicate_error(e):
            record("DUPLICATE_BLOCKED", client_order_id=cid, error=str(e)[:200])
            try:   # 同编号的订单已经在券商那里（例如网络库自动重试过一次）→ 记下来，免得核对时误报“台账没有记录”
                old = client.get_order_by_client_id(cid)
                record("RESOLVED_FOUND", client_order_id=cid, broker_id=str(old.id),
                       status=getattr(old.status, "value", str(old.status)))
            except Exception:
                pass
            return None
        status = getattr(e, "status_code", None)
        if status in DEFINITE_REJECT_STATUS:
            record("REJECTED", client_order_id=cid, http_status=status, error=str(e)[:200])
            raise RejectedOrder(f"{cid} 被券商拒绝：{_reason(e)}") from e
        record("UNKNOWN", client_order_id=cid, error=f"{type(e).__name__}: {str(e)[:200]}")
        raise UncertainOrder(f"{cid} 提交结果未知：{type(e).__name__}") from e
    record("SUBMITTED", client_order_id=cid, broker_id=str(o.id),
           status=getattr(o.status, "value", str(o.status)))
    return o


def unresolved_ids() -> list:
    """有 INTENT 但没有任何结论（SUBMITTED / DUPLICATE_BLOCKED / RESOLVED_*）的编号。"""
    state = {}
    for e in entries():
        cid = e.get("client_order_id")
        if not cid:
            continue
        ev = e["event"]
        if ev in ("INTENT", "UNKNOWN"):
            state[cid] = "open"
        elif ev in ("SUBMITTED", "DUPLICATE_BLOCKED", "REJECTED", "RESOLVED_FOUND", "RESOLVED_NOT_FOUND"):
            state[cid] = "done"
    return [c for c, s in state.items() if s == "open"]


def resolve(client) -> list:
    """向券商按编号查询所有未落实的订单。返回仍然无法确认的编号（非空 = 不能继续交易）。"""
    still = []
    for cid in unresolved_ids():
        try:
            o = client.get_order_by_client_id(cid)
            record("RESOLVED_FOUND", client_order_id=cid, broker_id=str(o.id),
                   status=getattr(o.status, "value", str(o.status)))
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "404" in msg or "40410000" in msg:
                record("RESOLVED_NOT_FOUND", client_order_id=cid)
            else:
                still.append(cid)
    return still


def submitted_ids() -> set:
    return {e["client_order_id"] for e in entries()
            if e.get("event") in ("SUBMITTED", "RESOLVED_FOUND") and e.get("client_order_id")}
