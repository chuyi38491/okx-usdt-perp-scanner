# OKX USDT 永续 5 分钟异动监控

## 包含内容

- `okx_usdt_5m_monitor.py`：只读轮询程序，不需要 OKX API Key，不包含下单或账户权限。
- `start_monitor.ps1`：在隐藏窗口启动持续监控。
- `install_logon_task.ps1`：创建当前 Windows 用户登录时自动启动的计划任务。任务启动后，程序每 5 分钟扫描一次。
- `supply_overrides.csv`：可选的流通量核实表；没有人工确认的数据会标为未核实。

## 启动

先确保电脑上安装了 Python 3.10 或更新版本。随后在 PowerShell 运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& .\start_monitor.ps1
```

如需每次登录 Windows 后自动启动，可运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& .\install_logon_task.ps1
```

这会创建名为 `Codex-OKX-USDT-5m-Monitor` 的当前用户计划任务。移除方式：

```powershell
Unregister-ScheduledTask -TaskName 'Codex-OKX-USDT-5m-Monitor' -Confirm:$false
```

程序需要电脑保持开机并能访问 OKX API。睡眠或断网期间不会补扫，恢复后会继续下一轮。

## 输出位置

程序在本目录下创建 `monitor_data`：

- `latest_report.md`：最新一轮完整扫描结果。
- `signals.jsonl`：满足阈值的新观察信号历史；同一币种 30 分钟内去重。
- `monitor_errors.log`：API 请求或数据读取错误。
- `state.json`：用于比较相邻扫描周期的 OI 基线和提醒冷却时间。

报告按 UTC 标注采样时间。程序本身不发送 Telegram、邮件或其他外部通知；当前配置只将信号写入本地文件和启动进程的输出流。

## 信号规则

每轮先按 24 小时成交额筛选最多 40 个流动性较高的 USDT 永续，再读取最近已收盘的 5 分钟 K 线、公开 OI 和当前资金费率。每项命中计 1 分：

1. 最新已收盘 5 分钟成交额达到前 20 根均值的 2.5 倍。
2. OI 比本程序上次成功采样增加至少 5%。
3. 当前资金费率为负。
4. 最新已收盘 5 分钟 K 线上涨。
5. 24 小时涨幅不超过 35%（超过会在风险栏标注可能已过度拉升）。

报告展示得分至少 2 分的观察项；得分至少 3 分时写入 `signals.jsonl`。阈值可在脚本顶部修改。OI 的第一次扫描没有前次基线，因此不评分 OI 变化。

## 数据限制

- 5 分钟成交额来自 OKX 已收盘 K 线；OI 变化基于本地相邻两次成功采样。
- OKX 行情 API 不提供流通量、解锁日历或项目新闻。流通量可将人工核实值填入 `supply_overrides.csv`；新闻和解锁目前会明确提示未接入核验。
- “资金费率为负”仅代表永续合约资金结算方向，不能确认空头交易者都是散户。
- 这是异动扫描器，不是收益预测模型，不保证找到或提前发现 50%–200% 行情。

## API 地区域名

默认使用 `https://www.okx.com`。如果账户所属地区要求其他 API 域名，可在启动前设置 `OKX_API_BASE`，使用 OKX 官方文档对应地区列出的域名。程序只调用公开行情接口。

接口字段按 [OKX API 官方文档](https://www.okx.com/docs-v5/en/)编写：公开 Tickers、Candlesticks、Open Interest 和 Funding Rate。OKX API 对地区使用的 API 域名有要求，使用时应按账户注册地区配置。
