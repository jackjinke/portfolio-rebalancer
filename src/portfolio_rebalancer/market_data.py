from __future__ import annotations

from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from zoneinfo import ZoneInfo

from portfolio_rebalancer.domain import Instrument, InstrumentKind, Quote


class MarketDataError(RuntimeError):
    pass


def _load_frame(kind: InstrumentKind):
    try:
        import akshare as ak

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if kind == InstrumentKind.STOCK:
                return ak.stock_zh_a_spot_em()
            return ak.fund_etf_spot_em()
    except Exception as error:
        raise MarketDataError(f"failed to fetch {kind.value} quotes: {error}") from error


def fetch_quotes(instruments: Iterable[Instrument]) -> dict[Instrument, Quote]:
    requested = tuple(sorted(set(instruments), key=lambda item: item.key))
    by_kind = {
        kind: tuple(item for item in requested if item.kind == kind)
        for kind in InstrumentKind
    }
    as_of = datetime.now(ZoneInfo("Asia/Shanghai"))
    quotes: dict[Instrument, Quote] = {}

    for kind, kind_instruments in by_kind.items():
        if not kind_instruments:
            continue
        frame = _load_frame(kind)
        if "代码" not in frame.columns or "最新价" not in frame.columns:
            raise MarketDataError(f"unexpected AKShare {kind.value} quote columns")
        normalized = frame.copy()
        normalized["代码"] = normalized["代码"].astype(str).str.strip().str.zfill(6)
        normalized = normalized.drop_duplicates(subset="代码", keep="first").set_index("代码")

        for instrument in kind_instruments:
            if instrument.symbol not in normalized.index:
                raise MarketDataError(f"quote not found: {instrument.key}")
            row = normalized.loc[instrument.symbol]
            try:
                price = Decimal(str(row["最新价"]))
            except (InvalidOperation, ValueError) as error:
                raise MarketDataError(f"invalid quote price: {instrument.key}") from error
            if not price.is_finite() or price <= 0:
                raise MarketDataError(f"invalid quote price: {instrument.key}")
            name_value = row["名称"] if "名称" in normalized.columns else instrument.symbol
            quotes[instrument] = Quote(
                instrument=instrument,
                price=price,
                name=str(name_value),
                as_of=as_of,
            )

    return quotes
