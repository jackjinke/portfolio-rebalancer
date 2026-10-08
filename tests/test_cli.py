import json
from decimal import Decimal
from http.client import RemoteDisconnected

import akshare as ak
import pandas as pd
import pytest

from portfolio_rebalancer import cli


@pytest.mark.parametrize(
    ("kind", "symbol", "primary_down", "fallback_down"),
    [
        ("etf", "510300", False, True),
        ("etf", "510300", True, False),
        ("etf", "510300", True, True),
        ("stock", "600519", True, False),
    ],
)
def test_script_writes_scheduled_result(
    tmp_path, monkeypatch, capsys, kind, symbol, primary_down, fallback_down
) -> None:
    portfolio = tmp_path / "portfolio.yaml"
    portfolio.write_text(
        f"""
allow_additional_funds: true
assets:
  - symbol: "{symbol}"
    kind: {kind}
    target_weight: 0.50
    trigger:
      type: absolute
      threshold: 0.05
""".strip(),
        encoding="utf-8",
    )
    holdings = tmp_path / "holdings.yaml"
    holdings.write_text(
        f"cash: 0\npositions:\n  - symbol: '{symbol}'\n    kind: {kind}\n    quantity: 100\n",
        encoding="utf-8",
    )
    output = tmp_path / "plan.json"

    def primary_quotes():
        if primary_down:
            raise RemoteDisconnected("Eastmoney disconnected")
        return pd.DataFrame([{"代码": symbol, "最新价": 4.0}])

    def fallback_quotes(symbol):
        if fallback_down:
            raise ConnectionError("Sina disconnected")
        return pd.DataFrame([{"代码": "sh510300", "最新价": 4.0}])

    monkeypatch.setattr(ak, "fund_etf_spot_em", primary_quotes)
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", primary_quotes)
    monkeypatch.setattr(ak, "fund_etf_category_sina", fallback_quotes)

    exit_code = cli.run(
        [
            "--portfolio",
            str(portfolio),
            "--holdings",
            str(holdings),
            "--output",
            str(output),
        ]
    )

    if primary_down and (fallback_down or kind == "stock"):
        assert exit_code == 2
        assert not output.exists()
        error = capsys.readouterr().err
        assert "Eastmoney disconnected" in error
        if kind == "etf":
            assert "Sina disconnected" in error
        return

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "rebalance_required"
    assert result["trade_count"] == 0
    assert Decimal(result["additional_funds"]) == Decimal("400")
    assert Decimal(result["projected"]["cash"]) == Decimal("400")
