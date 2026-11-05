# -*- coding: utf-8 -*-
r"""出入金（往真钱账户存钱 / 取钱）：让收益率、回撤和单日涨跌不把存钱当成赚钱、取钱当成亏钱（2026-11-05 加）。

  - 来源：券商“账户活动”里的 CSD（入金）和 CSW（出金），只读查询（GET /v2/account/activities）。
  - 记录：<运行文件夹>\cash_flows.csv（实盘就是 live\cash_flows.csv），每笔一行，按券商编号去重；
    assigned_to = 记到哪一天的成绩单；"start" = 实盘起点以前就存进来的钱（已经算在起点净值里，不再计）。
  - equity_history.csv 的 flow 列 = 记到当天的出入金合计（存钱为正，取钱为负；空 = 0）。
  - 日收益 = (当天净值 − 当天出入金) ÷ 前一天净值 − 1：把出入金当作当天收盘后才到账（新钱当天只是现金，
    第二天开盘才买进去，当天不赚不亏）；按时间加权（基金净值的算法），存取多少钱都不改变收益率。
    净值本身照旧记账户里真实的钱，和券商对得上。
  - 一笔钱记到哪一天：17:10 换仓时第一次看到的新入金/出金，记到当天那一行。如果中间有几天没记成绩单
    （电脑没开、查询失败等），这几天记进记录文件的钱会一起算进下一次记的那一行。
  - “今日涨跌”（监控、状态页、单日亏损风控）用的是券商“昨日净值”之后到账的钱：按到账时间算，和记录文件无关。
  - 模拟盘没有出入金：不查询，flow 列留空，所有结果和以前完全一样。
查询失败（网络等）不影响交易：当天不记成绩单、状态页写提醒，下一次运行连同这一天一起补上。
"""
import csv
import datetime as dt
import os

import runmode
from paths import rt

LEDGER = rt("cash_flows.csv")
HISTORY = rt("equity_history.csv")
LEDGER_FIELDS = ["id", "created_at", "date", "type", "amount", "assigned_to"]
TYPES = ("CSD", "CSW")          # CSD = 存钱（入金），CSW = 取钱（出金）
PAGE = 100


# ---------- 纯计算（成绩单、回撤、对账共用） ----------
def flow_of(row) -> float:
    """equity_history 一行里的出入金（空 / 没有这一列 / 写错了 = 0）。"""
    try:
        return float(row.get("flow") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def has_flows(rows) -> bool:
    """除第一天以外有没有出入金（第一天的出入金已经算在起点里，不影响收益）。"""
    return any(abs(flow_of(r)) >= 0.005 for r in list(rows)[1:])


def total_flows(rows) -> float:
    return sum(flow_of(r) for r in list(rows)[1:])


def index_values(rows) -> list:
    """按时间加权的净值指数（第一天 = 1）。没有出入金时就是 净值 ÷ 第一天净值。"""
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            out.append(1.0)
            continue
        prev = float(rows[i - 1]["equity"])
        cur = float(r["equity"]) - flow_of(r)
        out.append(out[-1] * (cur / prev) if prev > 0 else out[-1])
    return out


def _history_dates():
    try:
        with open(HISTORY, encoding="utf-8-sig", errors="replace") as f:
            return sorted(r["date"][:10] for r in csv.DictReader(f) if r.get("date") and r.get("equity"))
    except OSError:
        return []


def first_day():
    """成绩单第一天（实盘起点）。还没有记录时返回 None。"""
    d = _history_dates()
    return d[0] if d else None


def last_day(before=None):
    """成绩单最后一天（before = 只看这天以前的）。没有时返回 None。"""
    d = [x for x in _history_dates() if before is None or x < _day(before)]
    return d[-1] if d else None


# ---------- 小工具（容忍手改过的记录文件） ----------
def _day(x):
    """各种写法的日期 → 'YYYY-MM-DD'；认不出返回 None。Excel 会把 2026-11-05 存成 2026/11/5。"""
    if x is None:
        return None
    if hasattr(x, "strftime"):
        return f"{x:%Y-%m-%d}"
    s = str(x).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _amount(v):
    """金额（容忍 −300、$-300、1,000 这类写法）；认不出返回 None。"""
    s = str(v if v is not None else "").strip()
    for a, b in (("−", "-"), ("－", "-"), ("$", ""), ("＄", ""), (",", ""), ("，", ""), (" ", "")):
        s = s.replace(a, b)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return None


def _utc(ts):
    """券商的时间（ISO，UTC）→ 带时区的 datetime；认不出返回 None。"""
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return t if t.tzinfo else None
    except (ValueError, TypeError):
        return None


def _as_date(x):
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


def prev_close(client, now=None):
    """券商“昨日净值”对应的那次收盘（上一个交易日 16:00，本机时间 = 美东），带时区。
    交易日历查不到时用上一个工作日 16:00——只会比真实收盘晚，最多少扣一点钱，回到以前的算法，绝不会多扣。"""
    now = now or dt.datetime.now()
    today = now.date()
    day = None
    try:
        from alpaca.trading.requests import GetCalendarRequest
        cal = client.get_calendar(GetCalendarRequest(start=today - dt.timedelta(days=10), end=today))
        days = sorted({_as_date(c.date) for c in cal} - {today})
        days = [d for d in days if d < today]
        if days:
            day = days[-1]
    except Exception:
        day = None
    if day is None:
        day = today - dt.timedelta(days=1)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(16, 0)).astimezone()


