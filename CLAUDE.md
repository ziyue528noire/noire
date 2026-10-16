# Noire —— 给 Claude Code 的项目说明（每次会话开始自动读取）

Noire（原名 Excaliber）是用户的美股**模拟盘**自动交易系统：动量轮动策略“**A+ 稳健版**”（2026-10-15 用户选定，`aplus.py`）。
7 只 ETF（SPY QQQ IWM EFA EEM TLT GLD）；资金分 4 批，各在月末 / 月末前第 5、10、15 个交易日按 1/3/6/12 月收益均值选前 2 名，
两只按波动倒数分配，分数 ≤0 的份额换成 SHY；杠杆 = 30% ÷ 组合近 63 天波动（1~2 倍），SPY 低于 200 日均线时最多 1 倍；不借钱买 SHY。
完整现状、历史决策和变更记录见 claude.ai 项目 “Noire” 里的 `noire-system.md`，以及本文件夹的 README.md。

## 绝对不能做的事
- **只连 Alpaca 模拟盘**（`paper=True` 写死）。不得加入任何实盘开关或实盘地址；上实盘由用户本人另行决定。
- **不读取、不打印、不提交** `.env`（Alpaca 密钥）或任何密钥。
- **只改 D:\claude 里的东西。** `D:\gpt` 是 ChatGPT 的项目（Excalibur），不读不写；它的计划任务名以 Excalibur 开头，不碰。
- `D:\scoreboard` 是共享成绩单：本系统只写 `claude.csv`（由 scoreboard.py 写），绝不改 `gpt.csv`。
- 不永久删除用户文件；需要删除时移到回收站或 `_to_delete\`。
- 不能削弱这些安全规则：不借钱（手动单）、不做空、订单唯一编号、结果不明不重试、STOP 文件、杠杆上限 2 倍（由波动目标 + 趋势开关控制）、单日亏损限制。

## 改代码的流程
1. 改之前先看相关测试（`tests\test_safety.py`）。
2. 改完必须跑：`python -m unittest discover -s tests`（全部通过才算完成；测试离线、用假券商和临时目录）。
3. 需要验证真实接口时只用空跑：`python paper_rotation.py`（不带 `--live`）或 `python monitor.py`（只读）。
4. 用 git 提交（作者 “Noire Bot”），提交信息写清楚改了什么、为什么。
5. 更新 README.md；重要变化告诉用户，以便更新项目文档 `noire-system.md` 的变更记录。

## 运行方式（已经自动化，一般不需要手动跑）
- Windows 计划任务 `Noire Paper Rotation`：周一至五 17:00 / 17:30 / 18:00 → `run_paper.bat` → `paper_job.py`（成功一次后跳过；第 3 次仍失败才报警）。
- Windows 计划任务 `Noire Monitor`：周一至五 9:45–15:45 每小时 → `monitor.py`（只读监控 + 与券商核对）。
- Claude 定时任务 “Noire 收盘日报”（17:16）和 “Noire 盘中警报”（9:52–15:52）读本文件夹的日志，推送到用户手机。
- 改动计划任务的名字或路径时，要同时告诉用户：那两个 Claude 定时任务里写着 `D:\claude\Noire` 的路径。

## 关键文件
| 文件 | 作用 |
|---|---|
| config.py | 全部参数（标的、杠杆、风控阈值、共享成绩单位置） |
| aplus.py | **实盘策略**：4 批信号、波动倒数分配、杠杆（波动目标 + 趋势开关）——改它必须同步 research/research_v2.py 并跑测试 |
| rotation.py / backtest.py | 旧的简单回测（rotation.py 的 load_panel 仍被实盘使用） |
| paper_rotation.py | 每日换仓（返回 ok / stopped / blocked） |
| paper_job.py | 有限次数重试 + 运行锁 |
| risk.py / ledger.py / alerts.py / paths.py | STOP、单日亏损、回撤记录；订单台账；报警；运行文件路径（测试用 EXC_RUNTIME_DIR 指向临时目录） |
| monitor.py / trade.py / emergency.py | 监控核对、手动下单与状态、紧急停止 |
| scoreboard.py | 成绩单（与 SPY、ChatGPT 系统同期比较） |
| preflight.py | 换仓前自检（跑全部测试） |

## 下一步（路线图）
研究引擎 v1/v2 已完成（`research\report.md`、`research\report_v2.md`）：现行“2 倍+熔断”历史上不如 1 倍；用户选定“A+ 稳健版”，2026-10-15 上线（aplus.py；回撤熔断取消）。以后改策略参数仍须用户同意。
之后：策略排行榜（与 SPY 同期比较）；行情改用 Alpaca 数据；不要再继续调参数（试得越多越容易被碰巧的结果骗）。
新闻/大模型判断涨跌：目前不用于交易（无法可靠回测）；可以作为日报里的信息。
上实盘前需模拟盘稳定 3–6 个月，由用户决定。

## 沟通
用户说中文，是编程新手：解释要通俗，先说结论；需要用户决定的事情先问，其余直接做完再简短汇报。
