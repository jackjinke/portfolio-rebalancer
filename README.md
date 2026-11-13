# Portfolio Rebalancer

一个面向国内 A 股和 ETF 的资产再平衡计划脚本。脚本通过 AKShare 和按证券代码查询的行情源获取最新行情，根据每个品种独立配置的偏差触发条件判断是否需要调仓，并在 100 股/份整数交易约束下让整个组合尽可能贴近目标配置。

脚本只计算计划，不会自动下单。

## 安装

需要 Python 3.13 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

## 配置

组合配置 `portfolio.yaml`：

```yaml
allow_additional_funds: true

assets:
  - symbol: "510300"
    kind: etf
    target_weight: 0.50
    trigger:
      type: relative
      threshold: 0.20
  - symbol: "600519"
    kind: stock
    target_weight: 0.30
    trigger:
      type: absolute
      threshold: 0.05
```

每个品种必须单独配置 `trigger`。`relative` 按 `|实际比例 - 目标比例| / 目标比例` 判断；`absolute` 按 `|实际比例 - 目标比例|` 判断，因此阈值 `0.05` 表示相差 5 个百分点。任一品种超过自身阈值即触发整个组合再平衡，触发后阈值不再作为调仓终点。目标比例之和不足 100% 的部分视为现金。

当前持仓 `holdings.yaml`：

```yaml
cash: 0

positions:
  - symbol: "510300"
    kind: etf
    quantity: 1000
  - symbol: "600519"
    kind: stock
    quantity: 100
```

持仓数量必须是 100 的倍数。触发调仓后，计划依次最小化最大目标相对偏差、总目标相对偏差、证券交易次数、补入资金和总成交金额。`allow_additional_funds` 控制是否允许补入资金。

## 使用

输出到终端：

```bash
uv run portfolio-rebalancer \
  --portfolio portfolio.yaml \
  --holdings holdings.yaml
```

输出到 JSON 文件：

```bash
uv run portfolio-rebalancer \
  --portfolio portfolio.yaml \
  --holdings holdings.yaml \
  --output plan.json
```

结果中的 `status` 为：

- `no_rebalance`：当前组合无需调仓；
- `rebalance_required`：需要交易、补入资金，或两者之一。

两种状态的 JSON 结果都包含 `quotes`，以 `etf:512050`、`stock:600519` 等品种键索引，记录各证券的 `symbol`、`kind`、`name`、`price`、`source` 和 `as_of`。`source` 为 `eastmoney`、`tencent` 或 `sina`；`as_of` 使用行情源提供的真实时间，统一为带 `Asia/Shanghai` 时区偏移的时间，不以脚本运行时间冒充报价时间。

可将 `.venv/bin/portfolio-rebalancer` 加入 cron 等定时任务，定期生成最新计划。

股票和 ETF 行情均优先使用 AKShare 的东方财富数据源。主源请求失败、缺少代码，或该证券价格、来源时间无效时，按代码依次查询腾讯和新浪；有效的主源报价保留，仅补齐缺失或无效的证券。请求发生传输错误时最多尝试两次，间隔 0.25 秒；按代码查询的每次 HTTP 请求超时为 8 秒。无需定时任务手动切换数据源，也不使用历史缓存兜底。

若任一证券在三个来源中均无法取得有效报价，脚本以退出码 `2` 失败，诊断包含证券键及各来源的错误，不生成新计划；指定的输出文件若已存在，原内容保持不变。

## 限制

当前不考虑手续费、税费、滑点、涨跌停和 T+1，仅支持国内 A 股与 ETF。行情时间不按交易日历或最大报价年龄校验，不能据此保证行情足够新鲜，也不能完整识别停牌；新浪明确标记为非正常交易状态的报价不会使用。行情和计算结果仅供调仓决策参考。

## License

[MIT](LICENSE)
