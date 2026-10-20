"""Numerical safety net: outputs of the calculation code on deterministic data.

Every case builds synthetic prices (fixed calendar and seeds, independent of
today's date), runs a model, analytics function or page service and reduces
the outcome to plain JSON (floats at full precision, long series summarized
by length, ends, sum, sum of squares, min and max; HTML/JSON payloads by
SHA-256). ``tests/test_golden.py`` compares them with ``tests/golden/values.json``.

The values pin the current behaviour: a refactor must leave them unchanged.
After an *intended* numerical change regenerate (and review the diff)::

    uv run python tests/golden_cases.py --update
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import zlib
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

GOLDEN_FILE = Path(__file__).parent / "golden" / "values.json"
_CALENDAR = pd.bdate_range("2022-01-03", "2024-12-31", name="Fecha")
_CRYPTO_CALENDAR = pd.date_range("2022-01-01", "2024-12-31", freq="D", name="Fecha")
START = date(2023, 1, 2)
HRP_TICKERS = ["AAA", "BBB", "CCC", "DDD", "BTC-USD"]
BL_TICKERS = ["AAA", "BBB", "CCC", "DDD"]
BENCHMARK = "SPY"


# ----------------------------------------------------------------- synthetic data
def synthetic_series(ticker: str) -> pd.Series:
    """Deterministic random walk per ticker (crypto trades every day)."""
    calendar = _CRYPTO_CALENDAR if ticker.endswith("-USD") else _CALENDAR
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))
    drift = 0.0002 + (zlib.crc32(ticker.encode()) % 7) * 0.0001
    vol = 0.008 + (zlib.crc32(ticker.encode()) % 5) * 0.004
    returns = rng.normal(drift, vol, size=len(calendar))
    return pd.Series(100 * np.cumprod(1 + returns), index=calendar, name=ticker)


def synthetic_prices(tickers, start=None, end=None) -> pd.DataFrame:
    frame = pd.concat([synthetic_series(t) for t in tickers], axis=1, sort=True)
    frame.index.name = "Fecha"
    if start is not None:
        frame = frame.loc[frame.index >= pd.Timestamp(start)]
    if end is not None:
        frame = frame.loc[frame.index <= pd.Timestamp(end)]
    return frame


def _fake_download(tickers, start, end):
    return synthetic_prices(tuple(tickers), start, end)


def _fake_info(ticker: str) -> dict:
    return {"marketCap": 1e9 * (1 + zlib.crc32(ticker.encode()) % 50), "currency": "USD", "quoteType": "EQUITY"}


class synthetic_market:
    """Context manager: market data served from ``synthetic_prices`` (empty caches)."""

    def __enter__(self):
        from optimizacion_portafolios.data import market_data
        from optimizacion_portafolios.models import hrp_backtest

        market_data.clear_price_cache()
        hrp_backtest.clear_backtest_cache()
        self._patches = [
            patch.object(market_data, "_download_prices_persistent", side_effect=_fake_download),
            patch.object(market_data, "_ticker_info", side_effect=_fake_info),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        from optimizacion_portafolios.data import market_data
        from optimizacion_portafolios.models import hrp_backtest

        for item in reversed(self._patches):
            item.stop()
        market_data.clear_price_cache()
        hrp_backtest.clear_backtest_cache()
        return False


# ------------------------------------------------------------------- reducers
def num(value):
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    return None if math.isnan(value) else ("inf" if math.isinf(value) else value)


def series_values(series: pd.Series) -> dict:
    return {str(k.date() if hasattr(k, "date") else k): num(v) for k, v in series.items()}


def frame_values(frame: pd.DataFrame) -> dict:
    return {str(column): series_values(frame[column]) for column in frame.columns}


def summary(series: pd.Series) -> dict:
    values = pd.to_numeric(pd.Series(series), errors="coerce")
    clean = values.dropna()
    first = values.index[0] if len(values) else None
    last = values.index[-1] if len(values) else None
    return {
        "n": len(values),
        "nan": int(values.isna().sum()),
        "first": str(first.date() if hasattr(first, "date") else first),
        "last": str(last.date() if hasattr(last, "date") else last),
        "sum": num(clean.sum()),
        "sumsq": num((clean**2).sum()),
        "min": num(clean.min()) if len(clean) else None,
        "max": num(clean.max()) if len(clean) else None,
        "last_value": num(values.iloc[-1]) if len(values) else None,
    }


def frame_summary(frame: pd.DataFrame) -> dict:
    return {str(column): summary(frame[column]) for column in frame.columns}


def digest(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(str(text).encode()).hexdigest()


def plain(value):
    """JSON-safe copy of nested dicts/lists of numbers and strings."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, str):
        return value
    return num(value)


