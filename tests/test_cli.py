import json
import shlex
from decimal import Decimal
from http.client import IncompleteRead, RemoteDisconnected

import akshare as ak
import pandas as pd
import pytest

from portfolio_rebalancer import cli, market_data


PRIMARY_TIME = "2026-08-18T14:30:00+08:00"
TENCENT_TIME = "2026-08-18T14:31:00+08:00"
SINA_TIME = "2026-08-18T14:32:00+08:00"


def tencent_payload(
    code="sh512050", *, price="4.000", security_type="ETF",
    timestamp="20260818143100", returned_code=None, variable=None,
):
    # Positions mirror Tencent's tilde-delimited single-security response.
    fields = [""] * 62
    fields[0:6] = ["1", "中证A500ETF", returned_code or code[2:], price, "3.990", "4.010"]
    fields[30] = timestamp
    fields[61] = security_type
    return f'v_{variable or code}="{"~".join(fields)}";'


def sina_payload(code="sh512050", *, price="4.000", status="00", date="2026-08-18", time="14:32:00"):
    fields = [""] * 33
    fields[0:6] = ["中证A500ETF", "4.010", "3.990", price, "4.020", "3.980"]
    fields[30:33] = [date, time, status]
    return f'var hq_str_{code}="{",".join(fields)}";'


class Response:
    def __init__(self, text):
        self.body = text.encode("gb18030")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


@pytest.fixture
def network(monkeypatch):
    """All quote traffic stays in-process, including the real HTTP parser path."""
    calls = []
    replies = {"tencent": [], "sina": []}
    sleeps = []
    primary_calls = []
    primary = pd.DataFrame(columns=["代码", "名称", "最新价", "更新时间"])

    def load_primary():
        primary_calls.append(True)
        value = primary.pop(0) if isinstance(primary, list) else primary
        if isinstance(value, Exception):
            raise value
        return value

    def urlopen(request, *, timeout):
        url = request.full_url
        calls.append(url)
        assert timeout == 8
        source = "tencent" if "gtimg.cn" in url else "sina" if "sinajs.cn" in url else None
        assert source is not None, f"Unexpected quote URL: {url}"
        assert replies[source], f"Unexpected {source} request: {url}"
        value = replies[source].pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(value)

    def set_primary(value):
        nonlocal primary
        primary = value

    monkeypatch.setattr(ak, "fund_etf_spot_em", load_primary)
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", load_primary)
    monkeypatch.setattr(market_data, "urlopen", urlopen)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(market_data, "sleep", sleeps.append)
    return set_primary, replies, calls, sleeps, primary_calls


def run_cli(tmp_path, capsys, *, assets=(("etf", "512050", "0.50"),), cash=0):
    portfolio = tmp_path / "portfolio.yaml"
    portfolio.write_text(
        "allow_additional_funds: true\nassets:\n" + "".join(
            f'  - symbol: "{symbol}"\n    kind: {kind}\n    target_weight: {weight}\n'
            "    trigger:\n      type: absolute\n      threshold: 0.05\n"
            for kind, symbol, weight in assets
        ), encoding="utf-8",
    )
    holdings = tmp_path / "holdings.yaml"
    holdings.write_text(
        f"cash: {cash}\npositions:\n" + "".join(
            f'  - symbol: "{symbol}"\n    kind: {kind}\n    quantity: 100\n'
            for kind, symbol, _ in assets
        ), encoding="utf-8",
    )
    output = tmp_path / "plan.json"
    args = ["--portfolio", str(portfolio), "--holdings", str(holdings), "--output", str(output)]
    (tmp_path / "rerun-command.txt").write_text(
        shlex.join(["uv", "run", "portfolio-rebalancer", *args]) + "\n", encoding="utf-8",
    )
    code = cli.run(args)
    captured = capsys.readouterr()
    (tmp_path / "result.json").write_text(
        json.dumps({"exit_code": code, "stdout": captured.out, "stderr": captured.err}),
        encoding="utf-8",
    )
    return code, output, captured.err


