"""User changes to a tracked portfolio: rebalance, remove and add assets.

Every change is previewed first (weights before/after, trades, turnover and
cost at the latest closes) and stored as a new version only when the user
confirms it. The preview travels as a signed token, so the confirmed version
is exactly what was shown, and it is rejected if the portfolio changed since.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

import pandas as pd
from itsdangerous import BadSignature

from optimizacion_portafolios.analytics.tracking import business_days, last_prices
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.models.black_litterman import optimize_black_litterman
from optimizacion_portafolios.models.hrp import WeightBounds, optimize_hrp
from optimizacion_portafolios.models.rebalancing import (
    apply_bounds,
    plan_change,
    with_assets,
    without_assets,
)
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.models import Portfolio
from optimizacion_portafolios.storage.repositories import HoldingData, TransactionData
from optimizacion_portafolios.web.black_litterman.services import OBJECTIVES, VIEW_CONFIDENCE, WINDOW_YEARS, market_caps_usd
from optimizacion_portafolios.web.common.signing import serializer
from optimizacion_portafolios.web.portfolios.services import DOWNLOAD_ERRORS, FIX_PRICE_LOOKBACK_DAYS, usd_prices

_SALT = "atlas-portfolio-change"
PLAN_MAX_AGE_SECONDS = 60 * 60
DEFAULT_HRP_WINDOW_DAYS = 365
ACTIONS = ("rebalance", "remove", "add")
METHODS = {
    "rebalance": ("targets", "reoptimize"),
    "remove": ("proportional", "reoptimize"),
    "add": ("manual", "reoptimize"),
}
ACTION_REASONS = {"rebalance": "rebalance", "remove": "remove", "add": "add"}


def base_params(portfolio: Portfolio) -> dict:
    return dict(portfolio.current_version.params or {})


def bounds_for(portfolio: Portfolio) -> WeightBounds | None:
    """Weight limits of the model inputs (HRP); ``None`` when not given."""
    raw = base_params(portfolio).get("bounds") or (portfolio.versions[0].params or {}).get("bounds")
    if not raw:
        return None
    return WeightBounds(
        float(raw.get("min_weight", 0.0)),
        float(raw.get("max_weight", 1.0)),
        dict(raw.get("asset_min_weights") or {}),
        dict(raw.get("asset_max_weights") or {}),
    )


def _bounded(weights: pd.Series, bounds: WeightBounds | None) -> pd.Series:
    if bounds is None or (weights < 0).any():
        return weights / weights.sum()
    return apply_bounds(weights, bounds)


def _recent_usd_prices(tickers: tuple[str, ...], today: date, required) -> pd.DataFrame:
    """Recent USD closes of ``tickers``; ``ValueError`` if one of ``required`` has none."""
    start = today - timedelta(days=FIX_PRICE_LOOKBACK_DAYS)
    try:
        prices = usd_prices(tickers, start, today)
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los precios: {error}") from error
    missing = [ticker for ticker in required if ticker not in prices.columns]
    if missing:
        raise ValueError("No hay precios recientes para: " + ", ".join(missing))
    return prices


def closing_prices(tickers: tuple[str, ...], today: date) -> tuple[date, dict[str, float]]:
    """Latest business-day closes in USD of ``tickers`` (raises ``ValueError``)."""
    prices = _recent_usd_prices(tickers, today, tickers)
    price_day, closes = last_prices(business_days(prices.sort_index().ffill()))
    return price_day.date(), closes


# ------------------------------------------------------------- reoptimization
def _hrp_weights(portfolio: Portfolio, tickers: list[str], today: date) -> pd.Series:
    params = base_params(portfolio)
    try:
        span = (date.fromisoformat(params["data_end"]) - date.fromisoformat(params["data_start"])).days
    except (KeyError, TypeError, ValueError):
        span = DEFAULT_HRP_WINDOW_DAYS
    start = today - timedelta(days=max(span, 60))
    raw = download_prices(tuple(tickers), start, today)
    missing = [t for t in tickers if t not in raw.columns or raw[t].dropna().empty]
    if missing:
        raise ValueError("No hay historia de precios para re-optimizar: " + ", ".join(missing))
    returns = raw.loc[:, tickers].ffill().dropna().pct_change(fill_method=None).dropna()
    if len(returns) < 30:
        raise ValueError("No hay suficiente historia común para re-optimizar con HRP.")
    bounds = bounds_for(portfolio)
    result = optimize_hrp(returns, bounds.restricted_to(tickers) if bounds else None)
    return result.weights.reindex(tickers)


def _black_litterman_weights(
    portfolio: Portfolio, tickers: list[str], today: date, new_views: dict[str, float]
) -> pd.Series:
    params = base_params(portfolio)
    views = {**(params.get("views") or {}), **new_views}
    missing_views = [t for t in tickers if t not in views]
    if missing_views:
        raise ValueError("Falta la view anual de: " + ", ".join(missing_views))
    objective = params.get("objective")
    if objective not in OBJECTIVES:
        raise ValueError("El objetivo de optimización guardado no es válido.")
    market = params.get("market_benchmark") or portfolio.benchmark
    start = today - timedelta(days=int(365.25 * float(params.get("window_years") or WINDOW_YEARS)))
    prices = download_prices(tuple(tickers), start, today).ffill().dropna()
    market_prices = download_prices((market,), start, today)
    if prices.shape[1] < len(tickers) or market not in market_prices:
        raise ValueError("No hay precios suficientes para re-optimizar con Black-Litterman.")
    caps = market_caps_usd(tickers)
    result = optimize_black_litterman(
        prices=prices.loc[:, tickers],
        views={t: float(views[t]) for t in tickers},
        confidences={t: VIEW_CONFIDENCE for t in tickers},
        objective=OBJECTIVES[objective],
        risk_free_rate=float(params.get("risk_free_rate") or 0.0),
        market_caps=caps,
        market_prices=market_prices[market],
    )
    return result.weights.reindex(tickers).fillna(0.0)


def reoptimized_weights(
    portfolio: Portfolio, tickers: list[str], today: date, new_views: dict[str, float] | None = None
) -> pd.Series:
    """Weights of the portfolio's model on ``tickers`` with an updated window."""
    try:
        if portfolio.model == "hrp":
            return _hrp_weights(portfolio, tickers, today)
        return _black_litterman_weights(portfolio, tickers, today, new_views or {})
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los datos para re-optimizar: {error}") from error


