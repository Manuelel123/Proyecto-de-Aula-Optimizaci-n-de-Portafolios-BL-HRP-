"""Persistent adjusted-close cache in InsForge (``price_history`` tables).

Sits below the in-memory cache of ``market_data.download_prices``: a ticker
whose stored coverage includes the requested range and was refreshed within the
TTL (24 h by default, ``ATLAS_PRICE_DB_TTL_HOURS``; at most 1 h when the range
reaches today, whose bar may still be intraday) is read from the database; the
rest is downloaded and written back. Adjusted closes change with every dividend
or split, so stale coverage is downloaded again in full.

The tables are server-only: every call uses the project API key. The store is
optional. Without configuration, or on any InsForge failure, it is skipped and
prices come straight from the providers.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from optimizacion_portafolios.insforge import InsForgeClient, InsForgeError, in_filter

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 24.0
_INTRADAY_TTL = timedelta(hours=1)
_WRITE_CHUNK = 1000
_client: InsForgeClient | None = None
_ttl = timedelta(hours=DEFAULT_TTL_HOURS)


def configure(client: InsForgeClient | None, ttl_hours: float = DEFAULT_TTL_HOURS) -> None:
    """Enable the store with a client holding the API key (``None`` disables it)."""
    global _client, _ttl
    _client = client if client is not None and client.has_admin_key else None
    _ttl = timedelta(hours=ttl_hours)


def enabled() -> bool:
    return _client is not None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fresh_coverage(tickers: tuple[str, ...], end: date) -> dict[str, dict]:
    rows = _client.select(
        "price_coverage",
        {"ticker": in_filter(tickers), "select": "ticker,start_date,end_date,fetched_at"},
        admin=True,
    )
    ttl = min(_ttl, _INTRADAY_TTL) if end >= date.today() else _ttl
    oldest = datetime.now(timezone.utc) - ttl
    return {
        row["ticker"]: row
        for row in rows
        if _parse_timestamp(row["fetched_at"]) >= oldest
    }


def load(tickers: tuple[str, ...], start: date, end: date) -> tuple[dict[str, pd.Series], tuple[str, ...]]:
    """Cached series covering ``[start, end]`` and the tickers still to download."""
    if _client is None or not tickers:
        return {}, tuple(tickers)
    try:
        coverage = _fresh_coverage(tickers, end)
        hits = tuple(
            ticker
            for ticker in tickers
            if ticker in coverage
            and date.fromisoformat(coverage[ticker]["start_date"]) <= start
            and date.fromisoformat(coverage[ticker]["end_date"]) >= end
        )
        series: dict[str, pd.Series] = {}
        if hits:
            rows = _client.select(
                "price_history",
                {
                    "ticker": in_filter(hits),
                    "and": f"(day.gte.{start.isoformat()},day.lte.{end.isoformat()})",
                    "select": "ticker,day,close",
                    "order": "ticker.asc,day.asc",
                },
                admin=True,
            )
            if rows:
                frame = pd.DataFrame(rows)
                frame["day"] = pd.to_datetime(frame["day"])
                for ticker, group in frame.groupby("ticker", sort=False):
                    series[str(ticker)] = pd.Series(
                        group["close"].astype(float).to_numpy(),
                        index=pd.DatetimeIndex(group["day"]),
                        name=str(ticker),
                    )
    except (InsForgeError, KeyError, ValueError) as error:
        logger.warning("Price cache read failed; downloading instead: %s", error)
        return {}, tuple(tickers)
    pending = tuple(ticker for ticker in tickers if ticker not in series)
    return series, pending


def save(prices: pd.DataFrame, start: date, end: date) -> None:
    """Store downloaded closes and extend each ticker's coverage to ``[start, end]``."""
    if _client is None or prices.empty:
        return
    tickers = tuple(str(column) for column in prices.columns)
    try:
        previous = _fresh_coverage(tickers, end)
        rows = [
            {"ticker": str(ticker), "day": day.date().isoformat(), "close": float(value)}
            for ticker in prices.columns
            for day, value in prices[ticker].dropna().items()
            if value > 0
        ]
        for offset in range(0, len(rows), _WRITE_CHUNK):
            _client.insert(
                "price_history",
                rows[offset : offset + _WRITE_CHUNK],
                admin=True,
                upsert=True,
                on_conflict="ticker,day",
                returning=False,
            )
        now = datetime.now(timezone.utc).isoformat()
        coverage = []
        for ticker in tickers:
            new_start, new_end = start, end
            old = previous.get(ticker)
            if old:
                old_start = date.fromisoformat(old["start_date"])
                old_end = date.fromisoformat(old["end_date"])
                # Fresh, overlapping (or adjacent) coverage is merged, not replaced.
                if old_start <= end + timedelta(days=1) and start <= old_end + timedelta(days=1):
                    new_start, new_end = min(start, old_start), max(end, old_end)
            coverage.append(
                {
                    "ticker": ticker,
                    "start_date": new_start.isoformat(),
                    "end_date": new_end.isoformat(),
                    "fetched_at": now,
                }
            )
        _client.insert(
            "price_coverage",
            coverage,
            admin=True,
            upsert=True,
            on_conflict="ticker",
            returning=False,
        )
    except (InsForgeError, ValueError) as error:
        logger.warning("Price cache write failed: %s", error)
