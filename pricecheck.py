# -*- coding: utf-8 -*-
"""收盘价核对（1.0.2，2026-11-07）：换仓前把 Yahoo 的最新收盘价和券商 Alpaca 的日线收盘价比一比。

为什么：策略用 Yahoo 的收盘价决定买什么、买多少。Yahoo 偶尔会给错价格，以前只有“一天涨跌超过 25%”这条
很宽的保护能抓出来。现在每次换仓前再和 Alpaca 核对一次，差 1% 以上就报警，让人检查。

只报警，不阻止交易：核对本身出任何错（网络、没有数据权限、超时）都只打印一行，交易照常。
Alpaca 的数据先用全市场（SIP）日线，收盘价就是官方收盘价；免费账户只能查 15 分钟以前的全市场数据，
所以只查到“现在 − 20 分钟”。没有权限时改用 IEX（只有 IEX 一家交易所的成交，收盘价可能差一点点，所以门槛定得宽）。
"""
import threading
import datetime as dt

MAX_GAP = 0.01      # 差 1% 以上才算不一致：正常情况两边几乎一样，这里只抓明显的错误
TIMEOUT = 20        # 秒：Alpaca 20 秒没回音就不等了（不能耽误换仓）


def _alpaca_closes(api, key, secret, symbols, as_of, now_utc):
    """Alpaca 在 as_of 那天的日线收盘价：(数据源, {标的: 收盘价})。先试 SIP，再试 IEX；都不行就抛出最后一个错误。"""
    StockHistoricalDataClient, StockBarsRequest, TimeFrame, DataFeed = api
    client = StockHistoricalDataClient(key, secret)
    start = dt.datetime.combine(as_of, dt.time(0, 0), tzinfo=dt.timezone.utc)
    end = now_utc - dt.timedelta(minutes=20)
    err = RuntimeError(f"Alpaca 没有 {as_of} 的日线")
    for feed in (DataFeed.SIP, DataFeed.IEX):
        try:
            res = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day,
                                                         start=start, end=end, feed=feed))
        except Exception as e:
            err = e
            continue
        data = getattr(res, "data", None) or {}
        out = {}
        for s in symbols:
            for b in data.get(s) or []:
                # 日线的时间是美东 0 点（= UTC 4 点或 5 点），按 UTC 算日期也是同一天
                if b.timestamp.astimezone(dt.timezone.utc).date() == as_of and float(b.close) > 0:
                    out[s] = float(b.close)
        if out:
            return str(getattr(feed, "value", feed)), out
        err = RuntimeError(f"Alpaca 没有 {as_of} 的日线")
    raise err


def check(key, secret, last_close, as_of: dt.date, now: dt.datetime = None):
    """返回 (结果, 一行说明)。结果：True = 一致；False = 有标的差 1% 以上（调用方报警）；None = 没核对成（不影响交易）。
    last_close：Yahoo 在 as_of 那天的收盘价（pandas Series，标的 → 价格）。now：现在（不带时区 = 本机时间）。"""
    now_utc = (now or dt.datetime.now()).astimezone(dt.timezone.utc)
    symbols = [str(s) for s in last_close.index]
    try:      # 在主线程里导入（很快）；后台线程只管联网，卡住了也不会占着导入锁
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
    except Exception as e:
        return None, f"收盘价核对没做成（{type(e).__name__}: {str(e)[:120]}），不影响交易"
    api = (StockHistoricalDataClient, StockBarsRequest, TimeFrame, DataFeed)
    box = {}

    def work():
        try:
            box["res"] = _alpaca_closes(api, key, secret, symbols, as_of, now_utc)
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=work, daemon=True)     # 放在后台线程里：卡住了也只等 TIMEOUT 秒
    t.start()
    t.join(TIMEOUT)
    if t.is_alive():
        return None, f"收盘价核对没做成（Alpaca {TIMEOUT:g} 秒没回音），不影响交易"
    if "err" in box:
        e = box["err"]
        return None, f"收盘价核对没做成（{type(e).__name__}: {str(e)[:120]}），不影响交易"
    feed, alp = box["res"]
    gaps = {s: float(last_close[s]) / alp[s] - 1 for s in symbols if s in alp}
    bad = {s: g for s, g in gaps.items() if not abs(g) <= MAX_GAP}       # 价格是 NaN 也算不一致
    missing = [s for s in symbols if s not in alp]
    miss = f"；{'、'.join(missing)} 没有 Alpaca 日线，没核对" if missing else ""
    if bad:
        parts = [f"{s} Yahoo {float(last_close[s]):.2f} / Alpaca {alp[s]:.2f}（差 {g:+.1%}）" for s, g in bad.items()]
        return False, (f"收盘价核对不一致（{as_of}）：{'；'.join(parts)}。今天照常按 Yahoo 的价格换仓，"
                       f"请让 Claude 检查数据（Alpaca {feed} 数据{miss}）")
    worst = max(gaps, key=lambda s: abs(gaps[s]))
    return True, f"收盘价核对：{len(gaps)} 只和 Alpaca（{feed}）一致，最大差 {abs(gaps[worst]):.2%}（{worst}）{miss}"
