# Portfolio Rebalancer

一个面向国内 A 股和 ETF 的资产再平衡计划脚本。脚本通过 AKShare 获取最新行情，根据目标比例和相对偏差阈值判断是否需要调仓，并在 100 股/份整数交易约束下生成交易次数最少的调仓计划。

脚本只计算计划，不会自动下单。

## 安装

需要 Python 3.13 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

## 配置

组合配置 `portfolio.yaml`：

```yaml
strategy:
  type: relative_deviation
  threshold: 0.10

allow_additional_funds: true

assets:
  - symbol: "510300"
    kind: etf
    target_weight: 0.50
  - symbol: "600519"
    kind: stock
    target_weight: 0.30
```

目标比例之和不足 100% 的部分视为现金。`threshold: 0.10` 表示相对目标比例偏差超过 10% 时触发再平衡。

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

持仓数量必须是 100 的倍数。`allow_additional_funds` 为 `true` 时，计划可以通过补入资金减少证券交易次数。

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

## 限制

当前不考虑手续费、税费、滑点、涨跌停、停牌和 T+1，仅支持国内 A 股与 ETF。行情和计算结果仅供调仓决策参考。

## License

[MIT](LICENSE)
