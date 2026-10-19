# Noire —— 给 Claude Code 的项目说明（每次会话开始自动读取）

Noire（原名 Excaliber）是用户的美股自动交易系统（**模拟盘**每天运行；**实盘模式**已写好、默认关闭，用户计划 11 月小额尝试）：动量轮动策略“**A+ 稳健版**”（2026-10-15 用户选定，`aplus.py`）。
7 只 ETF（SPY QQQ IWM EFA EEM TLT GLD）；资金分 4 批，各在月末 / 月末前第 5、10、15 个交易日按 1/3/6/12 月收益均值选前 2 名，
两只按波动倒数分配，分数 ≤0 的份额换成 SHY；杠杆 = 30% ÷ 组合近 63 天波动（1~2 倍），SPY 低于 200 日均线时最多 1 倍；不借钱买 SHY。
**第二条腿**（`dipbuy.py`，2026-10-16 用户同意启用）：SPY 收盘在 200 日线上方且 2 日 RSI < 10 → 买 SPY，收盘高于 5 日均线 → 卖；
只用 A+ 没用完的额度（最多 1 倍，总仓位 ≤ 2 倍）。开关是 config.py 的 `DIP_ENABLED`。
完整现状、历史决策和变更记录见 claude.ai 项目 “Noire” 里的 `noire-system.md`，以及本文件夹的 README.md。

