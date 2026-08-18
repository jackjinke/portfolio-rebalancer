from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from pulp import (
    PULP_CBC_CMD,
    LpBinary,
    LpInteger,
    LpMinimize,
    LpProblem,
    LpStatusOptimal,
    LpVariable,
    lpSum,
    value,
)

from portfolio_rebalancer.domain import (
    Allocation,
    Holdings,
    Instrument,
    PortfolioConfig,
    Quote,
    RebalancePlan,
    Trade,
    TradeSide,
)

LOT_SIZE = 100
MONEY_SCALE = 10_000


class PlannerError(ValueError):
    pass


@dataclass(frozen=True)
class _Candidate:
    funding_units: int
    final_lots: dict[Instrument, int]
    final_cash_units: int
    max_deviation: Decimal
    turnover_units: int


def _to_units(amount: Decimal, context: str) -> int:
    scaled = amount * MONEY_SCALE
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise PlannerError(f"{context} has more than four decimal places")
    return int(integral)


def _from_units(amount: int) -> Decimal:
    return Decimal(amount) / MONEY_SCALE


def _deviation(value_units: int, total_units: int, target: Decimal) -> Decimal | None:
    if target == 0:
        return Decimal(0) if value_units == 0 else None
    actual = Decimal(value_units) / Decimal(total_units)
    return abs(actual - target) / target


def _validate_instruments(config: PortfolioConfig, holdings: Holdings) -> None:
    kinds_by_symbol = {asset.instrument.symbol: asset.instrument.kind for asset in config.assets}
    for position in holdings.positions:
        expected = kinds_by_symbol.get(position.instrument.symbol)
        if expected is not None and expected != position.instrument.kind:
            raise PlannerError(
                f"instrument kind differs between portfolio and holdings: "
                f"{position.instrument.symbol}"
            )
        if position.quantity % LOT_SIZE:
            raise PlannerError(
                f"position quantity must be a multiple of {LOT_SIZE}: "
                f"{position.instrument.key}"
            )


def _allocations(
    instruments: tuple[Instrument, ...],
    lots: Mapping[Instrument, int],
    cash_units: int,
    total_units: int,
    target_weights: Mapping[Instrument, Decimal],
    lot_values: Mapping[Instrument, int],
    quotes: Mapping[Instrument, Quote],
    cash_target: Decimal,
) -> tuple[Allocation, ...]:
    rows: list[Allocation] = []
    for instrument in instruments:
        quantity = lots[instrument] * LOT_SIZE
        value_units = lots[instrument] * lot_values[instrument]
        target = target_weights.get(instrument, Decimal(0))
        rows.append(
            Allocation(
                key=instrument.key,
                name=quotes[instrument].name,
                quantity=quantity,
                value=_from_units(value_units),
                target_weight=target,
                actual_weight=Decimal(value_units) / Decimal(total_units),
                relative_deviation=_deviation(value_units, total_units, target),
            )
        )
    rows.append(
        Allocation(
            key="cash",
            name="现金",
            quantity=None,
            value=_from_units(cash_units),
            target_weight=cash_target,
            actual_weight=Decimal(cash_units) / Decimal(total_units),
            relative_deviation=_deviation(cash_units, total_units, cash_target),
        )
    )
    return tuple(rows)


def _max_deviation(
    instruments: Iterable[Instrument],
    lots: Mapping[Instrument, int],
    cash_units: int,
    total_units: int,
    targets: Mapping[Instrument, Decimal],
    lot_values: Mapping[Instrument, int],
    cash_target: Decimal,
) -> Decimal:
    deviations = [
        deviation
        for instrument in instruments
        if (
            deviation := _deviation(
                lots[instrument] * lot_values[instrument],
                total_units,
                targets.get(instrument, Decimal(0)),
            )
        )
        is not None
    ]
    cash_deviation = _deviation(cash_units, total_units, cash_target)
    if cash_deviation is not None:
        deviations.append(cash_deviation)
    return max(deviations, default=Decimal(0))


