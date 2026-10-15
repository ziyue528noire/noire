# -*- coding: utf-8 -*-
"""全局配置：改这里就能换股票、换参数，不用动其他代码。"""

# ===== 回测标的 =====
SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT"]   # 要测试的股票/ETF
START = "2010-01-01"                       # 回测开始日期
END = None                                 # None = 到今天

# ===== 策略参数（双均线 + 趋势过滤）=====
FAST_MA = 50     # 快线天数
SLOW_MA = 200    # 慢线天数

# ===== 交易成本（一定要算！）=====
COMMISSION = 0.0005   # 每次买或卖的手续费比例（0.05%）
SLIPPAGE = 0.0005     # 滑点：实际成交价比理想价差的比例（0.05%）

# ===== 风控 =====
INITIAL_CAPITAL = 10_000   # 初始资金（美元）
MAX_POSITION_PCT = 1.0     # 单个标的最多用多少比例资金（1.0 = 100%）
STOP_LOSS_PCT = 0.10       # 持仓从买入价下跌 10% 强制止损（0 = 关闭）

# ===== 动量轮动策略（rotation.py）=====
# 每月最后一个交易日，在这些 ETF 里挑近期最强的 TOP_N 只，下个交易日开盘换仓
ROTATION_UNIVERSE = [
    "SPY",   # 美国大盘
    "QQQ",   # 纳斯达克100
    "IWM",   # 美国小盘
    "EFA",   # 发达国家（美国以外）
    "EEM",   # 新兴市场
    "TLT",   # 美国长期国债
    "GLD",   # 黄金
]
CASH_SYMBOL = "SHY"       # 所有资产都在跌时躲进短期国债（近似现金）
TOP_N = 2                 # 同时持有几只（等权）；回测里 2 只的综合表现最好
LEVERAGE = 2.0            # 杠杆倍数：1.0 = 不借钱；2.0 = 买入金额是本金的 2 倍（借一半）
                          # 每个交易日都会按最新净值把仓位调回这个倍数，亏了会自动减仓
CASH_BUFFER = 0.02       # 模拟盘留 2% 现金，避免价格跳动导致资金不足
REBALANCE_THRESHOLD = 0.05  # 实际仓位和目标相差不到总资产 5% 就不动，省手续费
LOOKBACK_MONTHS = [1, 3, 6, 12]   # 动量 = 这几个周期收益率的平均

# ===== 风控熔断（risk.py；不归策略管，优先级最高）=====
DELEVER_DRAWDOWN = 0.20   # 账户从最高点回撤超过 20% → 强制降到 1 倍杠杆
RELEVER_DRAWDOWN = 0.10   # 回撤收窄到 10% 以内 → 恢复设定的杠杆
DAILY_LOSS_LIMIT = 0.05   # 单日亏损超过 5% → 当天不再买入，并报警
ALERT_LEVERAGE = 2.3      # 实际杠杆超过 2.3 倍 → 报警
ALERT_MARGIN_DISTANCE = 0.15  # 再跌不到 15% 就会被追缴保证金 → 报警
ALERT_DRAWDOWN = 0.15     # 回撤超过 15% → 提前报警
MAINTENANCE_MARGIN = 0.30 # 估算用的维持保证金比例（券商实际数值优先）

# ===== 共享成绩单（和 ChatGPT 系统同期比较）=====
SHARED_SCOREBOARD_DIR = r"D:\scoreboard"   # 两边各写各的 CSV，互不修改
SCOREBOARD_NAME = "claude"                 # 本系统写 D:\scoreboard\claude.csv

# ===== 文件位置 =====
DATA_DIR = "data"
REPORT_DIR = "reports"
