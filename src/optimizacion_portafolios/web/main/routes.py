"""Home page routes."""

from flask import render_template

from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    ACTIVOS_OPCIONES,
    BENCHMARKS,
)
from optimizacion_portafolios.web.main import bp

MODEL_COUNT = 2
DATA_SOURCE_COUNT = 2

MODULES = (
    {
        "key": "market",
        "endpoint": "monitoring.monitoring",
        "eyebrow": "Mercado",
        "title": "Monitoreo de mercado",
        "description": "Precios, retornos, volatilidad y fundamentales de cada activo en un solo lugar.",
        "features": (
            "Gráfico de precios interactivo con rangos",
            "Volatilidad histórica por periodo",
            "Perfil fundamental de cada compañía",
        ),
    },
    {
        "key": "hrp",
        "endpoint": "hrp.hrp",
        "eyebrow": "Riesgo jerárquico",
        "title": "Optimización HRP",
        "description": "Reparte el riesgo por clusters de correlación sin estimar retornos esperados.",
        "features": (
            "Clustering por correlación y dendrograma",
            "Límites de peso globales y por activo",
            "Desempeño histórico frente a un benchmark",
        ),
    },
    {
        "key": "black_litterman",
        "endpoint": "black_litterman.black_litterman",
        "eyebrow": "Equilibrio + views",
        "title": "Black-Litterman",
        "description": "Combina el equilibrio implícito del mercado con tus expectativas por activo.",
        "features": (
            "Prior de equilibrio por capitalización",
            "Views anuales con confianza de Idzorek",
            "Objetivos de Sharpe, Sortino, CVaR y utilidad",
        ),
    },
)


def catalog_asset_count() -> int:
    """Unique tickers offered across the asset, benchmark and options catalogs."""
    tickers = (
        set(ACTIVOS_HRP.values())
        | set(BENCHMARKS.values())
        | set(ACTIVOS_OPCIONES.values())
    )
    return len(tickers)


@bp.get("/")
def index():
    stats = (
        {"value": catalog_asset_count(), "label": "activos e índices en catálogo"},
        {"value": MODEL_COUNT, "label": "modelos de optimización"},
        {"value": DATA_SOURCE_COUNT, "label": "fuentes de datos de mercado"},
    )
    return render_template("main/index.html", modules=MODULES, stats=stats)
