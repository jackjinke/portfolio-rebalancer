# Portfolio Rebalancer

一个面向国内 A 股和 ETF 的资产再平衡计划脚本。脚本通过 AKShare 获取最新行情，根据每个品种独立配置的偏差触发条件判断是否需要调仓，并在 100 股/份整数交易约束下让整个组合尽可能贴近目标配置。

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

可将 `.venv/bin/portfolio-rebalancer` 加入 cron 等定时任务，定期生成最新计划。

ETF 行情优先使用 AKShare 的东方财富数据源；主源请求失败时自动改用新浪 ETF 行情，主源缺少个别代码时仅由新浪补齐，已有报价仍以主源为准。无需定时任务手动切换数据源。若两个数据源均请求失败，脚本以退出码 `2` 失败并报告两次错误，不生成新计划；缺失或无效的价格仍会报错。A 股股票行情不使用 ETF 兜底。

## 限制

当前不考虑手续费、税费、滑点、涨跌停、停牌和 T+1，仅支持国内 A 股与 ETF。行情和计算结果仅供调仓决策参考。

## License

[MIT](LICENSE)
