# -*- coding: utf-8 -*-
"""下载并缓存美股日线数据（来源：Yahoo Finance，免费）。

2026-10-16 加固：
  - Yahoo 偶尔限流或临时出错 → 同一次运行里自动再试 2 次（先等 20 秒、再等 60 秒）
  - 新下载的数据明显比本地缓存少（开头晚了很多、少了很多天、或者最新日期倒退）→ 当作下载失败，
    不覆盖本地缓存（否则动量会因为“历史不够”而全部换成现金）
实盘准备（2026-10-18）：模拟盘和实盘共用 data\\ 缓存 → 写缓存时先写临时文件再整体替换，
    两边前后脚运行时也不会读到写了一半的文件。
"""
import os
import time
import pandas as pd
import config

DOWNLOAD_TRIES = 3
DOWNLOAD_WAITS = (20, 60)       # 第 2、3 次之前各等多少秒


def _problem(new: pd.DataFrame, old: pd.DataFrame):
    """新数据有没有明显缺东西（和本地缓存比）。没问题返回 None。"""
    if new is None or new.empty:
        return "没有返回数据"
    if old is None or old.empty:
        return None
    if new.index[0] > old.index[0] + pd.Timedelta(days=10):
        return f"开头从 {old.index[0].date()} 变成了 {new.index[0].date()}"
    if new.index[-1] < old.index[-1]:
        return f"最新日期从 {old.index[-1].date()} 倒退到 {new.index[-1].date()}"
    if len(new.loc[:old.index[-1]]) < len(old) - 5:
        return f"比本地缓存少了 {len(old) - len(new.loc[:old.index[-1]])} 天"
    return None


def _download(symbol: str, old: pd.DataFrame = None) -> pd.DataFrame:
    import yfinance as yf
    why = None
    for i in range(DOWNLOAD_TRIES):
        try:
            df = yf.download(symbol, start=config.START, end=config.END, auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):   # 新版 yfinance 返回多级列
                    df.columns = df.columns.get_level_values(0)
                df = df[["Open", "High", "Low", "Close", "Volume"]]
            why = _problem(df, old)
            if why is None:
                return df
        except Exception as e:
            why = str(e)[:120]
        if i < DOWNLOAD_TRIES - 1:
            wait = DOWNLOAD_WAITS[min(i, len(DOWNLOAD_WAITS) - 1)]
            print(f"  {symbol} 这次没下载好，{wait} 秒后再试（第 {i + 2}/{DOWNLOAD_TRIES} 次）")
            time.sleep(wait)
    raise RuntimeError(f"{symbol} 下载 {DOWNLOAD_TRIES} 次都没成功（{why}），本地缓存没有改动")


def _save(df: pd.DataFrame, path: str, tries: int = 5):
    """先写临时文件再整体替换：别的程序（例如模拟盘和实盘的换仓前后脚运行）任何时候读到的都是完整文件。
    Windows 上目标文件正被别的程序读的那一瞬间替换会失败 → 等 1 秒再试。"""
    tmp = f"{path}.{os.getpid()}.tmp"
    df.to_csv(tmp)
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(1)


def load_prices(symbol: str, refresh: bool = False) -> pd.DataFrame:
    """返回包含 Open/High/Low/Close/Volume 的日线 DataFrame（已复权）。
    第一次会联网下载并存到 data/ 文件夹，之后直接读本地缓存；refresh=True 时重新下载。"""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    path = os.path.join(config.DATA_DIR, f"{symbol}.csv")

    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        old = None
        if os.path.exists(path):
            try:
                old = pd.read_csv(path, index_col=0, parse_dates=True).loc[config.START:].dropna()
            except Exception:
                old = None
        print(f"下载 {symbol} ...")
        df = _download(symbol, old)
        _save(df, path)

    df = df.loc[config.START:]
    if config.END:
        df = df.loc[:config.END]
    return df.dropna()