def _solve_subset(
    instruments: tuple[Instrument, ...],
    adjustable: frozenset[Instrument],
    current_lots: Mapping[Instrument, int],
    target_weights: Mapping[Instrument, Decimal],
    lot_values: Mapping[Instrument, int],
    initial_cash_units: int,
    initial_total_units: int,
    cash_target: Decimal,
    threshold: Decimal,
    allow_funds: bool,
) -> _Candidate | None:
    for instrument in instruments:
        if (
            target_weights.get(instrument, Decimal(0)) == 0
            and current_lots[instrument] > 0
            and instrument not in adjustable
        ):
            return None

    problem = LpProblem("portfolio_rebalance", LpMinimize)
    funding = (
        LpVariable("additional_funds", lowBound=0, cat=LpInteger)
        if allow_funds
        else None
    )
    funding_expression = funding if funding is not None else 0

    final_lots: dict[Instrument, int | LpVariable] = {}
    for index, instrument in enumerate(instruments):
        target = target_weights.get(instrument, Decimal(0))
        if target == 0:
            final_lots[instrument] = 0
        elif instrument in adjustable:
            final_lots[instrument] = LpVariable(
                f"lots_{index}", lowBound=0, cat=LpInteger
            )
        else:
            final_lots[instrument] = current_lots[instrument]

    final_cash = initial_cash_units + funding_expression - lpSum(
        (final_lots[instrument] - current_lots[instrument]) * lot_values[instrument]
        for instrument in instruments
    )
    final_total = lpSum([initial_total_units, funding_expression])
    problem += final_cash >= 0

    lower_factor = Decimal(1) - threshold
    upper_factor = Decimal(1) + threshold
    for instrument in instruments:
        target = target_weights.get(instrument, Decimal(0))
        final_value = lpSum([final_lots[instrument] * lot_values[instrument]])
        if target == 0:
            continue
        problem += final_value >= float(target * lower_factor) * final_total
        problem += final_value <= float(target * upper_factor) * final_total

    if cash_target > 0:
        problem += final_cash >= float(cash_target * lower_factor) * final_total
        problem += final_cash <= float(cash_target * upper_factor) * final_total

    solver = PULP_CBC_CMD(msg=False)
    if funding is not None:
        problem.setObjective(funding)
        if problem.solve(solver) != LpStatusOptimal:
            return None
        funding_units = round(value(funding))
        problem += funding == funding_units
    else:
        funding_units = 0
    constant_total = initial_total_units + funding_units

    max_deviation = LpVariable("max_relative_deviation", lowBound=0)
    for instrument in instruments:
        target = target_weights.get(instrument, Decimal(0))
        if target == 0:
            continue
        target_value = float(target) * constant_total
        difference = final_lots[instrument] * lot_values[instrument] - target_value
        scale = float(target) * constant_total
        problem += difference <= max_deviation * scale
        problem += -difference <= max_deviation * scale
    if cash_target > 0:
        cash_difference = final_cash - float(cash_target) * constant_total
        cash_scale = float(cash_target) * constant_total
        problem += cash_difference <= max_deviation * cash_scale
        problem += -cash_difference <= max_deviation * cash_scale

    problem.setObjective(max_deviation)
    if problem.solve(solver) != LpStatusOptimal:
        return None
    best_max_deviation = float(value(max_deviation))
    problem += max_deviation <= best_max_deviation + 1e-8

    turnover_terms = []
    for index, instrument in enumerate(instruments):
        target = target_weights.get(instrument, Decimal(0))
        if target == 0:
            turnover_terms.append(current_lots[instrument] * lot_values[instrument])
        elif instrument in adjustable:
            changed_lots = LpVariable(f"changed_lots_{index}", lowBound=0)
            difference = final_lots[instrument] - current_lots[instrument]
            problem += changed_lots >= difference
            problem += changed_lots >= -difference
            turnover_terms.append(changed_lots * lot_values[instrument])

    problem.setObjective(lpSum(turnover_terms))
    if problem.solve(solver) != LpStatusOptimal:
        return None

    result_lots: dict[Instrument, int] = {}
    for instrument in instruments:
        expression = final_lots[instrument]
        result_lots[instrument] = (
            expression if isinstance(expression, int) else round(value(expression))
        )

    final_cash_units = initial_cash_units + funding_units - sum(
        (result_lots[instrument] - current_lots[instrument]) * lot_values[instrument]
        for instrument in instruments
    )
    if final_cash_units < 0:
        return None
    turnover_units = sum(
        abs(result_lots[instrument] - current_lots[instrument])
        * lot_values[instrument]
        for instrument in instruments
    )
    return _Candidate(
        funding_units=funding_units,
        final_lots=result_lots,
        final_cash_units=final_cash_units,
        max_deviation=_max_deviation(
            instruments,
            result_lots,
            final_cash_units,
            constant_total,
            target_weights,
            lot_values,
            cash_target,
        ),
        turnover_units=turnover_units,
    )


