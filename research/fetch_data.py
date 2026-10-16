# -*- coding: utf-8 -*-
"""研究用：下载完整历史日线（1993 年起，各 ETF 从上市日开始）+ 13 周国库券利率 ^IRX。
只下载数据，不交易。保存到 research/data/（不进 git）。
CORE 是现行策略用到的；EXTRA 是研究“扩大可选范围”用的候选（房地产、大宗商品、各类债券、行业等）。"""
import os
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
CORE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "SHY", "^IRX"]
EXTRA = ["IEF", "LQD", "HYG", "TIP", "AGG", "BIL",            # 债券 / 现金
         "IYR", "VNQ", "DBC", "GSG", "SLV",                     # 房地产 / 大宗商品
         "EWJ", "VGK", "VWO", "MDY", "DIA",                     # 其他股票市场
         "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]   # 美股 9 个行业


def fetch(s):
    df = yf.download(s, start="1993-01-01", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    name = s.replace("^", "")
    df.to_csv(os.path.join(OUT, f"{name}.csv"))
    return name, df


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    ok, bad = 0, []
    for s in CORE + EXTRA:
        try:
            name, df = fetch(s)
            print(f"{name:4s} {len(df):6d} 行  {df.index[0].date()} ~ {df.index[-1].date()}")
            ok += 1
        except Exception as e:                       # 某个代码失败不影响其他
            bad.append(s)
            print(f"{s:4s} 失败：{type(e).__name__}: {e}")
    print(f"完成：成功 {ok} 个，失败 {len(bad)} 个 {bad}")
