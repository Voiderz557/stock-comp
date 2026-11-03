from datetime import datetime, timezone
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from api import ticker_opinion as opinion, public_app
from api.auth import Principal, require_principal

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

def frames():
    index = pd.bdate_range(end="2026-11-02", periods=260)
    close = np.linspace(100, 160, len(index))
    frame = pd.DataFrame({"Close": close, "Open": close, "High": close + 1, "Low": close - 1, "Volume": 1_000_000}, index=index)
    return {key: frame.copy() for key in ("AAPL", "SPY", "QQQ")}

def test_opinion_uses_all_live_strategies_without_mutating_frames():
    data = frames(); original = deepcopy(data)
    output = opinion.build_ticker_opinion("aapl", loader=lambda *args: (data, {}), now=NOW)
    assert output["ticker"] == "AAPL"
    assert len(output["strategies"]) == len(opinion.live_strategy_names())
    assert output["price_as_of"] == "2026-11-02"
    assert output["regime"] is not None
    assert any(row["signal"] == "BUY" for row in output["strategies"])
    assert "quantity" not in output and "allocation" not in output
    for key in data:
        pd.testing.assert_frame_equal(data[key], original[key])

def test_future_prices_do_not_change_opinion():
    data = frames()
    first = opinion.build_ticker_opinion("AAPL", loader=lambda *args: (data, {}), now=NOW)
    for frame in data.values():
        frame.loc[pd.Timestamp("2026-11-04")] = [999999] * 5
    assert opinion.build_ticker_opinion("AAPL", loader=lambda *args: (data, {}), now=NOW) == first

@pytest.mark.parametrize("kind", ["missing", "stale", "short", "mismatched"])
def test_unavailable_history_never_fabricates_signal(kind):
    data = frames()
    if kind == "missing": data.pop("AAPL")
    if kind == "stale": data = {key: frame.iloc[:-20] for key, frame in data.items()}
    if kind == "short": data["AAPL"] = data["AAPL"].tail(2)
    if kind == "mismatched": data["AAPL"] = data["AAPL"].iloc[:-1]
    output = opinion.build_ticker_opinion("AAPL", loader=lambda *args: (data, {}), now=NOW)
    assert all(row["signal"] == "UNAVAILABLE" and row["score"] is None for row in output["strategies"])
    assert output["warnings"]

def test_bad_symbol_before_loading_and_concurrent_worker_guard():
    def forbidden(*args): pytest.fail("Must not load data")
    with pytest.raises(ValueError): opinion.build_ticker_opinion("../secret", loader=forbidden)
    opinion._gate.acquire()
    try:
        with pytest.raises(opinion.OpinionBusy): opinion.build_ticker_opinion("AAPL", loader=forbidden)
    finally: opinion._gate.release()

def test_route_authenticated_read_only_and_errors_redacted(monkeypatch):
    monkeypatch.setenv("STOCK_COMP_SUPABASE_URL", "https://example.supabase.co")
    app = public_app.create_app()
    monkeypatch.setenv("STOCK_COMP_API_DEMO_MODE", "false")
    with TestClient(app) as client:
        assert client.get("/v1/research/opinion/AAPL").status_code == 401
        app.dependency_overrides[require_principal] = lambda: Principal(user_id="test", access_token="test-token")
        monkeypatch.setattr(public_app, "build_ticker_opinion", lambda ticker: {"ticker": ticker})
        assert client.get("/v1/research/opinion/AAPL").json() == {"ticker": "AAPL"}
        def fail(ticker): raise RuntimeError("PRIVATE_TOKEN")
        monkeypatch.setattr(public_app, "build_ticker_opinion", fail)
        response = client.get("/v1/research/opinion/AAPL")
        assert response.status_code == 503 and "PRIVATE_TOKEN" not in response.text
