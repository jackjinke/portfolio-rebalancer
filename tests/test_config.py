from decimal import Decimal

import pytest

from portfolio_rebalancer.config import ConfigError, load_holdings, load_portfolio


def test_loads_zero_cash_and_python_313_configuration(tmp_path) -> None:
    portfolio = tmp_path / "portfolio.yaml"
    portfolio.write_text(
        """
allow_additional_funds: true
assets:
  - symbol: "510300"
    kind: etf
    target_weight: 0.80
    trigger:
      type: relative
      threshold: 0.20
  - symbol: "600519"
    kind: stock
    target_weight: 0.10
    trigger:
      type: absolute
      threshold: 0.05
""".strip(),
        encoding="utf-8",
    )
    holdings = tmp_path / "holdings.yaml"
    holdings.write_text(
        """
cash: 0
positions:
  - symbol: "510300"
    kind: etf
    quantity: 1000
""".strip(),
        encoding="utf-8",
    )

    loaded_portfolio = load_portfolio(portfolio)
    loaded_holdings = load_holdings(holdings)

    assert loaded_portfolio.allow_additional_funds is True
    assert loaded_portfolio.cash_weight == Decimal("0.10")
    assert loaded_portfolio.assets[0].trigger.type == "relative"
    assert loaded_portfolio.assets[0].trigger.threshold == Decimal("0.20")
    assert loaded_portfolio.assets[1].trigger.type == "absolute"
    assert loaded_portfolio.assets[1].trigger.threshold == Decimal("0.05")
    assert loaded_holdings.cash == 0
    assert loaded_holdings.positions[0].quantity == 1000


def test_rejects_non_board_lot_quantity(tmp_path) -> None:
    holdings = tmp_path / "holdings.yaml"
    holdings.write_text(
        "cash: 0\npositions:\n  - symbol: '510300'\n    kind: etf\n    quantity: 150\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="multiple of 100"):
        load_holdings(holdings)
