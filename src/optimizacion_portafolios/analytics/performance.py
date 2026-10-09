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
