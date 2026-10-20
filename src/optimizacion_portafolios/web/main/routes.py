"""Portada (signed out), main screen (signed in) and glossary."""

from flask import render_template
from flask_login import current_user

from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    ACTIVOS_OPCIONES,
    BENCHMARKS,
)
from optimizacion_portafolios.web.auth.permissions import (
    ROLE_SUMMARIES,
    can_open,
    role_label,
)
from optimizacion_portafolios.web.auth.passwords import password_rules
from optimizacion_portafolios.web.common.guided import glossary as glossary_items
from optimizacion_portafolios.web.main import bp
from optimizacion_portafolios.web.profile.access import current_profile, pending_profile_step

MODEL_COUNT = 2
DATA_SOURCE_COUNT = 2

# Tools of the main screen; each card shows only if the role can open it.
TOOLS = (
    {
        "key": "hrp",
        "endpoint": "hrp.hrp",
        "eyebrow": "Riesgo jerárquico",
        "title": "Optimización HRP",
        "description": "Reparte el dinero según el riesgo y la correlación de los activos, sin predecir retornos.",
    },
    {
        "key": "black_litterman",
        "endpoint": "black_litterman.black_litterman",
        "eyebrow": "Equilibrio + views",
        "title": "Black-Litterman",
        "description": "Parte del equilibrio de mercado y busca el mejor retorno esperado por unidad de riesgo.",
    },
    {
        "key": "compare",
        "endpoint": "compare.compare",
        "eyebrow": "Dos métodos",
        "title": "Comparar HRP y Black-Litterman",
        "description": "Los dos portafolios con los mismos activos, lado a lado, con sus métricas clave.",
    },
    {
        "key": "market",
        "endpoint": "monitoring.monitoring",
        "eyebrow": "Mercado",
        "title": "Monitoreo de mercado",
        "description": "Precios, retornos, volatilidad, opciones y fundamentales de cada activo.",
    },
    {
        "key": "portfolios",
        "endpoint": "portfolios.index",
        "eyebrow": "Seguimiento",
        "title": "Mis portafolios",
        "description": "Los portafolios que fijaste: panel, alertas y versiones.",
    },
    {
        "key": "history",
        "endpoint": "decisions.history",
        "eyebrow": "Registro",
        "title": "Mi historial",
        "description": "Cada portafolio que generaste, con fecha, perfil vigente, método, pesos y supuestos.",
    },
    {
        "key": "glossary",
        "endpoint": "main.glossary",
        "eyebrow": "Aprende",
        "title": "Glosario",
        "description": "Diversificación, volatilidad, correlación, drawdown, Sharpe y más, en lenguaje simple.",
    },
)
ROLES_OVERVIEW = (
    ("Novato", ROLE_SUMMARIES["novato"]),
    ("Inversionista", ROLE_SUMMARIES["inversionista"]),
    ("Gestor", ROLE_SUMMARIES["gestor"]),
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
    if not current_user.is_authenticated:
        stats = (
            {"value": catalog_asset_count(), "label": "activos e índices en catálogo"},
            {"value": MODEL_COUNT, "label": "métodos de construcción de portafolios"},
            {"value": DATA_SOURCE_COUNT, "label": "fuentes de datos de mercado"},
        )
        return render_template(
            "main/index.html",
            stats=stats,
            roles=ROLES_OVERVIEW,
            password_rules=password_rules(),
        )
    # The home page is public, so the access checks do not run here: a user
    # without a profile (or role) sees the pending step instead of the tools.
    step = pending_profile_step(current_user)
    tools = [] if step else [tool for tool in TOOLS if can_open(tool["endpoint"])]
    return render_template(
        "main/dashboard.html",
        profile=current_profile(),
        pending_step=step,
        role_label=role_label(current_user.role),
        role_summary=ROLE_SUMMARIES.get(current_user.role, ""),
        tools=tools,
    )


@bp.get("/glosario")
def glossary():
    return render_template("main/glossary.html", items=glossary_items())
