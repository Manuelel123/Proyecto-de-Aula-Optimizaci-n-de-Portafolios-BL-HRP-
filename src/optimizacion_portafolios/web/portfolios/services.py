"""View models of the "Mis portafolios" pages."""

from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.models import Portfolio

MODEL_LABELS = {"hrp": "HRP", "black_litterman": "Black-Litterman"}
STATUS_LABELS = {"active": "Activo", "archived": "Archivado"}
REASON_LABELS = {
    "fix": "Fijado como óptimo",
    "rebalance": "Rebalanceo",
    "reoptimize": "Re-optimización",
    "remove": "Eliminación de activos",
    "add": "Alta de activos",
}


def _summary(portfolio: Portfolio) -> dict:
    version = portfolio.current_version
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "model": MODEL_LABELS.get(portfolio.model, portfolio.model),
        "status": portfolio.status,
        "status_label": STATUS_LABELS.get(portfolio.status, portfolio.status),
        "benchmark": portfolio.benchmark,
        "initial_capital": portfolio.initial_capital,
        "tracking_start": portfolio.tracking_start.isoformat(),
        "version_number": version.number if version else 0,
        "asset_count": len(version.holdings) if version else 0,
        "tickers": [holding.ticker for holding in version.holdings] if version else [],
    }


def portfolio_list(user_id: str) -> list[dict]:
    return [_summary(portfolio) for portfolio in repositories.list_portfolios(user_id)]


def portfolio_detail(portfolio: Portfolio) -> dict:
    version = portfolio.current_version
    return {
        **_summary(portfolio),
        "commission_bps": portfolio.commission_bps,
        "version": {
            "number": version.number,
            "effective_date": version.effective_date.isoformat(),
            "reason": REASON_LABELS.get(version.reason, version.reason),
            "value_after": version.value_after,
        },
        "holdings": [
            {
                "ticker": holding.ticker,
                "target_weight": holding.target_weight,
                "units": holding.units,
                "entry_price": holding.entry_price,
            }
            for holding in version.holdings
        ],
    }
