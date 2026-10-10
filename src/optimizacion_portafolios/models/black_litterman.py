"""Black-Litterman allocation built on skfolio.

Pipeline (all estimation happens on DAILY simple returns, which is the
frequency skfolio expects; results are re-annualized with 252 days):

1. Covariance: ``skfolio.moments.LedoitWolf``.
2. Equilibrium prior: ``skfolio.moments.EquilibriumMu`` with the normalized
   market-cap weights and the market-implied risk aversion
   ``delta = (E[r_m] - rf) / Var(r_m)``, estimated on the benchmark. The prior
   is an excess return (``delta * Sigma * w_mkt``).
3. Posterior: ``skfolio.prior.BlackLitterman`` with one absolute view per
   asset and Idzorek's confidence method (``view_confidences``), tau = 0.05.
   Views arrive as ANNUAL total returns and are converted to DAILY excess
   returns: ``q_daily = q_annual / 252 - rf / 252``. ``risk_free_rate=rf / 252``
   adds the daily risk-free rate back to the posterior. The arithmetic
   conversion (/252) is the exact inverse of the arithmetic annualization
   (x252) used for every reported figure, so a view of 8% maps back to 8%.
4. Allocation: ``skfolio.optimization.MeanRisk`` (long-only, budget 1, except
   the explicitly unconstrained utility objective).

Scenario-based risk measures (semi-deviation, CVaR) use the historical daily
returns re-centred on the posterior mean (``r_t - mean(r) + mu_post``): the
dispersion is historical, the location is the Black-Litterman posterior. The
same scenarios feed ``skfolio.portfolio.Portfolio`` for the Sortino ratio.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import sklearn.base as skb
from skfolio.exceptions import SkfolioError
from skfolio.measures import RiskMeasure
from skfolio.moments import EquilibriumMu, LedoitWolf
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.portfolio import Portfolio
from skfolio.prior import BasePrior, BlackLitterman, EmpiricalPrior, ReturnDistribution

TRADING_DAYS = 252
TAU = 0.05
CVAR_BETA = 0.95

_OBJECTIVE_SETTINGS = {
    "max_sharpe": (ObjectiveFunction.MAXIMIZE_RATIO, RiskMeasure.STANDARD_DEVIATION),
    "max_sortino": (ObjectiveFunction.MAXIMIZE_RATIO, RiskMeasure.SEMI_DEVIATION),
    "min_variance": (ObjectiveFunction.MINIMIZE_RISK, RiskMeasure.VARIANCE),
    "min_cvar": (ObjectiveFunction.MINIMIZE_RISK, RiskMeasure.CVAR),
    "max_return": (ObjectiveFunction.MAXIMIZE_RETURN, RiskMeasure.VARIANCE),
    "max_utility": (ObjectiveFunction.MAXIMIZE_UTILITY, RiskMeasure.VARIANCE),
    "max_utility_unconstrained": (
        ObjectiveFunction.MAXIMIZE_UTILITY,
        RiskMeasure.VARIANCE,
    ),
}
OBJECTIVES = tuple(_OBJECTIVE_SETTINGS)
LONG_ONLY_OBJECTIVES = tuple(
    objective for objective in OBJECTIVES if objective != "max_utility_unconstrained"
)


@dataclass(frozen=True)
class BlackLittermanResult:
    """Annualized model outputs (returns are total returns, rf included)."""

    weights: pd.Series
    market_weights: pd.Series
    view_confidences: pd.Series
    prior_returns: pd.Series
    posterior_returns: pd.Series
    posterior_covariance: pd.DataFrame
    expected_return: float
    volatility: float
    sharpe: float
    sortino: float
    risk_aversion: float
    tau: float


class PosteriorScenarioPrior(BasePrior):
    """Black-Litterman prior whose return scenarios carry the posterior mean.

    skfolio's ``BlackLitterman`` keeps the raw historical returns as scenarios,
    so scenario-based measures (semi-deviation, CVaR) would ignore the views.
    This wrapper shifts each asset's history so its mean equals the posterior
    mean while keeping the historical dispersion.
    """

    def __init__(self, black_litterman: BlackLitterman | None = None) -> None:
        self.black_litterman = black_litterman

    def fit(self, X, y=None, **fit_params) -> "PosteriorScenarioPrior":
        self.black_litterman_ = skb.clone(self.black_litterman).fit(X, y)
        distribution = self.black_litterman_.return_distribution_
        scenarios = distribution.returns - distribution.returns.mean(axis=0)
        self.return_distribution_ = ReturnDistribution(
            mu=distribution.mu,
            covariance=distribution.covariance,
            returns=scenarios + distribution.mu,
        )
        return self


def market_implied_risk_aversion(
    market_prices: pd.Series, risk_free_rate: float
) -> float:
    """delta = (E[r_m] - rf) / Var(r_m) on daily returns (frequency invariant)."""
    market_returns = market_prices.dropna().pct_change(fill_method=None).dropna()
    if len(market_returns) < 2:
        raise ValueError("El benchmark no tiene suficientes precios válidos.")
    excess = market_returns.mean() - risk_free_rate / TRADING_DAYS
    return float(excess / market_returns.var())


def _validate_inputs(
    prices: pd.DataFrame,
    views: dict[str, float],
    confidences: dict[str, float],
    objective: str,
    market_caps: dict[str, float],
) -> list[str]:
    if prices.shape[1] < 2 or len(prices) < 3:
        raise ValueError("Se requieren al menos dos activos y tres precios válidos.")
    if not np.isfinite(prices.to_numpy()).all():
        raise ValueError("Los precios contienen valores no finitos.")
    tickers = list(prices.columns)
    if set(views) != set(tickers):
        raise ValueError("Debe especificarse exactamente una view para cada activo.")
    if any(not np.isfinite(views[ticker]) for ticker in tickers):
        raise ValueError("Las views deben ser números finitos.")
    if set(confidences) != set(tickers):
        raise ValueError("Debe especificarse una confianza para cada activo.")
    if any(not 0 < confidences[ticker] < 1 for ticker in tickers):
        raise ValueError("Las confianzas deben estar entre 0 y 1, sin incluirlos.")
    if objective not in _OBJECTIVE_SETTINGS:
        raise ValueError("El objetivo debe ser uno de: " + ", ".join(OBJECTIVES) + ".")
    if set(market_caps) != set(tickers):
        raise ValueError("Debe especificarse una capitalización para cada activo.")
    if any(
        not np.isfinite(market_caps[ticker]) or market_caps[ticker] <= 0
        for ticker in tickers
    ):
        raise ValueError("Las capitalizaciones deben ser números positivos.")
    return tickers


def optimize_black_litterman(
    prices: pd.DataFrame,
    views: dict[str, float],
    confidences: dict[str, float],
    objective: str,
    risk_free_rate: float,
    market_caps: dict[str, float],
    market_prices: pd.Series,
    tau: float = TAU,
) -> BlackLittermanResult:
    """Fit the skfolio Black-Litterman prior and optimize with ``MeanRisk``.

    ``views`` are annual expected total returns, ``risk_free_rate`` is annual
    and ``market_caps`` are capitalizations in a common currency (USD).
    """
    prices = prices.dropna(axis="columns", how="all").dropna()
    tickers = _validate_inputs(prices, views, confidences, objective, market_caps)

    risk_aversion = market_implied_risk_aversion(market_prices, risk_free_rate)
    if not np.isfinite(risk_aversion) or risk_aversion <= 0:
        raise ValueError(
            "No se pudo estimar una aversión al riesgo positiva con el benchmark. "
            "Prueba otro benchmark."
        )

    returns = prices.pct_change(fill_method=None).dropna()
    daily_rf = risk_free_rate / TRADING_DAYS
    caps = np.array([market_caps[ticker] for ticker in tickers], dtype=float)
    market_weights = caps / caps.sum()
    confidence_values = [confidences[ticker] for ticker in tickers]

    # Aliases keep views parseable for tickers such as BTC-USD, GC=F or ^GSPC.
    aliases = [f"asset{position}" for position in range(len(tickers))]
    view_equations = [
        f"{alias} = {views[ticker] / TRADING_DAYS - daily_rf:.15f}"
        for alias, ticker in zip(aliases, tickers)
    ]
    equilibrium_prior = EmpiricalPrior(
        mu_estimator=EquilibriumMu(
            risk_aversion=risk_aversion,
            weights=market_weights,
            covariance_estimator=LedoitWolf(),
        ),
        covariance_estimator=LedoitWolf(),
    )
    black_litterman = BlackLitterman(
        views=view_equations,
        groups=[aliases],
        prior_estimator=equilibrium_prior,
        tau=tau,
        view_confidences=confidence_values,
        risk_free_rate=daily_rf,
    )

    objective_function, risk_measure = _OBJECTIVE_SETTINGS[objective]
    long_only = objective in LONG_ONLY_OBJECTIVES
    optimizer = MeanRisk(
        objective_function=objective_function,
        risk_measure=risk_measure,
        # Quadratic utility w'mu - (delta / 2) w'Sigma w: the same reverse
        # optimization that produced the equilibrium prior.
        risk_aversion=risk_aversion / 2,
        prior_estimator=PosteriorScenarioPrior(black_litterman=black_litterman),
        min_weights=0.0 if long_only else None,
        max_weights=1.0 if long_only else None,
        budget=1.0,
        risk_free_rate=daily_rf,
        cvar_beta=CVAR_BETA,
    )
    try:
        optimizer.fit(returns)
    except (SkfolioError, ValueError) as error:
        raise ValueError(
            f"La optimización Black-Litterman no encontró solución: {error}"
        ) from error

    fitted_prior = optimizer.prior_estimator_
    distribution = fitted_prior.return_distribution_
    equilibrium = fitted_prior.black_litterman_.prior_estimator_.return_distribution_
    weights = pd.Series(optimizer.weights_, index=tickers, dtype=float)
    posterior_mu = pd.Series(distribution.mu, index=tickers)
    posterior_cov = pd.DataFrame(
        distribution.covariance, index=tickers, columns=tickers
    )

    expected_return = float(weights @ posterior_mu) * TRADING_DAYS
    volatility = float(np.sqrt(weights @ posterior_cov @ weights * TRADING_DAYS))
    portfolio = Portfolio(
        X=pd.DataFrame(distribution.returns, index=returns.index, columns=tickers),
        weights=weights.to_numpy(),
        risk_free_rate=daily_rf,
        annualization_factor=TRADING_DAYS,
    )
    return BlackLittermanResult(
        weights=weights,
        market_weights=pd.Series(market_weights, index=tickers),
        view_confidences=pd.Series(confidence_values, index=tickers, dtype=float),
        prior_returns=pd.Series(
            (equilibrium.mu + daily_rf) * TRADING_DAYS, index=tickers
        ),
        posterior_returns=posterior_mu * TRADING_DAYS,
        posterior_covariance=posterior_cov * TRADING_DAYS,
        expected_return=expected_return,
        volatility=volatility,
        sharpe=(expected_return - risk_free_rate) / volatility,
        sortino=float(portfolio.annualized_sortino_ratio),
        risk_aversion=risk_aversion,
        tau=tau,
    )
