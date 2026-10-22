# -*- coding: utf-8 -*-
"""“偷看未来”陷阱测试（2026-10-22 加）。

做法：造一份确定的假行情（不用随机数、不依赖今天日期），把某一天 D 之后（或从 D 开始）的价格故意改得面目全非
（每天交替 ×3 / ×0.3），再确认 D 之前已经做出的决定一点都没变。
如果变了，说明程序在做那天的决定时偷看了“当时还不知道的价格”。

检查两类代码：
  1. 实盘用的计算：aplus.compute（4 批信号 + 杠杆）、dipbuy.positions（第二条腿）
     —— 第 D 天收盘后算的目标，只能用 D 天收盘及以前的价格。
  2. 研究引擎（research/ 文件夹）里现在用来算 A+ 稳健版和第二条腿的函数
     —— 第 D 天开盘生效的仓位和杠杆，只能用 D-1 天收盘及以前的价格（D 天的开盘价也不行：下单时还不知道）。
最后还有一项“陷阱本身是否灵”的检查：故意写一个偷看明天价格的函数，确认陷阱能抓到它。

已知的历史问题（不在每日自检里，因为它只影响旧报告）：research_v2 / research.py（v1）用“开盘到开盘”的收益算杠杆，
会用到当天开盘价。2026-10-22 用真实数据量过：A+ 稳健版年化 13.1% vs 改正后 13.2%，结论不变；
v3、v4、v5 和 expectations.py 都已用收盘价，和实盘一致。
"""
import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(TESTS)
RESEARCH = os.path.join(PROJECT, "research")
sys.path.insert(0, PROJECT)

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402
import config                            # noqa: E402
import aplus                             # noqa: E402
import dipbuy                            # noqa: E402

UNI = list(config.ROTATION_UNIVERSE)
COLS = UNI + [config.CASH_SYMBOL]
N = 700


def fake_closes(n=N):
    """确定的假行情：每只有不同的长期趋势和不同节奏的起伏，排名、波动、趋势都会随时间变化。"""
    i = np.arange(n)[:, None]
    k = np.arange(len(COLS))[None, :]
    rets = 0.0004 * (k - 3) / 3 + 0.012 * np.sin(i * (0.7 + 0.13 * k) + k) + 0.006 * np.sin(i / 37.0 + k)
    idx = pd.bdate_range("2021-01-04", periods=n)
    return pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=COLS)


def fake_opens(closes):
    i = np.arange(len(closes))[:, None]
    k = np.arange(closes.shape[1])[None, :]
    return closes.shift(1).fillna(closes) * (1 + 0.003 * np.cos(i * 1.3 + k))


def scramble_from(df, day, first_up=False):
    """把 day 当天及以后的价格改乱（每天交替 ×0.3、×3；first_up=True 时先 ×3）；之前的完全不动。"""
    x = df.copy()
    m = x.index >= pd.Timestamp(day)
    a, b = (3.0, 0.3) if first_up else (0.3, 3.0)
    f = np.where(np.arange(m.sum()) % 2, b, a)
    x.loc[m] = x.loc[m].values * f[:, None]
    return x


def scrambles(df, day):
    """两个方向都试：只试一个方向时，偷看未来的规则可能碰巧得出同样的结论（2026-10-22 自检时真的发生过）。"""
    return [scramble_from(df, day, False), scramble_from(df, day, True)]


