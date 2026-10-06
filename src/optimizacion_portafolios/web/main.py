"""Dashboard route."""

from flask import Blueprint, render_template

from optimizacion_portafolios.catalogs import ACTIVOS, INDICES

main_blueprint = Blueprint("main", __name__)


@main_blueprint.get("/")
def index():
    modules = [
        {
            "title": "Monitoreo de activos",
            "description": "Precios históricos, retornos, riesgo y datos fundamentales.",
            "href": "/monitoring",
            "eyebrow": "Mercado",
            "icon": "01",
        },
        {
            "title": "Optimización HRP",
            "description": "Pesos de cartera por paridad de riesgo jerárquica.",
            "href": "/hrp",
            "eyebrow": "Asignación",
            "icon": "02",
        },
        {
            "title": "Black-Litterman",
            "description": "Combina el prior de equilibrio del mercado con views.",
            "href": "/black-litterman",
            "eyebrow": "Optimización",
            "icon": "03",
        },
    ]
    return render_template(
        "index.html",
        modules=modules,
        asset_count=len(set(ACTIVOS.values()) | set(INDICES.values())),
        model_count=2,
    )
