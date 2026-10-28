# OKX USDT 永续自选日报（每日 09:00 阿尔及尔时间）

本包包含：

- `okx_daily_watchlist.py`：读取 OKX 公开行情，生成中文日报并通过 PushPlus 微信公众号渠道转发。
- 仓库根目录工作流：`.github/workflows/okx-daily-wechat.yml`，每天 `Africa/Algiers` 09:00 运行，也可在 Actions 手动运行。

程序只读取公开市场数据，不需要 OKX API Key，不含下单功能。

## 报告覆盖内容

- 自选 USDT 永续现价、24 小时涨跌和估算成交额。
- 当前 OI 与可用时的 24 小时历史变化、资金费率及下次结算时间。
- 最近 7/30 根已收盘日线区间、ATR14、现货对应交易对是否有成交。
- 条件式回踩/突破观察区和失效参考，不构成买卖指令。
- 流通量、解锁、项目催化在尚未接入可靠自动核验源前明确显示“未核实”，不会猜填。
- 成功运行后，报告也会以 GitHub Actions Artifact 保存 14 天。

## 上传到现有仓库

保留以下仓库相对路径：

1. 把 `okx_daily_watchlist.py` 上传到 `outputs/daily_report_package/okx_daily_watchlist.py`。
2. 把工作流文件上传到仓库根目录 `.github/workflows/okx-daily-wechat.yml`。GitHub 只会从这个根目录位置识别 Actions 工作流。

此目录中的 `.github/workflows/okx-daily-wechat.yml` 是对应文件的副本，方便一起打包；实际部署时必须放到仓库根目录 `.github/workflows/`。

## 在 GitHub 添加两个 Actions Secrets

在仓库页面进入 **Settings → Secrets and variables → Actions → New repository secret**，分别创建：

### `PUSHPLUS_TOKEN`

到 PushPlus 官方网站登录，完成微信渠道绑定及账户要求后，在个人中心获取 Token。把 Token 直接粘贴进 GitHub Secret 表单；不要把它发到聊天、提交到代码或截图公开。PushPlus 接口成功受理不等于最终微信送达，首次运行后还要检查 PushPlus 消息记录及微信绑定状态。

### `OKX_WATCHLIST`

值设为以下逗号分隔代码（只保存为 Secret，不公开到仓库）：

```text
BTCUSDT,ETHUSDT,ARUSDT,IRYSUSDT,LABUSDT,HYPEUSDT,ZECUSDT,BNBUSDT,SOLUSDT,APTUSDT,TRUMPUSDT,DOGEUSDT
```

## 启用与查看

文件提交到默认分支且两个 Secrets 配好后，进入仓库 **Actions → OKX Daily Watchlist WeChat Report**。可以点 **Run workflow** 做首次手动运行。绿色完成后，在运行页底部 **Artifacts** 下载 `okx-daily-watchlist-report` 查看 Markdown 报告；同时检查 PushPlus 是否显示发送成功并在微信收到消息。

若运行失败，先打开运行记录看 `Generate and send report` 步骤。常见原因是 Secret 名称/值不正确、PushPlus 尚未绑定微信、公开 API 临时不可用，或所选合约已不在 OKX 交易。

## 数据与风险限制

- 估算成交额按 OKX 衍生品 `volCcy24h × 最新价` 计算；不应当作精确撮合成交额。
- OI 历史端点如果不给足两个有效点，日报显示“未核实”。
- 现货是否存在成交不等于现货方向确认。此版未抓取现货K线。
- 本版未自动核验流通量、代币解锁和项目新闻/公告；报告会明确标注缺失。
- 行情区间是历史技术参考，不预测收益；定时任务、微信送达也可能延迟或失败。

## 官方资料

- OKX API 文档：<https://www.okx.com/docs-v5/en/>
- PushPlus 消息接口文档：<https://pushplus.plus/doc/guide/api.html>
- GitHub Actions 定时工作流说明：<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>
