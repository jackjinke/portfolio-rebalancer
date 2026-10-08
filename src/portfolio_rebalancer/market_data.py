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


def _load_etf_fallback_frame():
    try:
        import akshare as ak

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return ak.fund_etf_category_sina(symbol="ETF基金")
    except Exception as error:
        raise MarketDataError(f"failed to fetch ETF fallback quotes: {error}") from error


def _normalize_frame(frame, context: str, *, prefixed_codes: bool = False):
    if "代码" not in frame.columns or "最新价" not in frame.columns:
        raise MarketDataError(f"unexpected AKShare {context} quote columns")
    normalized = frame.copy()
    codes = normalized["代码"].astype(str).str.strip()
    normalized["代码"] = (
        codes.str.extract(r"(\d{6})$", expand=False)
        if prefixed_codes
        else codes.str.zfill(6)
    )
    return normalized.drop_duplicates(subset="代码", keep="first").set_index("代码")


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
        primary_error = None
        normalized = None
        try:
            frame = _load_frame(kind)
        except MarketDataError as error:
            if kind != InstrumentKind.ETF:
                raise
            primary_error = error
        else:
            normalized = _normalize_frame(frame, kind.value)

        fallback = None
        if kind == InstrumentKind.ETF and (
            normalized is None
            or any(
                instrument.symbol not in normalized.index
                for instrument in kind_instruments
            )
        ):
            try:
                fallback = _normalize_frame(
                    _load_etf_fallback_frame(),
                    "ETF fallback",
                    prefixed_codes=True,
                )
            except MarketDataError as error:
                if primary_error is not None:
                    raise MarketDataError(f"{primary_error}; {error}") from error
                raise

        for instrument in kind_instruments:
            source = normalized
            if source is None or instrument.symbol not in source.index:
                source = fallback
            if source is None or instrument.symbol not in source.index:
                raise MarketDataError(f"quote not found: {instrument.key}")
            row = source.loc[instrument.symbol]
            try:
                price = Decimal(str(row["最新价"]))
            except (InvalidOperation, ValueError) as error:
                raise MarketDataError(f"invalid quote price: {instrument.key}") from error
            if not price.is_finite() or price <= 0:
                raise MarketDataError(f"invalid quote price: {instrument.key}")
            name_value = row["名称"] if "名称" in source.columns else instrument.symbol
            quotes[instrument] = Quote(
                instrument=instrument,
                price=price,
                name=str(name_value),
                as_of=as_of,
            )

    return quotes