# ---------------------------------------------------------------------- cases
def case_hrp_model() -> dict:
    from optimizacion_portafolios.models.hrp import WeightBounds, calculate_hrp_contributions, optimize_hrp

    returns = synthetic_prices(HRP_TICKERS[:4]).pct_change(fill_method=None).dropna()
    free = optimize_hrp(returns)
    bounded = optimize_hrp(returns, WeightBounds(min_weight=0.1, max_weight=0.35, asset_max_weights={"AAA": 0.2}))
    _, contributions = calculate_hrp_contributions(returns, free.weights)
    return {
        "weights": series_values(free.weights),
        "sortino": num(free.sortino_ratio),
        "ordered_correlation": frame_values(free.ordered_correlation),
        "bounded_weights": series_values(bounded.weights),
        "contributions": frame_values(contributions),
    }


def case_black_litterman_model() -> dict:
    from optimizacion_portafolios.models.black_litterman import optimize_black_litterman

    prices = synthetic_prices(BL_TICKERS, START)
    market = synthetic_series(BENCHMARK).loc[prices.index]
    caps = {t: _fake_info(t)["marketCap"] for t in BL_TICKERS}
    views = {"AAA": 0.08, "BBB": 0.12, "CCC": 0.05, "DDD": 0.10}
    out = {}
    for objective in ("max_sharpe", "min_variance", "max_utility_unconstrained"):
        result = optimize_black_litterman(
            prices=prices,
            views=views,
            confidences={t: 0.95 for t in BL_TICKERS},
            objective=objective,
            risk_free_rate=0.02,
            market_caps=caps,
            market_prices=market,
        )
        out[objective] = {
            "weights": series_values(result.weights),
            "market_weights": series_values(result.market_weights),
            "prior": series_values(result.prior_returns),
            "posterior": series_values(result.posterior_returns),
            "posterior_covariance": frame_values(result.posterior_covariance),
            "expected_return": num(result.expected_return),
            "volatility": num(result.volatility),
            "sharpe": num(result.sharpe),
            "sortino": num(result.sortino),
            "risk_aversion": num(result.risk_aversion),
        }
    return out


def case_performance() -> dict:
    from optimizacion_portafolios.analytics.performance import calculate_quantstats_metrics, calculate_strategy_metrics

    returns = synthetic_prices(["AAA"])["AAA"].pct_change(fill_method=None).dropna()
    values = synthetic_prices(["BBB"])["BBB"] * 100
    return {
        "quantstats": series_values(calculate_quantstats_metrics(returns)["Valor"]),
        "strategy": plain(calculate_strategy_metrics(values, 10_000.0)),
    }


def case_statistics_volatility() -> dict:
    from optimizacion_portafolios.analytics.statistics import calculate_expected_returns, calculate_statistics
    from optimizacion_portafolios.analytics.volatility import calculate_historical_volatility, calculate_monthly_volatility

    prices = synthetic_prices(HRP_TICKERS).ffill().dropna()
    returns = prices.pct_change(fill_method=None).dropna(how="all")
    period, annual = calculate_historical_volatility(returns, "Mensual", prices.index.min(), prices.index.max())
    return {
        "statistics": frame_values(calculate_statistics(prices)),
        "expected": frame_values(calculate_expected_returns(prices)),
        "monthly_volatility": frame_summary(calculate_monthly_volatility(returns)),
        "period_volatility": frame_summary(period),
        "annualized_volatility": frame_summary(annual),
    }