# ---------- 券商记录 ----------
def fetch(client, since_days=400) -> list:
    """券商的入金/出金记录（只读）。返回 [{id, created_at, date, type, amount}]，amount 存钱为正、取钱为负。
    只算已经完成（executed）的；还在路上的钱不在净值里，也不算。"""
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, seen, token = [], set(), None
    for _ in range(50):
        params = {"activity_types": ",".join(TYPES), "direction": "asc", "page_size": PAGE, "after": after}
        if token:
            params["page_token"] = token
        page = client.get("/account/activities", params) or []
        if isinstance(page, dict):          # 有的版本把列表包在一层里
            page = page.get("activities") or page.get("data") or []
        for x in page:
            kind = str(x.get("activity_type") or "")
            if kind not in TYPES:
                continue
            if str(x.get("status") or "executed").lower() != "executed":
                continue
            amount = abs(_amount(x.get("net_amount")) or 0.0)
            if kind == "CSW":
                amount = -amount
            created = str(x.get("created_at") or "")
            aid = str(x.get("id") or f"{kind}-{created}-{amount:.2f}")
            if aid in seen:
                continue
            seen.add(aid)
            out.append({"id": aid, "created_at": created, "date": str(x.get("date") or created)[:10],
                        "type": kind, "amount": amount})
        if len(page) < PAGE:
            break
        token = page[-1].get("id")
        if not token:
            break
    return out


def read_ledger():
    """已经记过的出入金。还没有建这个文件（第一次用）时返回 None。
    文件被手改过也尽量读（Excel 另存的 GBK 编码也认得）。"""
    if not os.path.exists(LEDGER):
        return None
    try:
        with open(LEDGER, encoding="utf-8-sig") as f:
            return [r for r in csv.DictReader(f) if r.get("id")]
    except UnicodeDecodeError:            # 中文 Windows 上用 Excel 另存过（GBK 编码）
        with open(LEDGER, encoding="gbk", errors="replace") as f:
            return [r for r in csv.DictReader(f) if r.get("id")]


def _write_ledger(rows):
    os.makedirs(os.path.dirname(LEDGER) or ".", exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, LEDGER)


def _local(ts: str) -> str:
    """券商的 UTC 时间 → 本机时间（电脑时区 = 美东）。"""
    t = _utc(ts)
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else str(ts)


