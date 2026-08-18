from decimal import Decimal

import akshare as ak
import pandas as pd

from portfolio_rebalancer import market_data
from portfolio_rebalancer.domain import Instrument, InstrumentKind


def test_missing_bond_etf_uses_sina_realtime_fallback(monkeypatch) -> None:
    instrument = Instrument(kind=InstrumentKind.ETF, symbol="511090")
    primary = pd.DataFrame(
        [{"代码": "513500", "名称": "标普500ETF", "最新价": 2.4}]
    )
    fallback = pd.DataFrame(
        [{"代码": "sh511090", "名称": "30年国债ETF", "最新价": 119.503}]
    )
    monkeypatch.setattr(market_data, "_load_frame", lambda kind: primary)
    monkeypatch.setattr(
        ak,
        "fund_etf_category_sina",
        lambda symbol: fallback,
    )

    quote = market_data.fetch_quotes([instrument])[instrument]

    assert quote.price == Decimal("119.503")
    assert quote.name == "30年国债ETF"
