import json
from datetime import UTC, datetime
from decimal import Decimal

from portfolio_rebalancer import cli
from portfolio_rebalancer.domain import Quote


def test_script_writes_scheduled_result(tmp_path, monkeypatch) -> None:
    portfolio = tmp_path / "portfolio.yaml"
    portfolio.write_text(
        """
allow_additional_funds: true
assets:
  - symbol: "510300"
    kind: etf
    target_weight: 0.50
    trigger:
      type: absolute
      threshold: 0.05
""".strip(),
        encoding="utf-8",
    )
    holdings = tmp_path / "holdings.yaml"
    holdings.write_text(
        "cash: 0\npositions:\n  - symbol: '510300'\n    kind: etf\n    quantity: 100\n",
        encoding="utf-8",
    )
    output = tmp_path / "plan.json"

    def fake_quotes(instruments):
        return {
            instrument: Quote(
                instrument=instrument,
                price=Decimal("4.000"),
                name="沪深300ETF",
                as_of=datetime(2026, 8, 18, tzinfo=UTC),
            )
            for instrument in instruments
        }

    monkeypatch.setattr(cli, "fetch_quotes", fake_quotes)

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

    result = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert result["status"] == "rebalance_required"
    assert result["trade_count"] == 0
    assert Decimal(result["additional_funds"]) > 0
    assert isinstance(result["projected"]["cash"], str)