# ---------------------------------------------------------------------- plans
def build_change(portfolio: Portfolio, action: str, method: str, *, removed=(), added=None, views=None, today: date | None = None) -> dict:
    """Preview of a change: the plan plus what the new version will record.

    ``added`` maps new tickers to manual weights (fractions; ignored when
    re-optimizing); ``views`` maps new tickers to annual views (Black-Litterman).
    """
    if action not in ACTIONS or method not in METHODS[action]:
        raise ValueError("Acción no válida.")
    if not portfolio.is_active:
        raise ValueError("El portafolio está archivado; no admite cambios.")
    today = today or date.today()
    version = portfolio.current_version
    current = version.target_weights
    bounds = bounds_for(portfolio)
    added = {t: float(w) for t, w in (added or {}).items()}
    views = {t: float(v) for t, v in (views or {}).items()}

    if action == "rebalance":
        tickers = list(current)
        target = (
            reoptimized_weights(portfolio, tickers, today)
            if method == "reoptimize"
            else pd.Series(current, dtype=float)
        )
    elif action == "remove":
        removed = [t for t in removed if t in current]
        if not removed:
            raise ValueError("Elige al menos un activo para eliminar.")
        tickers = [t for t in current if t not in removed]
        if not tickers:
            raise ValueError("El portafolio debe conservar al menos un activo.")
        target = (
            reoptimized_weights(portfolio, tickers, today)
            if method == "reoptimize" and len(tickers) >= 2
            else without_assets(current, removed)
        )
    else:
        new = [t for t in added if t not in current]
        if not new:
            raise ValueError("Escribe al menos un activo nuevo que no esté en el portafolio.")
        # Validate the new tickers before anything else. The download covers
        # every ticker the new version will hold, so the closing prices below
        # come from the cache instead of a second download.
        _recent_usd_prices(tuple(sorted(set(version.units) | set(current) | set(new))), today, new)
        tickers = list(current) + new
        target = (
            reoptimized_weights(portfolio, tickers, today, {t: views[t] for t in new if t in views})
            if method == "reoptimize"
            else with_assets(current, {t: added[t] for t in new})
        )

    target = _bounded(target.astype(float), bounds.restricted_to(tickers) if bounds else None)
    held = tuple(sorted(set(version.units) | set(target.index)))
    price_day, prices = closing_prices(held, today)
    if price_day < version.effective_date:
        raise ValueError("Los precios disponibles son anteriores a la versión vigente.")
    plan = plan_change(version.units, prices, target.to_dict(), portfolio.commission_bps)
    params = base_params(portfolio)
    params["tickers"] = list(target.index)
    if views:
        params["views"] = {**(params.get("views") or {}), **{t: v for t, v in views.items() if t in target.index}}
    params["change"] = {"action": action, "method": method, "removed": list(removed), "added": list(added)}
    reason = "reoptimize" if method == "reoptimize" and action == "rebalance" else ACTION_REASONS[action]
    return {
        "portfolio_id": portfolio.id,
        "version_number": version.number,
        "effective_date": price_day.isoformat(),
        "reason": reason,
        "action": action,
        "method": method,
        "params": params,
        "plan": asdict(plan),
    }