def assert_provenance(quote, *, symbol, source, as_of, name="中证A500ETF", kind="etf"):
    assert set(quote) == {"symbol", "kind", "name", "price", "source", "as_of"}
    assert quote["symbol"] == symbol
    assert quote["kind"] == kind
    assert quote["name"] == name
    assert Decimal(quote["price"]) == Decimal("4")
    assert quote["source"] == source
    assert quote["as_of"] == as_of


@pytest.mark.parametrize(("cash", "status"), [(0, "rebalance_required"), (800, "no_rebalance")])
def test_missing_512050_is_repaired_without_replacing_primary(
    tmp_path, capsys, network, cash, status,
):
    set_primary, replies, calls, _, _ = network
    set_primary(pd.DataFrame([
        {"代码": "510300", "名称": "沪深300ETF", "最新价": 4.0, "更新时间": "2026-08-18 14:30:00"},
    ]))
    replies["tencent"].append(tencent_payload())

    code, output, error = run_cli(
        tmp_path, capsys, cash=cash,
        assets=(("etf", "510300", "0.25"), ("etf", "512050", "0.25")),
    )

    assert code == 0, error
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == status
    assert result["trade_count"] == 0
    assert Decimal(result["additional_funds"]) == Decimal(800 if cash == 0 else 0)
    assert set(result["quotes"]) == {"etf:510300", "etf:512050"}
    assert_provenance(result["quotes"]["etf:510300"], symbol="510300", source="eastmoney", as_of=PRIMARY_TIME, name="沪深300ETF")
    assert_provenance(result["quotes"]["etf:512050"], symbol="512050", source="tencent", as_of=TENCENT_TIME)
    assert len(calls) == 1
    assert "q=sh512050" in calls[0]


@pytest.mark.parametrize(
    ("kind", "symbol", "timestamp"),
    [("etf", "512050", None), ("stock", "600519", None), ("etf", "512050", pd.NaT),
     ("etf", "512050", "2026-08-18"),
     ("etf", "512050", "2999-01-01T14:30:00+08:00")],
)
def test_primary_without_usable_source_time_uses_direct_quote(
    tmp_path, capsys, network, kind, symbol, timestamp,
):
    set_primary, replies, calls, _, _ = network
    row = {"代码": symbol, "名称": "主源", "最新价": 99}
    if timestamp is not None:
        row["更新时间"] = timestamp
    set_primary(pd.DataFrame([row]))
    replies["tencent"].append(tencent_payload(
        f"sh{symbol}", security_type="ETF" if kind == "etf" else "GP-A",
    ))

    code, output, error = run_cli(tmp_path, capsys, assets=((kind, symbol, "0.50"),))

    assert code == 0, error
    quote = json.loads(output.read_text(encoding="utf-8"))["quotes"][f"{kind}:{symbol}"]
    assert_provenance(quote, symbol=symbol, kind=kind, source="tencent", as_of=TENCENT_TIME)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad_tencent",
    [
        "transport",
        tencent_payload(security_type=""),
        tencent_payload(security_type="GP-A"),
        tencent_payload(returned_code="510300"),
        tencent_payload(variable="sz512050"),
        tencent_payload(price="0"),
        tencent_payload(price="NaN"),
        tencent_payload(timestamp=""),
        tencent_payload(timestamp="202608181431"),
        tencent_payload(timestamp="29990101143100"),
    ],
    ids=["transport", "empty-type", "wrong-type", "wrong-code", "wrong-exchange", "zero", "nan", "missing-time", "short-time", "future-time"],
)
def test_rejected_tencent_quote_falls_through_to_sina(tmp_path, capsys, network, bad_tencent):
    _, replies, calls, _, _ = network
    replies["tencent"].extend(
        [RemoteDisconnected("Tencent disconnected")] * 2
        if bad_tencent == "transport" else [bad_tencent]
    )
    replies["sina"].append(sina_payload())

    code, output, error = run_cli(tmp_path, capsys)

    assert code == 0, error
    quote = json.loads(output.read_text(encoding="utf-8"))["quotes"]["etf:512050"]
    assert_provenance(quote, symbol="512050", source="sina", as_of=SINA_TIME)
    assert all("gtimg.cn" in url for url in calls[:-1])
    assert "sinajs.cn" in calls[-1]
    assert "list=sh512050" in calls[-1]


