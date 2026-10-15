# Noire · 美股自动交易系统

> 原名 Excaliber，2026-10-15 改名为 Noire（法语“黑色”，取 “in the black” 盈利之意），以和 ChatGPT 的 Excalibur 区分。

目标路线：**回测 → 模拟盘 → 小资金实盘 → 加入 AI**。当前处于第 3 阶段（模拟盘）。

## 一、第一次运行（Windows）

1. 安装 Python 3.10 以上版本：https://www.python.org/downloads/ （安装时勾选 **Add Python to PATH**）
2. 打开命令提示符（Win+R → 输入 `cmd`），然后运行：
   ```
   cd /d D:\claude\trader
   pip install -r requirements.txt
   python backtest.py
   ```
3. 结果会打印在屏幕上，图表和表格保存在 `reports\` 文件夹。

以后直接双击 `run_backtest.bat` 就能运行。

## 二、文件说明

| 文件 | 作用 |
|---|---|
| `config.py` | 所有参数：股票、日期、均线天数、手续费、止损 |
| `data.py` | 从 Yahoo Finance 下载日线数据，缓存到 `data\` |
| `strategy.py` | 策略（当前是 50/200 日双均线），想换策略就改这里 |
| `backtest.py` | 回测引擎：扣成本、次日开盘成交、止损、和买入持有对比 |

## 三、怎么看结果

- **年化收益**：每年平均赚多少
- **夏普比率**：每承担一份风险能换来多少收益，大于 1 算不错，小于 0.5 一般
- **最大回撤**：从最高点最多跌了多少，这个数决定你拿不拿得住
- **一定要和“买入持有”比**：跑不赢一直拿着 SPY 不动的策略，就没必要做

双均线策略在大牛市里通常跑不赢买入持有。它的价值在于大跌时（比如 2008、2022 年）把回撤压小，这是一个学习用的基准，不是赚钱秘籍。

## 四、自我检查清单（防止骗自己）

- [ ] 参数是不是试了很多组，最后挑了最好看的那组？（过拟合）
- [ ] 换别的股票、别的年份，还有效吗？
- [ ] 把手续费和滑点调高一倍，还赚钱吗？
- [ ] 最大回撤 -30% 的时候，你真的不会手动停掉吗？

## 五、下一步

1. 改 `config.py` 里的参数和股票，观察结果怎么变
2. 在 `strategy.py` 里写第二个策略（比如动量、RSI 均值回归）对比
3. 回测满意后，注册 Alpaca 模拟账户，运行 `paper_rotation.py`（已接通），至少跑 3–6 个月
4. 之后再考虑机器学习、新闻情绪等 AI 信号

## 六、模拟盘（已接通）

| 文件 | 作用 |
|---|---|
| `set_keys.bat` | 设置 Alpaca 模拟盘密钥（保存在本文件夹 `.env`，不要发给任何人）|
| `paper_dryrun.bat` | 动量轮动策略空跑：只显示打算怎么买卖，不下单 |
| `run_paper.bat` | 动量轮动策略正式运行（只连模拟盘），由计划任务每个交易日 17:00 调用 |
| `install_schedule.bat` / `uninstall_schedule.bat` | 安装 / 删除 Windows 计划任务 |
| `trade_status.bat` | 查看账户、持仓、盈亏、订单 → `orders\status.txt` |
| `trade_place.bat` | 执行 `orders\order.json` 里的手动订单 → `orders\result.txt` |

规则（写死在代码里）：只连模拟盘；不借钱；不做空；订单文件执行一次后自动移走。
动量轮动只管理自己的 8 只 ETF（SPY QQQ IWM EFA EEM TLT GLD SHY）；手动买的其他股票它不会动。
注意：如果手动买卖这 8 只 ETF，下次自动换仓时会被调回策略的目标仓位。

## 七、自动运行与安全网

| 自动任务 | 时间（美东） | 做什么 |
|---|---|---|
| Noire Paper Rotation（Windows 计划任务） | 周一至周五 17:00 | 自动快照代码 → 动量轮动换仓（2 倍杠杆，模拟盘）→ 刷新账户状态 |
| Noire Monitor（Windows 计划任务） | 周一至周五 9:45–15:45 每小时 | 只检查不交易；有异常弹 Windows 通知并写入 `alerts.txt` |
| Noire 收盘日报（Claude 定时任务） | 周一至周五约 17:15 | 读日志写日报到 `reports\daily\`；只提建议，不改代码、不下单 |

**风控熔断**（`risk.py`，参数在 `config.py`）：回撤超过 20% 自动降到 1 倍杠杆（回到 10% 以内恢复）；单日亏损超过 5% 当天只卖不买。

**紧急停止**：双击 `emergency_stop.bat`（10 秒内关掉窗口可取消）→ 禁止交易、停用自动换仓、撤单、清仓。
恢复：双击 `resume_trading.bat`。

**版本历史**：`versions\` 里保存每一版代码，每天自动换仓前自动快照。
`python versions.py list` 查看，`python versions.py restore 版本号` 退回。

**注意**：电脑要开着、不能睡眠，计划任务才会运行（设置 → 系统 → 电源 → 睡眠：从不）。

## 八、工程加固（2026-10-15，借鉴 ChatGPT 版的做法）

- **订单唯一编号 + 台账**（`ledger.py`，`logs\order_ledger.jsonl`）：每笔订单编号 `noire-rot-日期-标的-方向`（手动单 `noire-man-...`）。
  先记意图再提交；同编号券商会拒绝 → 重复运行也不会重复下单；提交结果不明 → 立即停止、报警、绝不重试，下次运行先向券商核实。
- **和券商逐笔核对**（`monitor.py`）：结果不明的订单、不是本系统下的订单、已成交但台账没记录的订单、不明来源的持仓，都会报警。
- **安全测试**（`tests\`，19 项，用假券商、临时目录，完全离线）：双击 `run_tests.bat`。
  **每天 17:00 换仓前自动先跑一遍（`preflight.py`），任何一项不通过当天就不交易并报警。**
- **git**：`setup_phase2.bat` 建立本地仓库；每天换仓前自动提交一次。密钥、数据、日志不进 git。
- **成绩单**（`scoreboard.py`）：每天 17:00 在 `equity_history.csv` 记一行净值，和同期 SPY 比较 → `reports\scoreboard.txt`。
  其他系统按同样格式（`date,equity` 两列的 CSV）放进 `scoreboard\` 文件夹就会一起比较。
- 修复：账户第一天券商没有“昨日净值”时，“今日盈亏”改用当天首次记录的净值计算。
