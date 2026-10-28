# OKX USDT 永续合约异动扫描器

这是一个**只读**扫描器：使用 OKX 公开行情，不需要 API Key，不会读取账户、下单或自动交易。扫描时间为每小时 UTC 第 7 分钟和第 37 分钟，对应拉各斯时间 08:07、08:37 等。GitHub 定时任务可能延迟或漏跑。

## 扫描规则

- 获取 OKX 全部 SWAP tickers，筛出 USDT 永续，按近 24 小时估算成交额排序。
- 对成交额较高的前 80 个读取 30 分钟 K 线，和此前最多 20 根已完成 K 线比较。
- 30 分钟成交额达到基线 4 倍，或达到 3 倍且价格上涨至少 1.5%，列入异动报告。
- 读取 OKX 当前公开 OI 并比较最近两次成功扫描，变化达到 ±5% 时标注。
- 仅对成交量/OI 触发项读取资金费率。
- 流通量、解锁和项目新闻不由行情接口提供，报告会注明未核实；不把这些指标猜成事实。

## 部署到 GitHub

1. 创建一个 **Public** 仓库，例如 `okx-usdt-perp-scanner`。标准 GitHub-hosted runner 在公开仓库免费；仓库代码是公开的。这里没有密钥或个人数据。
2. 将 ZIP 解压后，把里面的全部文件和 `.github` 隐藏目录上传到仓库根目录。最终应有：

   ```text
   .github/workflows/okx-scan.yml
   scanner/okx_scanner.py
   scanner/README.md
   ```

3. 在仓库的 **Actions** 页面启用工作流。
4. 打开 **OKX USDT Perpetual Scanner**，选择 **Run workflow** 做首次手动运行。
5. 打开运行记录里的 `okx-anomaly-report` artifact 下载报告。后续计划任务每次运行也会保存报告 7 天。

首次成功扫描没有 OI 前次快照；从第二次成功运行开始，才可比较最近两次扫描的 OI。快照通过 GitHub Actions cache 保存，cache 可能被 GitHub 清理，因此较长中断后 OI 基线会重置。

## 手机上传提示

ZIP 里包含 `.github` 隐藏目录。如果 iPhone 文件选择器没有显示它，不要只上传 `scanner` 文件夹，否则 GitHub 找不到定时工作流。可以改用电脑上传，或告诉我你在 GitHub 页面看到什么，我再按页面带你操作。

## 运行限制

- 公开仓库上的标准 GitHub-hosted runner 使用免费；公开代码对所有人可见。
- GitHub 的计划任务只在默认分支运行。高峰期可能延迟，公开仓库 60 天没有仓库活动时计划工作流可能被自动禁用。
- 成交额采用 OKX `volCcy24h × last` 估算 USDT，不等同于官方 quote volume 字段。
- OI 对比的是最近两次扫描，不是精确的 24 小时变化；缓存丢失或任务漏跑会改变基线。
- 这是异动观察工具，不是收益预测，也不会预测 50%–200% 涨幅。报告和 artifact 是 GitHub 上的独立渠道，不会自动发送到 ChatGPT 手机通知。

## 本地手动运行（可选）

需要 Python 3.10+ 和可访问 OKX API：

```bash
python scanner/okx_scanner.py
```

报告写入 `scanner/reports/`。
