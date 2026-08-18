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
    TargetAllocation,
    TradeSide,
    Trigger,
    TriggerType,
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
        allow_additional_funds=allow_funds,
        assets=tuple(
            TargetAllocation(
                instrument=instrument,
                weight=Decimal(weight),
                trigger=Trigger(
                    type=TriggerType.RELATIVE_DEVIATION,
                    threshold=Decimal(threshold),
                ),
            )
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

def test_each_asset_uses_its_own_trigger() -> None:
    absolute_inside = stock("600001")
    relative_outside = stock("600002")
    absolute_outside = stock("600003")
    result = plan(
        PortfolioConfig(
            allow_additional_funds=False,
            assets=(
                TargetAllocation(
                    instrument=absolute_inside,
                    weight=Decimal("0.2"),
                    trigger=Trigger(
                        type=TriggerType.ABSOLUTE_DEVIATION,
                        threshold=Decimal("0.05"),
                    ),
                ),
                TargetAllocation(
                    instrument=relative_outside,
                    weight=Decimal("0.2"),
                    trigger=Trigger(
                        type=TriggerType.RELATIVE_DEVIATION,
                        threshold=Decimal("0.15"),
                    ),
                ),
                TargetAllocation(
                    instrument=absolute_outside,
                    weight=Decimal("0.6"),
                    trigger=Trigger(
                        type=TriggerType.ABSOLUTE_DEVIATION,
                        threshold=Decimal("0.05"),
                    ),
                ),
            ),
        ),
        Holdings(
            cash=Decimal(0),
            positions=(
                Position(absolute_inside, 1600),
                Position(relative_outside, 1600),
                Position(absolute_outside, 6800),
            ),
        ),
        quote_map(
            (absolute_inside, "1"),
            (relative_outside, "1"),
            (absolute_outside, "1"),
        ),
    )

    assert result.triggered_by == (
        relative_outside.key,
        absolute_outside.key,
    )


def test_additional_funds_can_rebalance_without_a_security_trade() -> None:
    asset = stock("600001")
    result = plan(
        config([(asset, "0.5")], allow_funds=True),
        Holdings(cash=Decimal(0), positions=(Position(asset, 100),)),
        quote_map((asset, "10")),
    )

    assert result.status == "rebalance_required"
    assert result.trade_count == 0
    assert result.additional_funds == Decimal(1000)
    assert result.after[0].relative_deviation == 0
    assert result.after[-1].relative_deviation == 0


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


def test_triggered_rebalance_moves_entire_portfolio_to_target() -> None:
    first = stock("600001")
    second = stock("600002")
    third = stock("600003")
    result = plan(
        config([(first, "0.5"), (second, "0.3"), (third, "0.2")]),
        Holdings(
            cash=Decimal(0),
            positions=(
                Position(first, 300),
                Position(second, 300),
                Position(third, 400),
            ),
        ),
        quote_map((first, "1"), (second, "1"), (third, "1")),
    )

    assert result.status == "rebalance_required"
    assert result.trade_count == 2
    assert [allocation.quantity for allocation in result.after[:-1]] == [500, 300, 200]
    assert all(allocation.relative_deviation == 0 for allocation in result.after)


def test_target_seeking_handles_real_portfolio_scale() -> None:
    instruments = {
        symbol: stock(symbol)
        for symbol in (
            "159980",
            "159985",
            "511090",
            "511260",
            "512050",
            "513180",
            "513500",
            "518880",
        )
    }
    result = plan(
        config(
            [
                (instruments["159980"], "0.10"),
                (instruments["511260"], "0.15"),
                (instruments["159985"], "0.05"),
                (instruments["518880"], "0.10"),
                (instruments["513500"], "0.10"),
                (instruments["513180"], "0.05"),
                (instruments["512050"], "0.20"),
                (instruments["511090"], "0.25"),
            ],
            threshold="0.20",
            allow_funds=True,
        ),
        Holdings(
            cash=Decimal(0),
            positions=tuple(
                Position(instruments[symbol], quantity)
                for symbol, quantity in {
                    "513500": 27500,
                    "159980": 30600,
                    "159985": 17100,
                    "511090": 1400,
                    "511260": 700,
                    "512050": 104100,
                    "513180": 51000,
                    "518880": 5700,
                }.items()
            ),
        ),
        quote_map(
            (instruments["159980"], "2.150"),
            (instruments["159985"], "2.205"),
            (instruments["511090"], "119.503"),
            (instruments["511260"], "135.871"),
            (instruments["512050"], "1.253"),
            (instruments["513180"], "0.599"),
            (instruments["513500"], "2.688"),
            (instruments["518880"], "9.078"),
        ),
    )

    assert result.status == "rebalance_required"
    assert result.trade_count > 1
    assert max(
        allocation.relative_deviation or Decimal(0)
        for allocation in result.after
    ) < Decimal("0.03")


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
