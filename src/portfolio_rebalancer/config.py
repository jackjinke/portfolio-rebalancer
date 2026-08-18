from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from portfolio_rebalancer.domain import (
    Holdings,
    Instrument,
    InstrumentKind,
    PortfolioConfig,
    Position,
    TargetAllocation,
    Trigger,
    TriggerType,
)


class ConfigError(ValueError):
    pass


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a mapping")
    return value


def _list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{context} must be a list")
    return value


def _decimal(value: Any, context: str) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{context} must be a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ConfigError(f"{context} must be a number") from error
    if not result.is_finite():
        raise ConfigError(f"{context} must be finite")
    return result


def _instrument(data: Any, context: str) -> Instrument:
    item = _mapping(data, context)
    symbol = item.get("symbol")
    if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit():
        raise ConfigError(f"{context}.symbol must be a six-digit string")
    try:
        kind = InstrumentKind(item.get("kind"))
    except ValueError as error:
        raise ConfigError(f"{context}.kind must be 'stock' or 'etf'") from error
    return Instrument(kind=kind, symbol=symbol)


def _load_yaml(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    try:
        with file_path.open(encoding="utf-8") as file:
            return _mapping(yaml.safe_load(file), str(file_path))
    except OSError as error:
        raise ConfigError(f"cannot read {file_path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {file_path}: {error}") from error


def load_portfolio(path: str | Path) -> PortfolioConfig:
    data = _load_yaml(path)
    allow_funds = data.get("allow_additional_funds")
    if not isinstance(allow_funds, bool):
        raise ConfigError("allow_additional_funds must be true or false")

    assets: list[TargetAllocation] = []
    seen: set[str] = set()
    for index, raw_asset in enumerate(_list(data.get("assets"), "assets")):
        context = f"assets[{index}]"
        item = _mapping(raw_asset, context)
        instrument = _instrument(item, context)
        if instrument.symbol in seen:
            raise ConfigError(f"duplicate asset symbol: {instrument.symbol}")
        seen.add(instrument.symbol)
        weight = _decimal(item.get("target_weight"), f"{context}.target_weight")
        if not Decimal(0) < weight <= Decimal(1):
            raise ConfigError(f"{context}.target_weight must be between 0 and 1")

        trigger_context = f"{context}.trigger"
        trigger_data = _mapping(item.get("trigger"), trigger_context)
        try:
            trigger_type = TriggerType(trigger_data.get("type"))
        except (TypeError, ValueError) as error:
            raise ConfigError(
                f"{trigger_context}.type must be 'relative' or "
                "'absolute'"
            ) from error
        threshold = _decimal(
            trigger_data.get("threshold"), f"{trigger_context}.threshold"
        )
        if not Decimal(0) < threshold < Decimal(1):
            raise ConfigError(
                f"{trigger_context}.threshold must be between 0 and 1"
            )
        assets.append(
            TargetAllocation(
                instrument=instrument,
                weight=weight,
                trigger=Trigger(type=trigger_type, threshold=threshold),
            )
        )

    if not assets:
        raise ConfigError("assets must contain at least one target")
    total_weight = sum((asset.weight for asset in assets), Decimal(0))
    if total_weight > Decimal(1):
        raise ConfigError("target weights must not sum to more than 1")

    return PortfolioConfig(
        allow_additional_funds=allow_funds,
        assets=tuple(assets),
    )


def load_holdings(path: str | Path) -> Holdings:
    data = _load_yaml(path)
    cash = _decimal(data.get("cash"), "cash")
    if cash < 0:
        raise ConfigError("cash must not be negative")

    positions: list[Position] = []
    seen: set[str] = set()
    for index, raw_position in enumerate(_list(data.get("positions"), "positions")):
        context = f"positions[{index}]"
        item = _mapping(raw_position, context)
        instrument = _instrument(item, context)
        if instrument.symbol in seen:
            raise ConfigError(f"duplicate position symbol: {instrument.symbol}")
        seen.add(instrument.symbol)
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ConfigError(f"{context}.quantity must be an integer")
        if quantity < 0 or quantity % 100 != 0:
            raise ConfigError(f"{context}.quantity must be a non-negative multiple of 100")
        if quantity:
            positions.append(Position(instrument=instrument, quantity=quantity))

    return Holdings(cash=cash, positions=tuple(positions))