def case_tracking() -> dict:
    from optimizacion_portafolios.analytics.tracking import (
        Segment,
        business_days,
        holding_values,
        initial_positions,
        last_prices,
        tracking_metrics,
        tracking_summary,
    )

    tickers = ("AAA", "BBB", "BTC-USD")
    prices = synthetic_prices(tickers, START)
    start = pd.Timestamp(START)
    _, closes = last_prices(business_days(prices.ffill()), start)
    weights = {"AAA": 0.5, "BBB": 0.3, "BTC-USD": 0.2}
    positions = initial_positions(100_000.0, weights, closes, 10.0)
    change = pd.Timestamp("2024-03-01")
    _, closes_change = last_prices(business_days(prices.ffill()), change)
    value_change = sum(positions.units[t] * closes_change[t] for t in tickers)
    units2 = {t: value_change / 3 / closes_change[t] for t in tickers}
    segments = [Segment(start, positions.units, "fix"), Segment(change, units2, "rebalance")]
    holdings = business_days(holding_values(prices, segments))
    total = holdings.sum(axis=1).rename("Portafolio")
    benchmark = synthetic_series(BENCHMARK)
    benchmark = benchmark.loc[benchmark.index >= start] / benchmark.loc[benchmark.index >= start].iloc[0] * 100_000.0
    metrics = tracking_metrics(
        total,
        holdings,
        business_days(prices.ffill()).reindex(holdings.index),
        business_days(benchmark),
        {t: 1 / 3 for t in tickers},
        rf=0.02,
        turnover=0.25,
        raw_prices=business_days(prices),
    )
    series = metrics["series"]
    return {
        "positions": plain({"units": positions.units, "cost": positions.cost, "invested": positions.invested}),
        "total": summary(total),
        "summary": plain(tracking_summary(total, benchmark, 100_000.0)),
        "scalars": plain(metrics["scalars"]),
        "weights": series_values(metrics["weights"]),
        "drift": series_values(metrics["drift"]),
        "return_contributions": series_values(metrics["return_contributions"]),
        "risk_contributions": series_values(metrics["risk_contributions"]),
        "series": {
            "returns": summary(series["returns"]),
            "benchmark_returns": summary(series["benchmark_returns"]),
            "drawdown": summary(series["drawdown"]),
            "rolling_sharpe": summary(series["rolling_sharpe"]),
            "average_correlation": summary(series["average_correlation"]),
            **{f"rolling_volatility_{w}": summary(v) for w, v in series["rolling_volatility"].items()},
        },
        "context": plain(metrics["context"]),
    }


def case_backtest() -> dict:
    from optimizacion_portafolios.models.hrp import WeightBounds
    from optimizacion_portafolios.models.hrp_backtest import BacktestConfig, run_backtest

    prices = synthetic_prices(BL_TICKERS).ffill().dropna()
    result = run_backtest(
        prices,
        BacktestConfig(lookback=126, vol_window=21),
        WeightBounds(max_weight=0.6),
        START,
    )
    return {
        "metrics": {str(label): plain(row.to_dict()) for label, row in result.metrics.iterrows()},
        "values": frame_summary(result.values),
        "rebalances": {label: [d.date().isoformat() for d in dates] for label, dates in result.rebalance_dates.items()},
        "start": result.start.date().isoformat(),
        "end": result.end.date().isoformat(),
    }


def case_signals() -> dict:
    from optimizacion_portafolios.models.signals import SignalConfig, run_signals

    prices = synthetic_prices(BL_TICKERS[:3])
    result = run_signals(prices, {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}, 50_000.0, pd.Timestamp(START), SignalConfig())
    return {
        "metrics": {str(label): plain(row.to_dict()) for label, row in result.metrics.iterrows()},
        "values": frame_summary(result.values),
        "trades": plain(result.trades),
        "suggestions": plain(result.suggestions),
    }


def _capture_matrix(module):
    captured = {}

    def fake(series, names):
        captured["series"] = {str(k): summary(v) for k, v in series.items()}
        captured["names"] = {str(k): str(v) for k, v in names.items()}
        return None

    return captured, patch.object(module, "returns_matrix_chart", side_effect=fake)


