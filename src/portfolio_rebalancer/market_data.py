from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from io import StringIO
from time import sleep
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from portfolio_rebalancer.domain import Instrument, InstrumentKind, Quote

SHANGHAI = ZoneInfo("Asia/Shanghai")


class MarketDataError(RuntimeError):
    pass


def _retry[T](operation: Callable[[], T], source: str) -> T:
    for attempt in range(2):
        try:
            return operation()
        except MarketDataError as error:
            if attempt == 1:
                raise MarketDataError(f"{source} failed after 2 attempts: {error}") from error
            sleep(0.25)
    raise AssertionError("unreachable")


def _load_frame(kind: InstrumentKind):
    try:
        import akshare as ak

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            if kind == InstrumentKind.STOCK:
                return ak.stock_zh_a_spot_em()
            return ak.fund_etf_spot_em()
    except Exception as error:
        raise MarketDataError(f"failed to fetch {kind.value} quotes: {error}") from error


def _request_text(url: str, *, referer: str | None = None) -> str:
    def request() -> str:
        headers = {"User-Agent": "Mozilla/5.0"}
        if referer is not None:
            headers["Referer"] = referer
        try:
            with urlopen(Request(url, headers=headers), timeout=8) as response:
                return response.read().decode("gb18030")
        except (URLError, OSError, HTTPException, UnicodeError, ValueError) as error:
            raise MarketDataError(f"request {url}: {error}") from error

    return _retry(request, url)


def _market_symbol(instrument: Instrument) -> str:
    symbol = instrument.symbol
    if not re.fullmatch(r"[0-9]{6}", symbol):
        raise MarketDataError(f"unsupported security code: {instrument.key}")
    prefix = symbol[:2]
    if instrument.kind == InstrumentKind.ETF:
        if prefix == "15":
            return f"sz{symbol}"
        if prefix in {"51", "52", "53", "56", "58"}:
            return f"sh{symbol}"
    elif instrument.kind == InstrumentKind.STOCK:
        if prefix in {"60", "68"}:
            return f"sh{symbol}"
        if prefix in {"00", "30"}:
            return f"sz{symbol}"
        if prefix in {"43", "83", "87", "92"}:
            return f"bj{symbol}"
    raise MarketDataError(f"unsupported security prefix: {instrument.key}")


def _source_time(value: object) -> datetime:
    try:
        text = str(value).strip()
        if not re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}", text):
            raise ValueError("source timestamp must contain a date and time")
        timestamp = datetime.fromisoformat(text)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=SHANGHAI)
        timestamp = timestamp.astimezone(SHANGHAI)
    except (ValueError, TypeError, OverflowError) as error:
        raise MarketDataError(f"missing or invalid source timestamp: {value!r}") from error
    if timestamp > datetime.now(SHANGHAI) + timedelta(minutes=5):
        raise MarketDataError(f"source timestamp is in the future: {timestamp.isoformat()}")
    return timestamp


def _quote(
    instrument: Instrument, price: object, name: object, timestamp: object, source: str
) -> Quote:
    try:
        parsed_price = Decimal(str(price))
    except (InvalidOperation, ValueError) as error:
        raise MarketDataError(f"invalid quote price: {price!r}") from error
    if not parsed_price.is_finite() or parsed_price <= 0:
        raise MarketDataError(f"invalid quote price: {price!r}")
    return Quote(
        instrument=instrument,
        price=parsed_price,
        name=str(name),
        as_of=_source_time(timestamp),
        source=source,
    )


def _normalize_frame(frame, context: str):
    if "代码" not in frame.columns or "最新价" not in frame.columns:
        raise MarketDataError(f"unexpected AKShare {context} quote columns")
    normalized = frame.copy()
    normalized["代码"] = normalized["代码"].astype(str).str.strip().str.zfill(6)
    return normalized.drop_duplicates(subset="代码", keep="first").set_index("代码")


