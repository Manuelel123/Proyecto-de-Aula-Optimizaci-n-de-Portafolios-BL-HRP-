# Contratos de la interfaz (rediseño UI)

Acuerdos compartidos entre los módulos del frontend. Especificaciones de referencia:
- `docs/diseno_visual.md`: sistema de diseño (tokens, componentes, tema de gráficos, layout).
- `docs/ux_graficos.md`: flujo de formularios, pantalla de resultados, inventario y arquitectura de gráficos. Su encabezado lista decisiones que prevalecen.

## Propiedad de archivos

| Módulo | Archivos |
|---|---|
| Diseño y estructura | `web/static/css/app.css`, `web/templates/base.html`, `web/templates/error.html`, `web/main/**`, `tests/test_web.py` |
| Componentes de formulario | `web/templates/macros/forms.html`, `web/static/js/forms.js`, `web/static/css/forms.css`, `web/common/forms.py`, `data/catalogs.py` (solo `GRUPOS_ACTIVOS`) |
| Gráficos | `web/common/charts.py`, `web/common/price_series.py`, `web/templates/macros/charts.html`, `web/templates/macros/chart_scripts.html`, `web/static/js/charts.js`, `web/static/css/charts.css`, `tests/test_charts.py` |
| HRP | `web/hrp/**`, `models/hrp.py`, `tests/test_web_hrp.py`, `tests/test_hrp_model.py` |
| Black-Litterman | `web/black_litterman/**`, `models/black_litterman.py`, `tests/test_web_black_litterman.py`, `tests/test_black_litterman_model.py` |
| Monitoreo y datos | `web/monitoring/**`, `data/market_data.py`, `tests/test_web_monitoring.py`, `tests/test_market_data.py` |

`web/common/legacy_charts.py` (Matplotlib) es temporal. Las páginas dejan de importarlo y el integrador lo borra al final. El informe QuantStats descargable sigue usando Matplotlib dentro de QuantStats, así que `matplotlib.use("Agg")` se queda en `web/__init__.py`.

## Plantilla base (`base.html`)
- Bloques: `title`, `head_extra`, `content`, `page_scripts`. Las páginas con gráficos incluyen en `page_scripts`:
  `{% include "macros/chart_scripts.html" %}` (plotly-cartesian 4.1.1, locale es, lightweight-charts 5.2.1, `js/charts.js`).
- `forms.js` se carga en todas las páginas (`defer`). En `<html>` se añade la clase `js` antes del primer render.
- Hojas: `css/app.css` (tokens y componentes generales), `css/forms.css`, `css/charts.css`. Los tres usan las variables CSS de `app.css`. Ningún archivo escribe colores hex fuera de los tokens.
- Tema: atributo `data-theme="light|dark"` en `<html>`, persistido en `localStorage["atlas-theme"]`. Al cambiarlo se emite `document.dispatchEvent(new CustomEvent("atlas:themechange", {detail: {theme}}))`. `charts.js` lo escucha para re-aplicar colores.
- Indicador de carga: un `<form>` con `data-loading-title="Optimizando portafolio HRP"` y `data-loading-steps='["Descargando precios", "Estimando covarianzas", ...]'` (JSON). Al enviar con `action` = `optimize`/`analyze`, el JS de `base.html` muestra el overlay sobre el elemento `[data-loading-target]` de la página y pone el botón en `is-loading`. Se restaura en `pageshow`.

## Vocabulario de clases (estiladas en `app.css`)
- Estructura de página:
  - `.page-header` contiene `.eyebrow`, `h1` y `p.lead`, más `.page-header__actions` opcional.
  - `.analysis-layout` es la retícula del panel lateral y los resultados.
  - `.param-panel` es el panel de parámetros (`<aside>`, sticky), con `.param-panel__header`, `.param-panel__body` y `.param-panel__footer` (pie pegajoso con el resumen y el botón primario).
  - `.results` es la columna de resultados y lleva `data-loading-target`.
- Formularios:
  - `fieldset.step` con `<legend>` y el número en `<span class="step__number">`.
  - `.form-field` contiene `label` (o `.field-label`), el control y `.help-text`.
  - `.form-row` agrupa campos en 2 columnas.
  - `details.advanced` es el bloque de opciones avanzadas.
  - `.js-hidden` oculta un elemento cuando `html.js` está activo.
- Resultados:
  - `.result-context` es la barra de contexto de una línea; las acciones van a la derecha en `.result-context__actions`.
  - `.kpi-grid` contiene `.metric-card`, que a su vez lleva `.metric-card__label`, `.metric-card__value` y `.metric-card__context`. El modificador `.metric-card--compact` es para mini-KPIs.
  - `.card` lleva `.card__header` (con `.card__title`, `.card__subtitle` y `.card__actions`) y `.card__body`.
  - `.allocation-card` es la tarjeta principal "Distribución del portafolio": retícula `.allocation-card__chart` + `.allocation-card__table`, apilada en pantallas estrechas.
  - `.analysis-toolbar` contiene los botones "Expandir todo / Contraer todo" (`data-expand-all`, `data-collapse-all`).
  - Las secciones a demanda son `details.analysis-section[data-section]`. Su `summary` lleva `.analysis-section__title` y `.analysis-section__meta`; el contenido va en `.analysis-section__body`.
  - `.chart-grid` es la retícula de varias `.chart-card`.
