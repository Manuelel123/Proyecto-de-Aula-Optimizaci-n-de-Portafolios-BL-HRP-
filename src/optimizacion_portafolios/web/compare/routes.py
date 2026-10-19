"""Comparison page: HRP and Black-Litterman on the same assets."""

from flask import flash, render_template, request

from optimizacion_portafolios.data.catalogs import ACTIVOS_HRP
from optimizacion_portafolios.web.auth.permissions import can
from optimizacion_portafolios.web.common import guided
from optimizacion_portafolios.web.common.forms import universes_payload
from optimizacion_portafolios.web.compare import bp, services
from optimizacion_portafolios.web.portfolios.snapshots import (
    black_litterman_snapshot,
    hrp_snapshot,
    sign,
)

_CATALOG_LABELS = {ticker: label for label, ticker in ACTIVOS_HRP.items()}


def _snapshots(comparison: dict, universe_name: str) -> None:
    """Signed "fix this portfolio" tokens of each successful method."""
    if not can("fix_portfolio"):
        return
    methods = comparison["methods"]
    if "hrp" in comparison["ok"]:
        methods["hrp"]["snapshot"] = sign(
            hrp_snapshot(
                methods["hrp"]["result"],
                universe=universe_name,
                start_date=guided.guided_start().isoformat(),
            )
        )
    if "black_litterman" in comparison["ok"]:
        methods["black_litterman"]["snapshot"] = sign(
            black_litterman_snapshot(
                methods["black_litterman"]["result"],
                universe=universe_name,
                objective=guided.GUIDED_OBJECTIVE,
                benchmark=guided.GUIDED_BENCHMARK,
                risk_free_rate=guided.GUIDED_RISK_FREE_PCT / 100,
            )
        )


@bp.route("/comparar", methods=["GET", "POST"])
def compare():
    universes = guided.GUIDED_UNIVERSES
    universe_name, available, selected = guided.guided_selection(universes)
    labels = guided.asset_labels(universes[universe_name])
    max_weight = guided.GUIDED_MAX_WEIGHT_PCT
    comparison = None
    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            max_weight = guided.guided_max_weight()
            if len(selected) < 2:
                raise ValueError("Deja al menos dos activos para comparar.")
            comparison = services.compare(selected, max_weight, {**_CATALOG_LABELS, **labels})
        except ValueError as error:
            flash(str(error), "error")
        else:
            for method in services.METHODS:
                if "error" in comparison["methods"][method]:
                    flash(f"{services.METHOD_LABELS[method]}: {comparison['methods'][method]['error']}", "error")
            if not comparison["ok"]:
                comparison = None
            else:
                _snapshots(comparison, universe_name)
    return render_template(
        "compare/compare.html",
        universes=universes,
        universe_names=list(universes),
        universe_name=universe_name,
        universe_descriptions=guided.UNIVERSE_DESCRIPTIONS,
        universes_payload=universes_payload(universes),
        available_tickers=available,
        selected_tickers=selected,
        asset_labels=labels,
        max_weight=f"{max_weight:g}",
        start_date=guided.guided_start().isoformat(),
        benchmark_name=guided.GUIDED_BENCHMARK_NAME,
        objective_name=guided.GUIDED_OBJECTIVE,
        glossary=guided.glossary(guided.GUIDED_TERMS + ("Equilibrio de mercado", "View")),
        comparison=comparison,
    )
