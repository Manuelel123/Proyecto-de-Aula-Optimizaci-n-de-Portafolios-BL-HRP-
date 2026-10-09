"""Safely escaped HTML tables and value formatters."""

import pandas as pd

PERCENTAGE_METRICS = {
    "Retorno anual compuesto",
    "Volatilidad anualizada",
    "Máxima caída",
    "Porcentaje de días positivos",
}


def format_percent(value) -> str:
    return "N/D" if pd.isna(value) else f"{value:.2%}"


def format_decimal(value) -> str:
    return "N/D" if pd.isna(value) else f"{value:,.2f}"


def dataframe_html(
    frame: pd.DataFrame,
    formatters: dict | None = None,
    index: bool = True,
) -> str:
    return frame.to_html(
        classes="data-table",
        border=0,
        escape=True,
        index=index,
        na_rep="N/D",
        formatters=formatters,
    )


def metrics_html(metrics: pd.DataFrame) -> str:
    rows = [
        {
            "Indicador": name,
            "Valor": (
                "N/D"
                if pd.isna(value)
                else (
                    f"{value:.2%}"
                    if name in PERCENTAGE_METRICS
                    else f"{value:.3f}"
                )
            ),
        }
        for name, value in metrics["Valor"].items()
    ]
    return dataframe_html(pd.DataFrame(rows), index=False)