def _primary_quote(frame, instrument: Instrument) -> Quote:
    if instrument.symbol not in frame.index:
        raise MarketDataError("quote not found")
    row = frame.loc[instrument.symbol]
    return _quote(
        instrument,
        row["最新价"],
        row.get("名称", instrument.symbol),
        row.get("更新时间"),
        "eastmoney",
    )


def _tencent_quote(instrument: Instrument, market_symbol: str) -> Quote:
    text = _request_text(f"https://qt.gtimg.cn/q={market_symbol}")
    match = re.fullmatch(r'\s*v_([a-z]{2}[0-9]{6})="([^"\r\n]*)";?\s*', text)
    if match is None or match[1] != market_symbol:
        raise MarketDataError("unexpected Tencent response or security code")
    fields = match[2].split("~")
    if len(fields) <= 61 or fields[2] != instrument.symbol:
        raise MarketDataError("Tencent quote missing fields or security code mismatch")
    security_type = fields[61].strip()
    valid_type = (
        security_type == "ETF"
        if instrument.kind == InstrumentKind.ETF
        else security_type.startswith("GP")
    )
    if not valid_type:
        raise MarketDataError(f"Tencent security_type mismatch: {security_type!r}")
    try:
        if not re.fullmatch(r"[0-9]{14}", fields[30]):
            raise ValueError("expected YYYYMMDDHHMMSS")
        timestamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
    except ValueError as error:
        raise MarketDataError(f"invalid Tencent source timestamp: {fields[30]!r}") from error
    return _quote(instrument, fields[3], fields[1], timestamp, "tencent")


def _sina_quote(instrument: Instrument, market_symbol: str) -> Quote:
    text = _request_text(
        f"https://hq.sinajs.cn/list={market_symbol}",
        referer="https://finance.sina.com.cn/",
    )
    match = re.fullmatch(
        r'\s*var\s+hq_str_([a-z]{2}[0-9]{6})="([^"\r\n]*)";?\s*', text
    )
    if match is None or match[1] != market_symbol:
        raise MarketDataError("unexpected Sina response or security code")
    fields = match[2].split(",")
    if len(fields) <= 32:
        raise MarketDataError("Sina quote missing fields")
    if fields[32].strip() != "00":
        raise MarketDataError(f"Sina security suspended or unavailable: status={fields[32]!r}")
    return _quote(
        instrument, fields[3], fields[0], f"{fields[30]} {fields[31]}", "sina"
    )


def fetch_quotes(instruments: Iterable[Instrument]) -> dict[Instrument, Quote]:
    requested = tuple(sorted(set(instruments), key=lambda item: item.key))
    market_symbols = {item: _market_symbol(item) for item in requested}
    quotes: dict[Instrument, Quote] = {}
    failures: list[str] = []

    for kind in InstrumentKind:
        kind_instruments = tuple(item for item in requested if item.kind == kind)
        if not kind_instruments:
            continue
        frame = None
        primary_error = None
        try:
            frame = _normalize_frame(
                _retry(lambda kind=kind: _load_frame(kind), "eastmoney"), kind.value
            )
        except MarketDataError as error:
            primary_error = str(error)

        for instrument in kind_instruments:
            errors: list[str] = []
            if frame is not None:
                try:
                    quotes[instrument] = _primary_quote(frame, instrument)
                    continue
                except MarketDataError as error:
                    errors.append(f"eastmoney: {error}")
            else:
                errors.append(f"eastmoney: {primary_error}")

            for source, load in (("tencent", _tencent_quote), ("sina", _sina_quote)):
                try:
                    quotes[instrument] = load(instrument, market_symbols[instrument])
                    break
                except MarketDataError as error:
                    errors.append(f"{source}: {error}")
            else:
                failures.append(f"{instrument.key}: {'; '.join(errors)}")

    if failures:
        raise MarketDataError("unable to obtain valid quotes:\n" + "\n".join(failures))
    return quotes