## 绝对不能做的事
- **连接券商只经过 `broker.py`**：模拟盘 `paper=True`；实盘（真钱）只在实盘脚本里（环境变量 `NOIRE_PROFILE=live`），而且要三道闸都开才允许下单：`config.LIVE_ENABLED = True`、**用户本人**双击 `enable_live.bat` 输入 YES（`live\LIVE_ENABLED`）、`LIVE_MAX_CAPITAL > 0`。
- **真钱的事 Claude 绝不代劳**：不替用户输入、读取、打印实盘密钥（`.env.live`）；不运行 `enable_live.bat`、`run_live.bat`、`liquidate_live.bat`，不在实盘账户上下任何订单（包括手动单；`trade.py place` 在实盘已禁止）；只读的 `live_dryrun.bat`、`live_status.bat` 也要用户同意后才运行；不削弱三道闸、只读连接、资金上限 `LIVE_MAX_CAPITAL`、杠杆上限 `LIVE_MAX_LEVERAGE`、单笔上限 `LIVE_MAX_ORDER`；**改实盘的金额、杠杆或策略必须先得到用户同意**。安装实盘计划任务（`install_live_schedule.bat`）也要用户同意或由用户自己双击。
- **不读取、不打印、不提交** `.env`、`.env.live`（Alpaca 密钥）或任何密钥。
- **只改 D:\claude 里的东西。** `D:\gpt` 是 ChatGPT 的项目（Excalibur），不读不写；它的计划任务名以 Excalibur 开头，不碰。
- `D:\scoreboard` 是共享成绩单：本系统只写 `claude.csv`（由 scoreboard.py 写），绝不改 `gpt.csv`。
- 不永久删除用户文件；需要删除时移到回收站或 `_to_delete\`。
- 不能削弱这些安全规则：不借钱（手动单）、不做空（策略卖单也不超过持有股数）、订单唯一编号、结果不明不重试（券商明确拒绝 HTTP 400/403/422 才算“没下成”）、STOP 文件、杠杆上限 2 倍（由波动目标 + 趋势开关控制；加上第二条腿后总仓位也不超过 2 倍）、单日亏损限制、价格异常（缺失/≤0/单日涨跌超 25%）不交易。
- 测试不能依赖当天日期或随机数（每天 17:00 自检，任何一项失败当天就不交易；2026-10-16 修过一次这样的“定时炸弹”）。
- 实盘的安全测试在 `tests\test_live.py`（每个场景在单独进程里用 `NOIRE_PROFILE=live` + 临时目录 + 假券商）；改实盘相关代码时，用“故意改坏一处、看测试能不能抓到”的办法确认保护真的有测试覆盖。

## 改代码的流程
1. 改之前先看相关测试（`tests\test_safety.py`）。
2. 改完必须跑：`python -m unittest discover -s tests`（全部通过才算完成；测试离线、用假券商和临时目录）。
3. 需要验证真实接口时只用空跑：`python paper_rotation.py`（不带 `--live`，结果写到 `reports\paper_status_dryrun.txt`）或 `python monitor.py`（只读）。
4. 用 git 提交（作者 “Noire Bot”），提交信息写清楚改了什么、为什么。
5. 更新 README.md；重要变化告诉用户，以便更新项目文档 `noire-system.md` 的变更记录。

## 运行方式（已经自动化，一般不需要手动跑）
- Windows 计划任务 `Noire Paper Rotation`：周一至五 17:00 / 17:30 / 18:00 → `run_paper.bat` → `paper_job.py`（成功一次后跳过；第 3 次仍失败才报警）。
- Windows 计划任务 `Noire Monitor`：周一至五 9:45–15:45 每小时 → `monitor.py`（只读监控 + 与券商核对）。
- 实盘（装上以后）：`Noire Live Rotation` 周一至五 17:10 / 17:40 / 18:10 → `run_live.bat`；`Noire Live Monitor` 9:47–15:47 每小时 → `run_live_monitor.bat`。记录全部在 `live\` 子文件夹（警报 `live\alerts.txt`，状态 `live\reports\paper_status.txt`）。
- Claude 定时任务 “Noire 收盘日报”（17:16）和 “Noire 盘中警报”（9:52–15:52）读本文件夹的日志，推送到用户手机。
- 改动计划任务的名字或路径时，要同时告诉用户：那两个 Claude 定时任务里写着 `D:\claude\Noire` 的路径。

## 关键文件
| 文件 | 作用 |
|---|---|
| config.py | 全部参数（标的、杠杆、风控阈值、共享成绩单位置、实盘 `LIVE_*`） |
| runmode.py / broker.py | 模拟盘还是实盘（`NOIRE_PROFILE`）；连接券商的唯一入口（实盘三道闸 + 只读连接） |
| aplus.py | **实盘策略**：4 批信号、波动倒数分配、杠杆（波动目标 + 趋势开关）——改它必须同步 research/research_v2.py 并跑测试 |
| dipbuy.py | **第二条腿**：RSI(2) 超跌反弹，只用 A+ 剩余额度——改它必须同步 research/research_v3.py 并跑测试 |
| fills.py / tracking.py | 成交价记录（monitor 每小时写 logs/fills.csv）；实盘 vs 模型对账 → reports/tracking.txt（只读） |
| rotation.py / backtest.py | 旧的简单回测（rotation.py 的 load_panel 仍被实盘使用） |
| paper_rotation.py | 每日换仓（返回 ok / stopped / blocked） |
| paper_job.py | 有限次数重试 + 运行锁 |
| risk.py / ledger.py / alerts.py / paths.py | STOP、单日亏损、回撤记录；订单台账；报警；运行文件路径（测试用 EXC_RUNTIME_DIR 指向临时目录） |
| monitor.py / trade.py / emergency.py | 监控核对、手动下单与状态、紧急停止 |
| scoreboard.py | 成绩单（与 SPY、ChatGPT 系统同期比较） |
| preflight.py | 换仓前自检（跑全部测试） |

## 下一步（路线图）
研究引擎 v1/v2/v3 已完成（`research\report.md`、`report_v2.md`、`report_v3.md`）：现行“2 倍+熔断”历史上不如 1 倍；用户选定“A+ 稳健版”，2026-10-15 上线（aplus.py；回撤熔断取消）；
2026-10-16 用户同意启用第二条腿 RSI(2)（dipbuy.py，上线前已逐日核对和研究一致）。以后改策略参数仍须用户同意。研究待办见 `research\backlog.md`。
之后：策略排行榜（与 SPY 同期比较）；行情改用 Alpaca 数据；不要再继续调参数（试得越多越容易被碰巧的结果骗）。
新闻/大模型判断涨跌：目前不用于交易（无法可靠回测）；可以作为日报里的信息。
实盘：用户 10-17 决定 11 月小额尝试（模拟盘继续当对照组）；上线步骤和检查清单见 README 第十五节。

## 沟通
用户说中文，是编程新手：解释要通俗，先说结论；需要用户决定的事情先问，其余直接做完再简短汇报。
