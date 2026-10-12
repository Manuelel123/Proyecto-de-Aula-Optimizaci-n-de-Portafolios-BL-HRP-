"""Portfolio performance metrics and QuantStats reports."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import quantstats as qs


def calculate_quantstats_metrics(returns: pd.Series) -> pd.DataFrame:
    metrics = {
        "Retorno anual compuesto": qs.stats.cagr(returns),
        "Volatilidad anualizada": qs.stats.volatility(returns),
        "Ratio de Sharpe": qs.stats.sharpe(returns),
        "Ratio de Sortino": qs.stats.sortino(returns),
        "Máxima caída": qs.stats.max_drawdown(returns),
        "Ratio de Calmar": qs.stats.calmar(returns),
        "Porcentaje de días positivos": qs.stats.win_rate(returns),
    }
    return pd.DataFrame.from_dict(metrics, orient="index", columns=["Valor"])


def generate_tearsheet(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    title: str,
    filename: str,
) -> bytes:
    with TemporaryDirectory() as directory:
        report_path = Path(directory) / "report.html"
        qs.reports.html(
            portfolio_returns,
            benchmark=benchmark_returns,
            output=str(report_path),
            title=f"Strategy Tearsheet - {title}",
            download_filename=filename,
        )
        return report_path.read_bytes()


def calculate_strategy_metrics(values: pd.Series, initial_value: float) -> dict:
    """Backtest metrics of a portfolio value series (QuantStats conventions).

    Total return and CAGR (calendar years of the series) are measured against
    ``initial_value``, so the costs of the initial purchase count; volatility,
    Sharpe and Sortino (rf = 0) use the daily returns of ``values``.
    """
    values = values.astype(float).dropna()
    returns = values.pct_change(fill_method=None).dropna()
    total_return = float(values.iloc[-1] / initial_value - 1)
    years = (values.index[-1] - values.index[0]).days / 365.25
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else float("nan")
    return {
        "Retorno total": total_return,
        "CAGR": float(cagr),
        "Volatilidad anualizada": float(qs.stats.volatility(returns)),
        "Ratio de Sharpe": float(qs.stats.sharpe(returns)),
        "Ratio de Sortino": float(qs.stats.sortino(returns)),
        "Máxima caída": float((values / values.cummax() - 1).min()),
    }