def cut_days(closes):
    """检查的几天：某批的换仓日、换仓日的下一天、普通的一天、很靠后的一天。"""
    sig = aplus.signal_dates(closes.index)[config.TRANCHE_OFFSETS[0]]
    d0 = pd.Timestamp(sig[len(sig) // 2])
    pos = closes.index.get_loc(d0)
    return [d0, closes.index[pos + 1], closes.index[400], closes.index[-30]]


class LiveCodeNoPeek(unittest.TestCase):
    """实盘的计算：第 D 天收盘后的决定，不受 D 之后价格的影响。"""

    def setUp(self):
        self.c = fake_closes()
        self.cal = self.c.index                  # 交易日历（日期本身事先就知道，不算偷看）

    def test_aplus_target_ignores_future_prices(self):
        for d in cut_days(self.c):
            nxt = self.c.index[self.c.index.get_loc(d) + 1]
            t1, l1, _ = aplus.compute(self.c, self.cal, as_of=d)
            t3, l3, _ = aplus.compute(self.c.loc[:d], self.cal, as_of=d)     # 干脆不给未来数据
            pd.testing.assert_series_equal(t1, t3, check_names=False)
            self.assertEqual(l1, l3)
            for dirty in scrambles(self.c, nxt):
                t2, l2, _ = aplus.compute(dirty, self.cal, as_of=d)
                pd.testing.assert_series_equal(t1, t2, check_names=False)
                self.assertEqual(l1, l2)

    def test_aplus_decisions_actually_vary(self):
        """防止“假通过”：假行情要让不同日子的目标真的不一样，陷阱才有意义。"""
        ts = {tuple(aplus.compute(self.c, self.cal, as_of=d)[0].round(6).items()) for d in cut_days(self.c)}
        levs = {round(aplus.compute(self.c, self.cal, as_of=d)[1], 6) for d in cut_days(self.c)}
        self.assertGreater(len(ts), 1)
        self.assertGreater(len(levs), 1)

    def test_dip_positions_ignore_future_prices(self):
        spy = self.c["SPY"]
        p_clean = dipbuy.positions(spy)
        self.assertGreater(float(p_clean.sum()), 0)                 # 假行情里第二条腿确实触发过
        self.assertLess(float(p_clean.sum()), len(p_clean))
        for d in cut_days(self.c):
            nxt = self.c.index[self.c.index.get_loc(d) + 1]
            for dirty in scrambles(spy.to_frame(), nxt):
                p_dirty = dipbuy.positions(dirty["SPY"])
                pd.testing.assert_series_equal(p_clean.loc[:d], p_dirty.loc[:d])


class TrapSelfCheck(unittest.TestCase):
    """陷阱本身灵不灵：一个偷看明天收盘价的“坏策略”必须被抓到。"""

    def test_trap_catches_a_peeking_rule(self):
        c = fake_closes()["SPY"]

        def cheater(close):                      # 明天会涨就今天买 —— 典型的偷看未来
            return (close.shift(-1) > close).astype(float)

        for i in (300, 400, 401, 500):                # 换几天试，每一天都必须抓到
            d, nxt = c.index[i], c.index[i + 1]
            clean = cheater(c).loc[:d]
            caught = any(not clean.equals(cheater(x["SPY"]).loc[:d]) for x in scrambles(c.to_frame(), nxt))
            self.assertTrue(caught, f"{d.date()}：陷阱没抓到偷看未来的规则，说明陷阱本身不灵")

    def test_trap_catches_using_todays_open(self):
        """研究里最容易犯的错：第 D 天开盘生效的杠杆用到了 D 天的开盘价（research_v2 的老写法）。"""
        sys.path.insert(0, RESEARCH)
        if not os.path.exists(os.path.join(RESEARCH, "research_v2.py")):
            self.skipTest("没有 research 文件夹")
        import research_v2 as v
        c = fake_closes()
        o = fake_opens(c)
        W = v.make_W(c, UNI, 2, "avg", "invvol", 0)

        def lev_from_opens(oo):                        # 老写法：开盘到开盘的收益
            R = (oo.shift(-1) / oo - 1)[UNI].copy()
            R["CASH"] = (oo.shift(-1) / oo - 1)[config.CASH_SYMBOL]
            return v.lev_vol(W, R.fillna(0.0), 0.30)

        clean = lev_from_opens(o)
        caught = 0
        for d in cut_days(c):
            caught += any(not clean.loc[:d].equals(lev_from_opens(x).loc[:d]) for x in scrambles(o, d))
        self.assertGreater(caught, 0, "陷阱没抓到“用当天开盘价算杠杆”")


@unittest.skipUnless(os.path.exists(os.path.join(RESEARCH, "research_v3.py")), "没有 research 文件夹")
class ResearchNoPeek(unittest.TestCase):
    """研究引擎：第 D 天开盘生效的仓位和杠杆，不受 D 天及以后价格（包括 D 天开盘价）的影响。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, RESEARCH)
        import research as r
        import research_v2 as v
        import research_v3 as v3
        cls.r, cls.v, cls.v3 = r, v, v3

    def setUp(self):
        self.c = fake_closes()
        self.o = fake_opens(self.c)

    def returns(self, o, c):
        """和研究引擎一样：R = 开盘到下一天开盘（算成绩）；R2 = 收盘到收盘（算杠杆，和实盘一致）。"""
        oo = (o.shift(-1) / o - 1)
        R = oo[UNI].copy()
        R["CASH"] = oo[config.CASH_SYMBOL]
        cc = c.pct_change()
        R2 = cc[UNI].copy()
        R2["CASH"] = cc[config.CASH_SYMBOL]
        return R.fillna(0.0), R2.fillna(0.0)

    def test_research_aplus_positions_and_leverage_ignore_today_and_later(self):
        v, v3 = self.v, self.v3
        irx = pd.Series(0.02, index=self.c.index)
        R, R2 = self.returns(self.o, self.c)
        for d, first_up in [(d, f) for d in cut_days(self.c) for f in (False, True)]:
            dc, do = scramble_from(self.c, d, first_up), scramble_from(self.o, d, first_up)
            Rd, R2d = self.returns(do, dc)
            for phase in (0, 2):
                W1 = sum(v.make_W(self.c, UNI, 2, "avg", "invvol", phase + 5 * j) for j in range(4)) / 4
                W2 = sum(v.make_W(dc, UNI, 2, "avg", "invvol", phase + 5 * j) for j in range(4)) / 4
                pd.testing.assert_frame_equal(W1.loc[:d], W2.loc[:d])
                # research_v3 / v4 / expectations 用的就是这一套：仓位 × 杠杆 = 风险资产总仓位
                _, g1 = v3.aplus_base(phase, self.c, irx, R, R2)
                _, g2 = v3.aplus_base(phase, dc, irx, Rd, R2d)
                pd.testing.assert_series_equal(g1.loc[:d], g2.loc[:d])
            pd.testing.assert_series_equal(self.r.lev_trend(self.c).loc[:d], self.r.lev_trend(dc).loc[:d])

    def test_research_dip_signal_ignores_future(self):
        spy = pd.DataFrame({"Open": self.o["SPY"], "High": self.c["SPY"], "Low": self.c["SPY"], "Close": self.c["SPY"]})
        p1 = self.v3.s1_rsi2(spy)
        for d in cut_days(self.c):
            nxt = self.c.index[self.c.index.get_loc(d) + 1]
            for dirty in scrambles(spy, nxt):
                p2 = self.v3.s1_rsi2(dirty)
                pd.testing.assert_series_equal(p1.loc[:d], p2.loc[:d])

    def test_research_matches_live_on_same_prices(self):
        """同一份价格：研究引擎第 D+1 天开盘的风险仓位 = 实盘在第 D 天收盘后算的目标（不含 SHY）。"""
        v, r = self.v, self.r
        _, R2 = self.returns(self.o, self.c)
        W = sum(v.make_W(self.c, UNI, 2, "avg", "invvol", k) for k in config.TRANCHE_OFFSETS) / 4
        lev = v.lowest(v.lev_vol(W, R2, config.VOL_TARGET), r.lev_trend(self.c).reindex(W.index))
        for d in cut_days(self.c)[:3]:
            nxt = self.c.index[self.c.index.get_loc(d) + 1]
            live, _, _ = aplus.compute(self.c, self.c.index, as_of=d)
            research = W.loc[nxt, UNI] * lev.loc[nxt]
            for s in UNI:
                self.assertAlmostEqual(float(live.get(s, 0.0)), float(research[s]), places=9, msg=f"{d.date()} {s}")


if __name__ == "__main__":
    unittest.main()