def case_hrp_page() -> dict:
    from optimizacion_portafolios.models.hrp import WeightBounds
    from optimizacion_portafolios.models.hrp_backtest import BacktestConfig
    from optimizacion_portafolios.web.hrp import services

    captured, matrix = _capture_matrix(services)
    with synthetic_market(), matrix:
        result = services.run_hrp(
            HRP_TICKERS,
            BENCHMARK,
            START,
            WeightBounds(max_weight=0.4),
            {"AAA": "Alpha (AAA)"},
            backtest=BacktestConfig(lookback=126, vol_window=21),
        )
    backtest = result["backtest"]
    return {
        "summary": plain(result["summary"]),
        "allocation_rows": plain(result["allocation_rows"]),
        "asset_stats": plain(result["asset_stats"]),
        "scalars": plain({k: result[k] for k in (
            "start", "end", "observations", "common_observations", "weight_sum", "min_assigned_weight",
            "max_assigned_weight", "min_weight_ticker", "max_weight_ticker", "equal_weight", "top_risk_ticker",
            "top_risk_share", "sortino_ratio", "missing", "has_rolling_sharpe", "selected_tickers")}),
        "tables": {k: digest(v) for k, v in result["tables"].items()},
        "prices_payload": digest(result["prices_payload"]),
        "returns_matrix": captured,
        "backtest": {
            "record": plain(backtest["record"]),
            "metrics_table": digest(backtest["metrics_table"]),
            "start": backtest["start"],
            "end": backtest["end"],
            "days": backtest["days"],
        },
    }


def case_black_litterman_page() -> dict:
    from optimizacion_portafolios.web.black_litterman import services

    captured, matrix = _capture_matrix(services)
    views = {"AAA": 0.08, "BBB": 0.12, "CCC": 0.05, "DDD": 0.10}
    with synthetic_market(), matrix:
        result = services.run_black_litterman(
            BL_TICKERS, views, "Máximo ratio de Sharpe", 0.02, BENCHMARK, START,
            {"AAA": "Alpha (AAA)"}, False, benchmark_label="S&P 500",
        )
    keep = ("expected_return", "volatility", "sharpe", "sortino", "risk_aversion", "tau", "weights_sum",
            "market_weights_sum", "has_negative_weights", "n_assets", "allocation_rows", "prior_rows",
            "assumption_rows", "prior_returns", "missing_prices", "included_tickers")
    window = dict(result["window"])
    for key in ("end",):  # depends on today's date
        window.pop(key, None)
    return {
        **{key: plain(result[key]) for key in keep},
        "window": plain(window),
        "allocation_csv": digest(result["allocation_csv"]),
        "covariance_table": digest(result["covariance_table"]),
        "prices_payload": digest(result["prices_payload"]),
        "history": {
            "metrics": digest(result["history"]["metrics"]),
            "max_drawdown": num(result["history"]["max_drawdown"]),
            "observations": result["history"]["observations"],
        },
        "returns_matrix": captured,
    }


def case_monitoring_page() -> dict:
    from optimizacion_portafolios.web.monitoring import services

    out = {}
    for view in ("portfolio", "options"):
        captured, matrix = _capture_matrix(services)
        with synthetic_market(), matrix:
            result = services.build_price_analysis(view, HRP_TICKERS, START, "Mensual")
        out[view] = {
            "tables": {k: digest(v) for k, v in result["tables"].items()},
            "prices_payload": digest(result["prices_payload"]),
            "scalars": plain({k: result[k] for k in ("tickers", "missing", "start", "end", "observations", "has_period_volatility")}),
            "returns_matrix": captured,
        }
    return out


def case_compare_metrics() -> dict:
    from optimizacion_portafolios.web.compare import services

    with synthetic_market():
        return plain(services._historical_metrics({"AAA": 0.4, "BBB": 0.35, "BTC-USD": 0.25}, START))


CASES = {
    "hrp_model": case_hrp_model,
    "black_litterman_model": case_black_litterman_model,
    "performance": case_performance,
    "statistics_volatility": case_statistics_volatility,
    "tracking": case_tracking,
    "backtest": case_backtest,
    "signals": case_signals,
    "hrp_page": case_hrp_page,
    "black_litterman_page": case_black_litterman_page,
    "monitoring_page": case_monitoring_page,
    "compare_metrics": case_compare_metrics,
}


def compute_all() -> dict:
    return {name: json.loads(json.dumps(case())) for name, case in CASES.items()}


if __name__ == "__main__":
    if "--update" not in sys.argv:
        sys.exit("Pass --update to regenerate tests/golden/values.json")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    GOLDEN_FILE.parent.mkdir(exist_ok=True)
    GOLDEN_FILE.write_text(json.dumps(compute_all(), indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {GOLDEN_FILE}")
