from datetime import UTC, datetime
from decimal import Decimal

import pytest

from portfolio_rebalancer.domain import (
    Holdings,
    Instrument,
    InstrumentKind,
    PortfolioConfig,
    Position,
    Quote,
    Strategy,
    TargetAllocation,
    TradeSide,
)
from portfolio_rebalancer.planner import PlannerError, plan

NOW = datetime(2026, 8, 18, tzinfo=UTC)


def stock(symbol: str) -> Instrument:
    return Instrument(kind=InstrumentKind.STOCK, symbol=symbol)


def config(
    targets: list[tuple[Instrument, str]],
    *,
    threshold: str = "0.10",
    allow_funds: bool = False,
) -> PortfolioConfig:
    return PortfolioConfig(
        strategy=Strategy(
            type="relative_deviation", threshold=Decimal(threshold)
        ),
        allow_additional_funds=allow_funds,
        assets=tuple(
            TargetAllocation(instrument=instrument, weight=Decimal(weight))
            for instrument, weight in targets
        ),
    )


def quote_map(*items: tuple[Instrument, str]) -> dict[Instrument, Quote]:
    return {
        instrument: Quote(
            instrument=instrument,
            price=Decimal(price),
            name=instrument.symbol,
            as_of=NOW,
        )
        for instrument, price in items
    }


def test_returns_no_trades_when_portfolio_is_inside_band() -> None:
    first = stock("600001")
    second = stock("600002")
    result = plan(
        config([(first, "0.5"), (second, "0.5")]),
        Holdings(
            cash=Decimal(0),
            positions=(Position(first, 100), Position(second, 100)),
        ),
        quote_map((first, "10"), (second, "10")),
    )

    assert result.status == "no_rebalance"
    assert result.trade_count == 0
    assert result.additional_funds == 0


def test_additional_funds_can_rebalance_without_a_security_trade() -> None:
    asset = stock("600001")
    result = plan(
        config([(asset, "0.5")], allow_funds=True),
        Holdings(cash=Decimal(0), positions=(Position(asset, 100),)),
        quote_map((asset, "10")),
    )

    assert result.status == "rebalance_required"
    assert result.trade_count == 0
    assert result.additional_funds == Decimal("818.1819")
    asset_deviation = result.after[0].relative_deviation
    cash_deviation = result.after[-1].relative_deviation
    assert asset_deviation is not None
    assert cash_deviation is not None
    assert asset_deviation <= Decimal("0.10")
    assert cash_deviation <= Decimal("0.10")


def test_minimum_plan_buys_missing_target_with_one_trade() -> None:
    first = stock("600001")
    second = stock("600002")
    result = plan(
        config([(first, "0.5"), (second, "0.5")], allow_funds=True),
        Holdings(cash=Decimal(0), positions=(Position(first, 100),)),
        quote_map((first, "10"), (second, "10")),
    )

    assert result.trade_count == 1
    assert result.additional_funds == Decimal(1000)
    assert result.trades[0].side == TradeSide.BUY
    assert result.trades[0].instrument == second
    assert result.trades[0].quantity == 100


def test_without_funding_sells_before_buying() -> None:
    first = stock("600001")
    second = stock("600002")
    result = plan(
        config([(first, "0.5"), (second, "0.5")]),
        Holdings(cash=Decimal(0), positions=(Position(first, 200),)),
        quote_map((first, "10"), (second, "10")),
    )

    assert result.trade_count == 2
    assert result.additional_funds == 0
    assert [(trade.side, trade.instrument, trade.quantity) for trade in result.trades] == [
        (TradeSide.SELL, first, 100),
        (TradeSide.BUY, second, 100),
    ]
    assert result.cash_after == 0


def test_position_outside_target_is_fully_sold() -> None:
    target = stock("600001")
    removed = stock("600002")
    result = plan(
        config([(target, "1")]),
        Holdings(cash=Decimal(0), positions=(Position(removed, 100),)),
        quote_map((target, "10"), (removed, "10")),
    )

    assert result.trade_count == 2
    assert result.trades[0].side == TradeSide.SELL
    assert result.trades[0].instrument == removed
    assert result.after[1].quantity == 0


def test_broad_portfolio_is_optimized_without_subset_enumeration() -> None:
    instruments = [stock(f"60{index:04d}") for index in range(1, 26)]
    result = plan(
        config([(instrument, "0.04") for instrument in instruments]),
        Holdings(
            cash=Decimal(0),
            positions=(Position(instruments[0], 2500),),
        ),
        quote_map(*[(instrument, "10") for instrument in instruments]),
    )

    assert result.trade_count == 25
    assert result.additional_funds == 0
    assert all(
        allocation.quantity == 100
        for allocation in result.after
        if allocation.key != "cash"
    )


def test_missing_quote_is_reported() -> None:
    asset = stock("600001")
    with pytest.raises(PlannerError, match="missing quotes"):
        plan(
            config([(asset, "1")]),
            Holdings(cash=Decimal(1000), positions=()),
            {},
        )