class Flows:
    """查一次券商的出入金记录（每次换仓 / 监控各查一次）。查不到时 ok=False，所有数字按 0 算，不影响交易。"""

    def __init__(self, client, enabled=None):
        self.ok, self.error, self.acts, self.warnings = True, "", [], []
        if enabled is None:
            enabled = runmode.IS_LIVE
        if not enabled:
            return
        for _ in range(2):
            try:
                self.acts = fetch(client)
                self.ok, self.error = True, ""
                return
            except Exception as e:          # 网络、接口变化等：只影响记账，不影响交易
                self.ok, self.error = False, f"{type(e).__name__}: {str(e)[:120]}"

    @classmethod
    def unknown(cls, error):
        """出错时用的“不知道”对象：所有数字按 0 算、不记账。"""
        f = cls(None, enabled=False)
        f.ok, f.error = False, error
        return f

    # —— 今天的涨跌用：券商“昨日净值”之后到账的钱（按到账时间，和记录文件无关）——
    def since(self, cutoff) -> float:
        """cutoff 之后到账的出入金合计。时间认不出的不算（少扣 = 以前的算法，安全的方向）。"""
        if not self.ok:
            return 0.0
        total = 0.0
        for a in self.acts:
            t = _utc(a["created_at"])
            if t is not None and t > cutoff:
                total += a["amount"]
        return total

    # —— 成绩单用：按记录文件记到哪一天 ——
    def _new(self, ledger, start):
        """券商有、本地还没记的 → [(记录, 是不是起点以前就存进来的)]。"""
        known = {r["id"] for r in (ledger or [])}
        out = []
        for a in self.acts:
            if a["id"] in known:
                continue
            # 第一次用（还没有记录文件）时，实盘起点那天及以前的入金已经算在起点净值里
            pre = ledger is None and (start is None or (bool(a["date"]) and a["date"] <= start))
            out.append((a, pre))
        return out

    def _ledger_sum(self, ledger, after, upto=None) -> float:
        """记录文件里记到 (after, upto] 这几天的出入金（不含 start）。写错的行跳过并记下提醒。"""
        total = 0.0
        for r in ledger or []:
            if str(r.get("assigned_to", "")).strip() == "start":
                continue
            d = _day(r.get("assigned_to"))
            amt = _amount(r.get("amount"))
            if d is None or amt is None:
                self.warnings.append(f"出入金记录有一行看不懂，已跳过：{r.get('id')}")
                continue
            if (after is None or d > after) and (upto is None or d <= upto):
                total += amt
        return total

    def pending(self, start=None) -> float:
        """券商已经到账、但还没进记录文件的出入金合计。"""
        if not self.ok:
            return 0.0
        return sum(a["amount"] for a, pre in self._new(read_ledger(), start) if not pre)

    def unrecorded(self, start=None, last_row=None) -> float:
        """还没算进成绩单最后一行（last_row 那天）的出入金：记录文件里记在它之后的 + 还没进记录文件的。"""
        if not self.ok:
            return 0.0
        return self._ledger_sum(read_ledger(), _day(last_row)) + self.pending(start)

    def assign(self, day, start=None, last_row=None):
        """正式换仓记成绩单时调用：把新看到的出入金记到今天，返回今天这一行的出入金
        （= 记录文件里记在 last_row 之后、今天及以前的；last_row = 成绩单上一次记的那天）。
        查询失败返回 None（不知道）。同一天重跑不会重复记（按券商编号去重）。"""
        if not self.ok:
            return None
        day = _day(day)
        ledger = read_ledger()
        new = self._new(ledger, start)
        rows = list(ledger or [])
        for a, pre in new:
            rows.append({"id": a["id"], "created_at": _local(a["created_at"]), "date": a["date"], "type": a["type"],
                         "amount": f"{a['amount']:.2f}", "assigned_to": "start" if pre else day})
        if new or ledger is None:
            _write_ledger(rows)
        return self._ledger_sum(rows, _day(last_row), day)


def describe(amount: float) -> str:
    """+$300.00（存钱） / −$200.00（取钱）"""
    return f"{'+' if amount >= 0 else '−'}${abs(amount):,.2f}（{'存钱' if amount >= 0 else '取钱'}）"
