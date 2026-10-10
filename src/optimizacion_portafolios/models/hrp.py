"""Hierarchical Risk Parity optimization (skfolio)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from skfolio.cluster import HierarchicalClustering, LinkageMethod
from skfolio.exceptions import OptimizationError
from skfolio.measures import RiskMeasure
from skfolio.optimization import HierarchicalRiskParity
from skfolio.seriation import HierarchicalSeriation

from optimizacion_portafolios.analytics.statistics import TRADING_DAYS

# Daily minimum acceptable return of the Sortino ratio (0 %, as in QuantStats).
SORTINO_MIN_ACCEPTABLE_RETURN = 0.0
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class WeightBounds:
    """Long-only weight limits as fractions (0.25 = 25 %).

    Per-asset limits take precedence over the global ``min_weight``/``max_weight``.
    """

    min_weight: float = 0.0
    max_weight: float = 1.0
    asset_min_weights: Mapping[str, float] = field(default_factory=dict)
    asset_max_weights: Mapping[str, float] = field(default_factory=dict)

    def restricted_to(self, assets: list[str]) -> "WeightBounds":
        """Drop per-asset limits of assets outside ``assets`` (e.g. without data)."""
        return WeightBounds(
            self.min_weight,
            self.max_weight,
            {k: v for k, v in self.asset_min_weights.items() if k in assets},
            {k: v for k, v in self.asset_max_weights.items() if k in assets},
        )

    def resolve(self, assets: list[str]) -> tuple[pd.Series, pd.Series]:
        """Return validated per-asset minimum and maximum weights for ``assets``."""
        unknown = sorted(
            (set(self.asset_min_weights) | set(self.asset_max_weights)) - set(assets)
        )
        if unknown:
            raise ValueError(
                "Hay límites de peso para activos fuera del portafolio: "
                + ", ".join(unknown)
            )
        minimums = pd.Series(
            [self.asset_min_weights.get(asset, self.min_weight) for asset in assets],
            index=assets,
            dtype=float,
        )
        maximums = pd.Series(
            [self.asset_max_weights.get(asset, self.max_weight) for asset in assets],
            index=assets,
            dtype=float,
        )
        for asset in assets:
            for value in (minimums[asset], maximums[asset]):
                if not np.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(
                        f"Los límites de peso de {asset} deben estar entre "
                        "0 % y 100 %."
                    )
        inverted = minimums.index[minimums > maximums + _TOLERANCE].tolist()
        if inverted:
            raise ValueError(
                "El peso mínimo no puede superar al máximo en: " + ", ".join(inverted)
            )
        if minimums.sum() > 1 + _TOLERANCE:
            raise ValueError(
                f"La suma de los pesos mínimos ({minimums.sum():.2%}) supera el "
                "100 %; no existe un portafolio que cumpla los límites."
            )
        if maximums.sum() < 1 - _TOLERANCE:
            raise ValueError(
                f"La suma de los pesos máximos ({maximums.sum():.2%}) es menor que "
                "el 100 %; no existe un portafolio totalmente invertido que cumpla "
                "los límites."
            )
        return minimums, maximums


@dataclass(frozen=True)
class HrpResult:
    weights: pd.Series
    ordered_correlation: pd.DataFrame
    sortino_ratio: float


def _build_optimizer(
    minimums: pd.Series, maximums: pd.Series
) -> HierarchicalRiskParity:
    # Single linkage on the Pearson angular distance sqrt((1 - corr) / 2), keeping
    # the dendrogram's own leaf order (no optimal leaf ordering), reproduces the
    # previous PyPortfolioOpt HRPOpt(linkage_method="single") allocation.
    seriation = HierarchicalSeriation(
        hierarchical_clustering_estimator=HierarchicalClustering(
            linkage_method=LinkageMethod.SINGLE
        ),
        optimal_ordering=False,
    )
    return HierarchicalRiskParity(
        risk_measure=RiskMeasure.VARIANCE,
        seriation_estimator=seriation,
        min_weights=minimums.to_dict(),
        max_weights=maximums.to_dict(),
        portfolio_params={
            "name": "Portafolio HRP",
            "annualization_factor": TRADING_DAYS,
            "min_acceptable_return": SORTINO_MIN_ACCEPTABLE_RETURN,
        },
    )


def optimize_hrp(
    returns: pd.DataFrame, bounds: WeightBounds | None = None
) -> HrpResult:
    """Fit HRP and return weights, quasi-diagonal correlation and Sortino ratio.

    The Sortino ratio is skfolio's ``annualized_sortino_ratio`` of the in-sample
    portfolio: mean daily return divided by the downside deviation below 0 %
    (sample estimator), multiplied by ``sqrt(252)``.
    """
    assets = [str(column) for column in returns.columns]
    returns = returns.set_axis(assets, axis="columns")
    minimums, maximums = (bounds or WeightBounds()).resolve(assets)
    try:
        optimizer = _build_optimizer(minimums, maximums).fit(returns)
    except OptimizationError as error:
        raise ValueError(
            f"No fue posible calcular el portafolio HRP: {error}"
        ) from error
    weights = pd.Series(optimizer.weights_, index=assets, dtype=float)
    order = [assets[i] for i in optimizer.seriation_estimator_.ordering_]
    portfolio = optimizer.predict(returns)
    return HrpResult(
        weights=weights.sort_values(ascending=False),
        ordered_correlation=returns.corr().loc[order, order],
        sortino_ratio=float(portfolio.annualized_sortino_ratio),
    )


def calculate_hrp(
    returns: pd.DataFrame, bounds: WeightBounds | None = None
) -> tuple[pd.Series, pd.DataFrame]:
    """Return HRP weights and the correlation matrix in quasi-diagonal order."""
    result = optimize_hrp(returns, bounds)
    return result.weights, result.ordered_correlation


def calculate_hrp_contributions(
    returns: pd.DataFrame, weights: pd.Series
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_contributions = returns.mul(weights, axis="columns")
    summary = pd.DataFrame(index=weights.index)
    summary["Peso HRP"] = weights
    summary["Retorno anualizado"] = returns.mean() * TRADING_DAYS
    summary["Volatilidad anualizada"] = returns.std() * np.sqrt(TRADING_DAYS)
    summary["Contribución anualizada"] = daily_contributions.mean() * TRADING_DAYS
    total_return = summary["Contribución anualizada"].sum()
    summary["Participación del retorno"] = (
        summary["Contribución anualizada"] / total_return
        if total_return != 0
        else 0
    )
    return daily_contributions, summary