def _solve_optimal(
    instruments: tuple[Instrument, ...],
    current_lots: Mapping[Instrument, int],
    target_weights: Mapping[Instrument, Decimal],
    lot_values: Mapping[Instrument, int],
    initial_cash_units: int,
    initial_total_units: int,
    cash_target: Decimal,
    threshold: Decimal,
    allow_funds: bool,
    total_upper_bound: int,
) -> _Candidate | None:
    problem = LpProblem("portfolio_rebalance_optimal", LpMinimize)
    funding = (
        LpVariable(
            "optimal_additional_funds",
            lowBound=0,
            upBound=total_upper_bound - initial_total_units,
            cat=LpInteger,
        )
        if allow_funds
        else None
    )
    funding_expression = funding if funding is not None else 0
    lower_factor = Decimal(1) - threshold
    upper_factor = Decimal(1) + threshold

    final_lots: dict[Instrument, int | LpVariable] = {}
    trade_indicators = []
    for index, instrument in enumerate(instruments):
        target = target_weights.get(instrument, Decimal(0))
        current = current_lots[instrument]
        if target == 0:
            final_lots[instrument] = 0
            if current > 0:
                trade_indicators.append(1)
            continue

        max_lots = int(
            (
                Decimal(total_upper_bound) * target * upper_factor
                / Decimal(lot_values[instrument])
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        lots = LpVariable(
            f"optimal_lots_{index}",
            lowBound=0,
            upBound=max_lots,
            cat=LpInteger,
        )
        changed = LpVariable(f"traded_{index}", cat=LpBinary)
        final_lots[instrument] = lots
        trade_indicators.append(changed)
        problem += lots - current <= (max_lots - current) * changed
        problem += current - lots <= current * changed

    final_cash = initial_cash_units + funding_expression - lpSum(
        (final_lots[instrument] - current_lots[instrument]) * lot_values[instrument]
        for instrument in instruments
    )
    final_total = lpSum([initial_total_units, funding_expression])
    problem += final_cash >= 0
    for instrument in instruments:
        target = target_weights.get(instrument, Decimal(0))
        if target == 0:
            continue
        final_value = lpSum([final_lots[instrument] * lot_values[instrument]])
        problem += final_value >= float(target * lower_factor) * final_total
        problem += final_value <= float(target * upper_factor) * final_total
    if cash_target > 0:
        problem += final_cash >= float(cash_target * lower_factor) * final_total
        problem += final_cash <= float(cash_target * upper_factor) * final_total

    solver = PULP_CBC_CMD(msg=False)
    trade_count = lpSum(trade_indicators)
    problem.setObjective(trade_count)
    if problem.solve(solver) != LpStatusOptimal:
        return None
    best_trade_count = round(value(trade_count))
    problem += trade_count == best_trade_count

    if funding is not None:
        problem.setObjective(funding)
        if problem.solve(solver) != LpStatusOptimal:
            return None
        funding_units = round(value(funding))
        problem += funding == funding_units
    else:
        funding_units = 0
    constant_total = initial_total_units + funding_units

    max_deviation = LpVariable("optimal_max_relative_deviation", lowBound=0)
    for instrument in instruments:
        target = target_weights.get(instrument, Decimal(0))
        if target == 0:
            continue
        target_value = float(target) * constant_total
        difference = final_lots[instrument] * lot_values[instrument] - target_value
        scale = float(target) * constant_total
        problem += difference <= max_deviation * scale
        problem += -difference <= max_deviation * scale
    if cash_target > 0:
        cash_difference = final_cash - float(cash_target) * constant_total
        cash_scale = float(cash_target) * constant_total
        problem += cash_difference <= max_deviation * cash_scale
        problem += -cash_difference <= max_deviation * cash_scale

    problem.setObjective(max_deviation)
    if problem.solve(solver) != LpStatusOptimal:
        return None
    best_max_deviation = float(value(max_deviation))
    problem += max_deviation <= best_max_deviation + 1e-8

    turnover_terms = []
    for index, instrument in enumerate(instruments):
        target = target_weights.get(instrument, Decimal(0))
        if target == 0:
            turnover_terms.append(current_lots[instrument] * lot_values[instrument])
            continue
        changed_lots = LpVariable(f"optimal_changed_lots_{index}", lowBound=0)
        difference = final_lots[instrument] - current_lots[instrument]
        problem += changed_lots >= difference
        problem += changed_lots >= -difference
        turnover_terms.append(changed_lots * lot_values[instrument])

    problem.setObjective(lpSum(turnover_terms))
    if problem.solve(solver) != LpStatusOptimal:
        return None

    result_lots = {
        instrument: (
            expression if isinstance(expression, int) else round(value(expression))
        )
        for instrument, expression in final_lots.items()
    }
    final_cash_units = initial_cash_units + funding_units - sum(
        (result_lots[instrument] - current_lots[instrument]) * lot_values[instrument]
        for instrument in instruments
    )
    if final_cash_units < 0:
        return None
    turnover_units = sum(
        abs(result_lots[instrument] - current_lots[instrument])
        * lot_values[instrument]
        for instrument in instruments
    )
    return _Candidate(
        funding_units=funding_units,
        final_lots=result_lots,
        final_cash_units=final_cash_units,
        max_deviation=_max_deviation(
            instruments,
            result_lots,
            final_cash_units,
            constant_total,
            target_weights,
            lot_values,
            cash_target,
        ),
        turnover_units=turnover_units,
    )


def plan(
    config: PortfolioConfig,
    holdings: Holdings,
    quotes: Mapping[Instrument, Quote],
) -> RebalancePlan:
    _validate_instruments(config, holdings)
    target_weights = {asset.instrument: asset.weight for asset in config.assets}
    held_quantities = {
        position.instrument: position.quantity for position in holdings.positions
    }
    instruments = tuple(
        sorted(set(target_weights) | set(held_quantities), key=lambda item: item.key)
    )

    missing_quotes = [item.key for item in instruments if item not in quotes]
    if missing_quotes:
        raise PlannerError(f"missing quotes: {', '.join(missing_quotes)}")

    lot_values: dict[Instrument, int] = {}
    for instrument in instruments:
        quote = quotes[instrument]
        if quote.price <= 0:
            raise PlannerError(f"quote price must be positive: {instrument.key}")
        lot_values[instrument] = _to_units(
            quote.price * LOT_SIZE, f"quote for {instrument.key}"
        )

    current_lots = {
        instrument: held_quantities.get(instrument, 0) // LOT_SIZE
        for instrument in instruments
    }
    initial_cash_units = _to_units(holdings.cash, "cash")
    initial_total_units = initial_cash_units + sum(
        current_lots[instrument] * lot_values[instrument]
        for instrument in instruments
    )
    if initial_total_units <= 0:
        raise PlannerError("portfolio total must be positive")

    cash_target = config.cash_weight
    threshold = config.strategy.threshold
    triggered_by: list[str] = []
    for instrument in instruments:
        target = target_weights.get(instrument, Decimal(0))
        value_units = current_lots[instrument] * lot_values[instrument]
        deviation = _deviation(value_units, initial_total_units, target)
        if (deviation is None and value_units > 0) or (
            deviation is not None and deviation > threshold
        ):
            triggered_by.append(instrument.key)
    cash_deviation = _deviation(initial_cash_units, initial_total_units, cash_target)
    if cash_deviation is not None and cash_deviation > threshold:
        triggered_by.append("cash")

    as_of = max(quote.as_of for quote in quotes.values())
    before = _allocations(
        instruments,
        current_lots,
        initial_cash_units,
        initial_total_units,
        target_weights,
        lot_values,
        quotes,
        cash_target,
    )
    if not triggered_by:
        return RebalancePlan(
            status="no_rebalance",
            as_of=as_of,
            triggered_by=(),
            initial_total=_from_units(initial_total_units),
            final_total=_from_units(initial_total_units),
            additional_funds=Decimal(0),
            cash_before=holdings.cash,
            cash_after=holdings.cash,
            trades=(),
            before=before,
            after=before,
        )

    feasible = _solve_subset(
        instruments=instruments,
        adjustable=frozenset(instruments),
        current_lots=current_lots,
        target_weights=target_weights,
        lot_values=lot_values,
        initial_cash_units=initial_cash_units,
        initial_total_units=initial_total_units,
        cash_target=cash_target,
        threshold=threshold,
        allow_funds=config.allow_additional_funds,
    )
    if feasible is None:
        raise PlannerError(
            "no feasible plan satisfies the deviation threshold and lot-size constraints"
        )

    total_upper_bound = initial_total_units + feasible.funding_units
    if config.allow_additional_funds:
        lower_factor = Decimal(1) - threshold
        for instrument in instruments:
            target = target_weights.get(instrument, Decimal(0))
            current_value = current_lots[instrument] * lot_values[instrument]
            if target > 0 and current_value > 0:
                unchanged_bound = int(
                    (
                        Decimal(current_value) / (target * lower_factor)
                    ).to_integral_value(rounding=ROUND_CEILING)
                )
                total_upper_bound = max(total_upper_bound, unchanged_bound)

    chosen = _solve_optimal(
        instruments=instruments,
        current_lots=current_lots,
        target_weights=target_weights,
        lot_values=lot_values,
        initial_cash_units=initial_cash_units,
        initial_total_units=initial_total_units,
        cash_target=cash_target,
        threshold=threshold,
        allow_funds=config.allow_additional_funds,
        total_upper_bound=total_upper_bound,
    )
    if chosen is None:
        raise PlannerError(
            "no feasible plan satisfies the deviation threshold and lot-size constraints"
        )

    trades: list[Trade] = []
    for instrument in instruments:
        changed_lots = chosen.final_lots[instrument] - current_lots[instrument]
        if changed_lots == 0:
            continue
        quantity = abs(changed_lots) * LOT_SIZE
        quote = quotes[instrument]
        trades.append(
            Trade(
                side=TradeSide.BUY if changed_lots > 0 else TradeSide.SELL,
                instrument=instrument,
                name=quote.name,
                quantity=quantity,
                price=quote.price,
                estimated_amount=quote.price * quantity,
            )
        )
    trades.sort(key=lambda trade: (trade.side == TradeSide.BUY, trade.instrument.key))

    final_total_units = initial_total_units + chosen.funding_units
    after = _allocations(
        instruments,
        chosen.final_lots,
        chosen.final_cash_units,
        final_total_units,
        target_weights,
        lot_values,
        quotes,
        cash_target,
    )
    return RebalancePlan(
        status="rebalance_required",
        as_of=as_of,
        triggered_by=tuple(triggered_by),
        initial_total=_from_units(initial_total_units),
        final_total=_from_units(final_total_units),
        additional_funds=_from_units(chosen.funding_units),
        cash_before=holdings.cash,
        cash_after=_from_units(chosen.final_cash_units),
        trades=tuple(trades),
        before=before,
        after=after,
    )
