"""Mi historial: the signed-in user's decision log (read-only)."""

from flask import render_template
from flask_login import current_user

from optimizacion_portafolios.storage.decisions import count_decisions, list_decisions
from optimizacion_portafolios.web.auth.permissions import role_label
from optimizacion_portafolios.web.decisions import bp
from optimizacion_portafolios.web.profile.access import get_questionnaire

METHOD_LABELS = {"hrp": "HRP", "black_litterman": "Black-Litterman"}
SOURCE_LABELS = {
    "hrp": "Página HRP",
    "black_litterman": "Página Black-Litterman",
    "comparar": "Comparación",
    "fijar": "Portafolio fijado",
}
PARAM_LABELS = {
    "mode": "Vista",
    "universe": "Universo",
    "start_date": "Desde",
    "benchmark": "Benchmark",
    "max_weight": "Peso máximo",
    "bounds": "Límites",
    "objective": "Objetivo",
    "risk_free_rate": "Tasa libre de riesgo",
    "views": "Views",
    "portfolio_id": "Portafolio",
    "name": "Nombre",
    "backtest": "Backtest",
}
_LIMIT = 200


def _text(value) -> str:
    """Compact text of a stored parameter (numbers with 4 decimals at most)."""
    if isinstance(value, dict):
        return '; '.join(f'{key}: {_text(item)}' for key, item in value.items()) or '—'
    if isinstance(value, (list, tuple)):
        return ', '.join(_text(item) for item in value) or '—'
    if isinstance(value, bool):
        return 'Sí' if value else 'No'
    if isinstance(value, float):
        return f'{value:.4g}'
    return '—' if value is None else str(value)


def _level_labels() -> dict[str, str]:
    try:
        return {level.id: level.label for level in get_questionnaire().levels}
    except Exception:  # noqa: BLE001 - labels are cosmetic
        return {}


def _row(record, levels) -> dict:
    weights = sorted((record.activos or {}).items(), key=lambda item: -(item[1] or 0))
    params = record.parametros or {}
    return {
        "id": record.id,
        "fecha": record.fecha,
        "method": METHOD_LABELS.get(record.metodo, record.metodo),
        "source": SOURCE_LABELS.get(record.origen, record.origen),
        "profile": levels.get(record.perfil_final, record.perfil_final) if record.perfil_final else "Sin perfil",
        "profile_id": record.perfil_id,
        "role": role_label(record.rol),
        "weights": weights,
        "params": [(PARAM_LABELS.get(key, key), _text(value)) for key, value in params.items() if key != "tickers"],
    }


@bp.get("/historial")
def history():
    levels = _level_labels()
    records = list_decisions(current_user.id, limit=_LIMIT)
    return render_template(
        "decisions/history.html",
        rows=[_row(record, levels) for record in records],
        total=count_decisions(current_user.id),
        limit=_LIMIT,
    )
