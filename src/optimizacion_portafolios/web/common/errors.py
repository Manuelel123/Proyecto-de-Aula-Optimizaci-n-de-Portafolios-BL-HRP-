"""User-facing messages for market data download failures."""

from yfinance.exceptions import YFRateLimitError

RATE_LIMIT_MESSAGE = (
    "Yahoo Finance limitó temporalmente las consultas. "
    "Espera unos minutos y vuelve a intentar."
)


def download_error_message(error: Exception, context: str = "No se pudieron descargar los datos") -> str:
    """Spanish message of a ``DOWNLOAD_ERRORS`` failure (rate limit or ``context: error``)."""
    if isinstance(error, YFRateLimitError):
        return RATE_LIMIT_MESSAGE
    return f"{context}: {error}"
