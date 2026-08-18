from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class InstrumentKind(StrEnum):
    STOCK = "stock"
    ETF = "etf"


@dataclass(frozen=True, order=True)
class Instrument:
    kind: InstrumentKind
    symbol: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.symbol}"


@dataclass(frozen=True)
class TargetAllocation:
    instrument: Instrument
    weight: Decimal


@dataclass(frozen=True)
class Strategy:
    type: str
    threshold: Decimal


@dataclass(frozen=True)
class PortfolioConfig:
    strategy: Strategy
    allow_additional_funds: bool
    assets: tuple[TargetAllocation, ...]

    @property
    def cash_weight(self) -> Decimal:
        return Decimal(1) - sum((asset.weight for asset in self.assets), Decimal(0))


@dataclass(frozen=True)
class Position:
    instrument: Instrument
    quantity: int


@dataclass(frozen=True)
class Holdings:
    cash: Decimal
    positions: tuple[Position, ...]


@dataclass(frozen=True)
class Quote:
    instrument: Instrument
    price: Decimal
    name: str
    as_of: datetime


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Trade:
    side: TradeSide
    instrument: Instrument
    name: str
    quantity: int
    price: Decimal
    estimated_amount: Decimal


@dataclass(frozen=True)
class Allocation:
    key: str
    name: str
    quantity: int | None
    value: Decimal
    target_weight: Decimal
    actual_weight: Decimal
    relative_deviation: Decimal | None


@dataclass(frozen=True)
class RebalancePlan:
    status: str
    as_of: datetime
    triggered_by: tuple[str, ...]
    initial_total: Decimal
    final_total: Decimal
    additional_funds: Decimal
    cash_before: Decimal
    cash_after: Decimal
    trades: tuple[Trade, ...]
    before: tuple[Allocation, ...]
    after: tuple[Allocation, ...]

    @property
    def trade_count(self) -> int:
        return len(self.trades)
