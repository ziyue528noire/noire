# -*- coding: utf-8 -*-
"""研究用：下载完整历史日线（1993 年起，各 ETF 从上市日开始）+ 13 周国库券利率 ^IRX。
只下载数据，不交易。保存到 research/data/（不进 git）。"""
import os
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "SHY", "^IRX"]

os.makedirs(OUT, exist_ok=True)
for s in SYMBOLS:
    df = yf.download(s, start="1993-01-01", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    name = s.replace("^", "")
    df.to_csv(os.path.join(OUT, f"{name}.csv"))
    print(f"{name:4s} {len(df):6d} 行  {df.index[0].date()} ~ {df.index[-1].date()}")
