# -*- coding: utf-8 -*-
"""下载并缓存美股日线数据（来源：Yahoo Finance，免费）。"""
import os
import pandas as pd
import config


def load_prices(symbol: str, refresh: bool = False) -> pd.DataFrame:
    """返回包含 Open/High/Low/Close/Volume 的日线 DataFrame（已复权）。
    第一次会联网下载并存到 data/ 文件夹，之后直接读本地缓存。"""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    path = os.path.join(config.DATA_DIR, f"{symbol}.csv")

    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        import yfinance as yf
        print(f"下载 {symbol} ...")
        df = yf.download(symbol, start=config.START, end=config.END,
                         auto_adjust=True, progress=False)
        if df.empty:
            raise RuntimeError(f"{symbol} 没有下载到数据，检查代码或网络")
        if isinstance(df.columns, pd.MultiIndex):   # 新版 yfinance 返回多级列
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df.to_csv(path)

    df = df.loc[config.START:]
    if config.END:
        df = df.loc[:config.END]
    return df.dropna()
