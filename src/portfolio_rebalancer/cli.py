from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from portfolio_rebalancer.config import ConfigError, load_holdings, load_portfolio
from portfolio_rebalancer.domain import Allocation, RebalancePlan, Trade
from portfolio_rebalancer.market_data import MarketDataError, fetch_quotes
from portfolio_rebalancer.planner import PlannerError, plan


def _number(value: Decimal) -> str:
    return str(value)


def _allocation_dict(allocation: Allocation) -> dict[str, Any]:
    return {
        "key": allocation.key,
        "name": allocation.name,
        "quantity": allocation.quantity,
        "value": _number(allocation.value),
        "target_weight": _number(allocation.target_weight),
        "actual_weight": _number(allocation.actual_weight),
        "relative_deviation": (
            _number(allocation.relative_deviation)
            if allocation.relative_deviation is not None
            else None
        ),
    }


def _trade_dict(trade: Trade) -> dict[str, Any]:
    return {
        "side": trade.side.value,
        "symbol": trade.instrument.symbol,
        "kind": trade.instrument.kind.value,
        "name": trade.name,
        "quantity": trade.quantity,
        "price": _number(trade.price),
        "estimated_amount": _number(trade.estimated_amount),
    }


def plan_dict(result: RebalancePlan) -> dict[str, Any]:
    return {
        "status": result.status,
        "as_of": result.as_of.isoformat(),
        "triggered_by": list(result.triggered_by),
        "trade_count": result.trade_count,
        "additional_funds": _number(result.additional_funds),
        "trades": [_trade_dict(trade) for trade in result.trades],
        "before": {
            "total_value": _number(result.initial_total),
            "cash": _number(result.cash_before),
            "allocations": [_allocation_dict(item) for item in result.before],
        },
        "projected": {
            "total_value": _number(result.final_total),
            "cash": _number(result.cash_after),
            "allocations": [_allocation_dict(item) for item in result.after],
        },
        "assumptions": [
            "prices are an AKShare snapshot",
            "fees, taxes, slippage, trading limits, and settlement rules are excluded",
            "each security trade is a multiple of 100 units",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portfolio-rebalancer",
        description="Calculate an A-share and ETF rebalancing plan.",
    )
    parser.add_argument("--portfolio", required=True, help="portfolio YAML file")
    parser.add_argument("--holdings", required=True, help="current holdings YAML file")
    parser.add_argument("--output", help="write JSON to this file instead of stdout")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_portfolio(args.portfolio)
        holdings = load_holdings(args.holdings)
        instruments = {asset.instrument for asset in config.assets}
        instruments.update(position.instrument for position in holdings.positions)
        quotes = fetch_quotes(instruments)
        result = plan(config, holdings, quotes)
    except (ConfigError, MarketDataError, PlannerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    output = json.dumps(plan_dict(result), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            Path(args.output).write_text(output, encoding="utf-8")
        except OSError as error:
            print(f"error: cannot write {args.output}: {error}", file=sys.stderr)
            return 2
    else:
        print(output, end="")
    return 0


def main() -> int:
    return run()