- Tablas: `.data-table-wrap > table.data-table`, con `td.num`/`th.num` (alineación numérica), `tfoot` (totales) y `.is-negative`.
- Componentes:
  - Avisos: `.notice` con los modificadores `.notice--info`, `--warning`, `--danger` y `--success`.
  - Flash: `.flash` con `.flash-error`, `.flash-warning`, `.flash-success` y `.flash-info`.
  - Botones: `.button` con `.button-primary`, `.button-secondary`, `.button-ghost` y `.button-sm`.
  - Otros: `.empty-state`, `.badge`, `.segmented` (pestañas de vista; la activa lleva `aria-current="page"`), `.visually-hidden`.

## Componentes de formulario (`macros/forms.html`, comportamiento en `forms.js`)
- Firmas fijas:
  - `universe_select(field_id, names, selected, payload)`
  - `asset_selector(available, selected, labels=None, name="tickers", field_id="asset-selector")`
  - `custom_tickers_input(value="", field_id="custom_tickers", placeholder=...)`
  - `percent_input(name, value="", field_id=None, min=None, max=None, placeholder=None, required=False)`
  - `help_tip(text, label="Ayuda")`
  - `date_presets(target_id, years=(1, 2, 3, 5))`
- `universes_payload(universes, default_selection=None)` en `web/common/forms.py`:
  - Produce `{nombre: {"assets": [{ticker, label, group}], "default": [tickers]}}` y va embebido en `<script type="application/json" id="universes">`.
  - `default_selection(nombre, tickers)` replica la regla del servidor. En HRP, todos. En BL, todos si el universo es "Portafolio actual" y los 4 primeros si no.
- Ganchos de JS:
  - `[data-universe-select]`: al cambiar, reconstruye los chips de `[data-asset-selector]` desde `#universes`. Conserva los marcados que existen en el nuevo universo; si no queda ninguno, aplica `default`. El botón `name="action" value="refresh"` lleva la clase `.js-hidden` como respaldo sin JS.
  - Filas dependientes de la selección. Un contenedor `[data-ticker-rows="<template-id>"]` con `<template id="<template-id>">`, donde el texto `__TICKER__` se reemplaza por el ticker. El JS mantiene una fila (`[data-ticker="X"]`) por cada ticker marcado o personalizado, en el orden de selección, y conserva los valores ya escritos.
  - Selección: `[data-asset-selector]` emite `atlas:selectionchange` con `{detail: {tickers}}` (marcados + personalizados válidos) cada vez que cambia la selección.
  - `[data-custom-tickers]` convierte el input en chips y valida con `^[A-Z0-9^=.\-]{1,24}$`.
  - `[data-date-presets="<id>"]` escribe la fecha de hace N años en el input `<id>`.
  - `[data-min-selected="2"]` en el selector deshabilita el botón primario del formulario con un mensaje si hay menos activos.
  - Los nombres de campo enviados al servidor no cambian: `tickers`, `custom_tickers`, `universe`, `start_date`, `benchmark`, `custom_benchmark`, `min_weight`, `max_weight`, `min_<T>`, `max_<T>`, `objective`, `risk_free_rate`, `view_<T>`, `view`, `portfolio`, `ticker`, `period`, `volatility_ticker`, `action`, `csrf_token`.

## Gráficos
- `web/common/charts.py`: cada función pública devuelve `Markup` (JSON de Plotly, ya escapado para `<script>`) o `None` si no hay datos. Firmas fijas, documentadas en el módulo:
  - `allocation_chart`, `cumulative_returns_chart`, `drawdown_chart`, `monthly_returns_heatmap`, `rolling_sharpe_chart`, `returns_distribution_chart`, `yearly_returns_chart`;
  - `historical_charts(portfolio, benchmark, rolling_period) -> (dict, bool)`;
  - `correlation_heatmap`, `dendrogram_chart`, `contribution_chart`, `volatility_histogram_chart`, `returns_box_chart`, `prior_posterior_chart`, `period_volatility_chart`.
- `web/common/price_series.py::price_series_payload(raw_prices, names=None, benchmark=None) -> Markup | None` con el formato columnar documentado en el módulo. Usar precios sin `ffill` entre activos.
- Claves estables de `result["charts"]`: `allocation`, `cumulative`, `drawdown`, `monthly_heatmap`, `rolling_sharpe`, `returns_distribution`, `yearly_returns`, `correlation`, `dendrogram`, `risk_contribution`, `return_contribution`, `monthly_volatility`, `returns_box`, `prior_posterior`, `posterior_correlation`, `period_volatility`. Además `result["prices_payload"]`.
- Macros de `macros/charts.html`:
  - `plotly_chart(key, payload, title, height=360, subtitle=None)` genera `[data-plotly="chart-<key>"]` + `<script id="chart-<key>">`. Si el payload es `None`, no dibuja nada.
  - `price_explorer(key, payload, title)` genera `[data-price-explorer="prices-<key>"]`.
  - Los `key` deben ser únicos en la página.
- `charts.js` dibuja lo visible al cargar. Para lo que está dentro de un `details` cerrado, dibuja al abrirlo (`toggle`) y nunca sobre contenedores ocultos. Aplica colores de tema desde las variables CSS.

## Pruebas
- Ningún test accede a la red. Cada módulo actualiza sus tests: se pueden adaptar las aserciones de marcado HTML (orden de atributos, textos) al nuevo diseño, siempre que se mantenga la intención (que el valor, el resultado o el error se muestren).
- Comando, desde la raíz del worktree: `PYTHONPATH=src <python compartido> -m unittest discover -s tests`. Toda la suite debe pasar.
