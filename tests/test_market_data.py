from datetime import datetime
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from portfolio_rebalancer import market_data
from portfolio_rebalancer.domain import Instrument, InstrumentKind


@pytest.fixture
def transport(monkeypatch):
    primary_calls = []
    urls = []
    payloads = []

    def load_frame(kind):
        primary_calls.append(kind)
        return pd.DataFrame(columns=["代码", "名称", "最新价", "更新时间"])

    def urlopen(request, *, timeout):
        urls.append(request.full_url)
        assert timeout == 8
        assert payloads, f"Unexpected HTTP request: {request.full_url}"
        return BytesIO(payloads.pop(0).encode("gb18030"))

    monkeypatch.setattr(market_data, "_load_frame", load_frame)
    monkeypatch.setattr(market_data, "urlopen", urlopen)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(market_data, "sleep", lambda seconds: None)
    return primary_calls, urls, payloads


@pytest.mark.parametrize(
    ("kind", "symbol", "exchange"),
    [
        (InstrumentKind.ETF, "150001", "sz"),
        (InstrumentKind.ETF, "510300", "sh"),
        (InstrumentKind.ETF, "520001", "sh"),
        (InstrumentKind.ETF, "530001", "sh"),
        (InstrumentKind.ETF, "560001", "sh"),
        (InstrumentKind.ETF, "580001", "sh"),
        (InstrumentKind.STOCK, "600519", "sh"),
        (InstrumentKind.STOCK, "688001", "sh"),
        (InstrumentKind.STOCK, "000001", "sz"),
        (InstrumentKind.STOCK, "300001", "sz"),
        (InstrumentKind.STOCK, "430001", "bj"),
        (InstrumentKind.STOCK, "830001", "bj"),
        (InstrumentKind.STOCK, "870001", "bj"),
        (InstrumentKind.STOCK, "920001", "bj"),
    ],
)
def test_direct_lookup_uses_supported_exchange_prefixes(transport, kind, symbol, exchange):
    primary_calls, urls, payloads = transport
    fields = [""] * 62
    fields[0:4] = ["1", "证券名称", symbol, "4.125"]
    # Deliberately historical and on a Sunday: no calendar/max-age policy applies.
    fields[30] = "20200105100000"
    fields[61] = "ETF" if kind == InstrumentKind.ETF else "GP-A"
    payloads.append(f'v_{exchange}{symbol}="{"~".join(fields)}";')
    instrument = Instrument(kind=kind, symbol=symbol)

    quote = market_data.fetch_quotes([instrument])[instrument]

    assert primary_calls == [kind]
    assert len(urls) == 1
    assert "gtimg.cn" in urls[0]
    assert f"q={exchange}{symbol}" in urls[0]
    assert quote.instrument == instrument
    assert quote.price == Decimal("4.125")
    assert quote.name == "证券名称"
    assert quote.source == "tencent"
    assert quote.as_of == datetime(2020, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert str(quote.as_of.tzinfo) == "Asia/Shanghai"


@pytest.mark.parametrize(
    ("kind", "symbol"),
    [(InstrumentKind.ETF, "160001"), (InstrumentKind.ETF, "600519"),
     (InstrumentKind.STOCK, "510300"), (InstrumentKind.STOCK, "990001")],
)
def test_unsupported_prefix_is_rejected_before_any_source_request(transport, kind, symbol):
    primary_calls, urls, _ = transport
    instrument = Instrument(kind=kind, symbol=symbol)

    with pytest.raises(market_data.MarketDataError) as caught:
        market_data.fetch_quotes([instrument])

    assert instrument.key in str(caught.value)
    assert primary_calls == []
    assert urls == []