def sign_change(change: dict) -> str:
    return serializer(_SALT).dumps(change)


def load_change(token: str, portfolio: Portfolio) -> dict:
    """The signed preview, if still valid for the portfolio's current version."""
    try:
        change = serializer(_SALT).loads(token, max_age=PLAN_MAX_AGE_SECONDS)
    except BadSignature as error:
        raise ValueError("La vista previa expiró o no es válida. Vuelve a prepararla.") from error
    if change.get("portfolio_id") != portfolio.id:
        raise ValueError("La vista previa no corresponde a este portafolio.")
    if change.get("version_number") != portfolio.current_version.number:
        raise ValueError("El portafolio cambió desde la vista previa. Vuelve a prepararla.")
    return change


def confirm_change(portfolio: Portfolio, change: dict, note: str | None = None):
    """Store the previewed change as the next version."""
    if not portfolio.is_active:
        raise ValueError("El portafolio está archivado; no admite cambios.")
    plan = change["plan"]
    prices = plan["prices"]
    holdings = [
        HoldingData(ticker, float(weight), float(plan["units_after"][ticker]), float(prices[ticker]))
        for ticker, weight in plan["weights_after"].items()
    ]
    transactions = [TransactionData(**{k: trade[k] for k in ("ticker", "side", "units", "price", "amount", "cost")}) for trade in plan["trades"]]
    return repositories.add_version(
        portfolio,
        effective_date=date.fromisoformat(change["effective_date"]),
        reason=change["reason"],
        holdings=holdings,
        transactions=transactions,
        value_before=plan["value_before"],
        value_after=plan["value_after"],
        cost=plan["cost"],
        turnover=plan["turnover"],
        params=change["params"],
        note=note,
    )


def preview_view(change: dict) -> dict:
    """Template rows of a preview (weights before/after and trades)."""
    plan = change["plan"]
    tickers = sorted(set(plan["weights_before"]) | set(plan["weights_after"]), key=lambda t: -plan["weights_after"].get(t, 0.0))
    return {
        "effective_date": change["effective_date"],
        "reason": change["reason"],
        "action": change["action"],
        "method": change["method"],
        "value_before": plan["value_before"],
        "value_after": plan["value_after"],
        "cost": plan["cost"],
        "turnover": plan["turnover"],
        "weights": [
            {
                "ticker": t,
                "before": plan["weights_before"].get(t, 0.0),
                "after": plan["weights_after"].get(t, 0.0),
                "change": plan["weights_after"].get(t, 0.0) - plan["weights_before"].get(t, 0.0),
            }
            for t in tickers
        ],
        "trades": sorted(plan["trades"], key=lambda trade: (trade["side"] != "sell", -trade["amount"])),
    }

