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
# 在这些 ETF 里挑近期最强的（每批 TOP_N 只），分 4 批换仓，收盘出信号、下个交易日开盘成交（见下面 A+ 稳健版）
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
TOP_N = 2                 # 每一批持有几只
LOOKBACK_MONTHS = [1, 3, 6, 12]   # 动量 = 这几个周期收益率的平均

# ===== A+ 稳健版（aplus.py；2026-10-15 用户选定，依据 research/report_v2.md）=====
STRATEGY_NAME = "A+ 稳健版"
TRANCHE_OFFSETS = [0, 5, 10, 15]  # 分 4 批换仓：月末、月末前第 5/10/15 个交易日，各管 1/4 资金
WEIGHTING = "invvol"      # 每批选中的两只按“近 63 天波动的倒数”分配（波动小的多买）；"equal" = 平均分
VOL_WINDOW = 63           # 计算波动用的交易日数（约 3 个月）
VOL_TARGET = 0.30         # 波动目标：杠杆 = 30% ÷ 组合近 63 天的年化波动
LEVERAGE = 2.0            # 杠杆上限（券商隔夜最多 2 倍）
LEV_FLOOR = 1.0           # 杠杆下限
TREND_SYMBOL = "SPY"      # 趋势开关：SPY 收盘在 200 日均线下方时，杠杆最多 1 倍
TREND_DAYS = 200
# ===== 第二条腿：RSI(2) 超跌反弹（dipbuy.py；2026-10-16 用户同意启用，依据 research/report_v3.md）=====
DIP_ENABLED = True        # False = 关掉第二条腿（A+ 稳健版照常）
DIP_SYMBOL = "SPY"
DIP_TREND_DAYS = 200      # 收盘在 200 日均线上方才买
DIP_RSI_ENTRY = 10        # 2 日 RSI 低于 10 才买
DIP_EXIT_DAYS = 5         # 收盘高于 5 日均线就卖
DIP_MAX = 1.0             # 最多用 1 倍本金，而且只用 A+ 没用完的额度（总仓位不超过 LEVERAGE）
if DIP_ENABLED:
    STRATEGY_NAME = STRATEGY_NAME + " + 超跌反弹"     # 报告和成绩单里显示的名字
CASH_BUFFER = 0.02       # 模拟盘留 2% 现金，避免价格跳动导致资金不足
REBALANCE_THRESHOLD = 0.05  # 实际仓位和目标相差不到总资产 5% 就不动，省手续费

# ===== 风控硬规则（risk.py；不归策略管，优先级最高）=====
# 原来的“回撤 >20% 降到 1 倍”熔断已取消：研究发现它总在低点附近降杠杆、之后长期卡在 1 倍，
# 历史上反而比不加杠杆还差。现在由上面的波动目标 + 趋势开关提前控制杠杆。
DAILY_LOSS_LIMIT = 0.05   # 单日亏损超过 5% → 当天不再买入，并报警
ALERT_LEVERAGE = 2.3      # 实际杠杆超过 2.3 倍 → 报警
ALERT_MARGIN_DISTANCE = 0.15  # 再跌不到 15% 就会被追缴保证金 → 报警
ALERT_DRAWDOWN = 0.15     # 回撤超过 15% → 报警（只提醒，不自动改杠杆）
MAINTENANCE_MARGIN = 0.30 # 估算用的维持保证金比例（券商实际数值优先）

# ===== 共享成绩单（和 ChatGPT 系统同期比较）=====
SHARED_SCOREBOARD_DIR = r"D:\scoreboard"   # 两边各写各的 CSV，互不修改
SCOREBOARD_NAME = "claude"                 # 本系统写 D:\scoreboard\claude.csv

# ===== 实盘（真钱）：只在实盘脚本里用（NOIRE_PROFILE=live）；默认关闭 =====
# 真钱下单必须同时满足三条：LIVE_ENABLED = True、用户自己双击 enable_live.bat、LIVE_MAX_CAPITAL > 0。
# 实盘的记录全部在 live\ 子文件夹；模拟盘照常运行，互不影响。
LIVE_ENABLED = False       # 总开关
LIVE_MAX_CAPITAL = 0       # 实盘最多管理多少美元（0 = 不交易）；账户里超出的钱不动
LIVE_MAX_LEVERAGE = 1.0    # 实盘杠杆上限：1.0 = 不借钱；1.5；2.0 = 和模拟盘一样（不能超过上面的 LEVERAGE）
LIVE_FRACTIONAL = True     # 实盘按金额下单（可以买零股，小金额也能按比例配仓）
LIVE_MAX_ORDER = 1.1       # 单笔订单金额上限 = 管理资金 × 杠杆上限 × 1.1（防程序出错下超大单）
LIVE_MIN_ORDER = 5.0       # 小于 5 美元的零碎订单不下

# ===== 文件位置 =====
DATA_DIR = "data"
REPORT_DIR = "reports"