@pytest.mark.parametrize(
    "failure", [RemoteDisconnected("temporary disconnect"), IncompleteRead(b"truncated", 100)],
    ids=["disconnect", "truncated-response"],
)
def test_direct_transport_retries_once_then_recovers(tmp_path, capsys, network, failure):
    _, replies, calls, sleeps, _ = network
    replies["tencent"].extend([failure, tencent_payload()])

    code, output, error = run_cli(tmp_path, capsys)

    assert code == 0, error
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert sleeps == [0.25]
    assert json.loads(output.read_text(encoding="utf-8"))["quotes"]["etf:512050"]["source"] == "tencent"


def test_primary_transport_retries_once_then_recovers(tmp_path, capsys, network):
    set_primary, _, calls, sleeps, primary_calls = network
    set_primary([
        RemoteDisconnected("temporary Eastmoney disconnect"),
        pd.DataFrame([{"代码": "512050", "名称": "中证A500ETF", "最新价": 4, "更新时间": pd.Timestamp(PRIMARY_TIME)}]),
    ])

    code, output, error = run_cli(tmp_path, capsys)

    assert code == 0, error
    assert len(primary_calls) == 2
    assert calls == []
    assert sleeps == [0.25]
    quote = json.loads(output.read_text(encoding="utf-8"))["quotes"]["etf:512050"]
    assert_provenance(quote, symbol="512050", source="eastmoney", as_of=PRIMARY_TIME)


@pytest.mark.parametrize("price", ["0", "NaN"])
@pytest.mark.parametrize("existing_output", [False, True])
def test_all_invalid_prices_fail_without_writing_plan(tmp_path, capsys, network, price, existing_output):
    set_primary, replies, _, _, _ = network
    set_primary(pd.DataFrame([{"代码": "512050", "最新价": price, "更新时间": PRIMARY_TIME}]))
    replies["tencent"].append(tencent_payload(price=price))
    replies["sina"].append(sina_payload(price=price))
    output = tmp_path / "plan.json"
    original = b'{"previous": "keep this exact output"}\n'
    if existing_output:
        output.write_bytes(original)

    code, output, error = run_cli(tmp_path, capsys)

    assert code == 2
    if existing_output:
        assert output.read_bytes() == original
    else:
        assert not output.exists()
    assert "etf:512050" in error
    for source in ("eastmoney", "tencent", "sina"):
        assert source in error.lower()


def test_all_transport_failures_report_sources_and_leave_no_plan(tmp_path, capsys, network):
    set_primary, replies, calls, sleeps, primary_calls = network
    set_primary(RemoteDisconnected("Eastmoney disconnected"))
    replies["tencent"].extend([RemoteDisconnected("Tencent disconnected")] * 2)
    replies["sina"].extend([RemoteDisconnected("Sina disconnected")] * 2)

    code, output, error = run_cli(tmp_path, capsys)

    assert code == 2
    assert not output.exists()
    assert len(primary_calls) == 2
    assert len(calls) == 4
    assert sleeps == [0.25, 0.25, 0.25]
    assert "etf:512050" in error
    for diagnostic in ("Eastmoney disconnected", "Tencent disconnected", "Sina disconnected"):
        assert diagnostic in error


@pytest.mark.parametrize(
    "payload",
    [sina_payload(status="02"), sina_payload(date="2999-01-01"), sina_payload("sz512050"), sina_payload(time="")],
    ids=["non-trading-status", "future-time", "wrong-exchange", "missing-time"],
)
def test_unusable_sina_quote_is_not_a_successful_fallback(tmp_path, capsys, network, payload):
    _, replies, _, _, _ = network
    replies["tencent"].append(tencent_payload(security_type=""))
    replies["sina"].append(payload)

    code, output, error = run_cli(tmp_path, capsys)

    assert code == 2
    assert not output.exists()
    assert "etf:512050" in error
    assert "sina" in error.lower()
