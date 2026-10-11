/* Chart runtime: lazy Plotly rendering, theming from CSS variables, Lightweight Charts price explorer.
 *
 * Hooks (see templates/macros/charts.html):
 *   [data-plotly="chart-<key>"]                Plotly container; figure JSON in <script id="chart-<key>">.
 *   select[data-plotly-control="chart-<key>"]  optional external selector: its value is matched against the
 *                                              labels of the figure's first updatemenu (e.g. a ticker).
 *   [data-price-explorer="prices-<key>"]       Lightweight Charts explorer; JSON in <script id="prices-<key>">.
 *   [data-linked-chart="chart-<key>"]          on the explorer: when the user picks an asset, the button of that
 *                                              chart's first updatemenu whose ``name`` is the ticker is applied
 *                                              (remembered and applied at render time if not drawn yet).
 *   [data-history-url]                         on the explorer: "Periodo del análisis / Historial completo"
 *                                              switch ([data-history-mode]); the full history of the selected
 *                                              asset is fetched from ``<url>?ticker=`` (prices + returns matrix
 *                                              figure) and shown in the explorer and in the linked chart.
 *   [data-expand-all] / [data-collapse-all]    open / close every details.analysis-section of the page.
 *
 * Figures are serialized with the light-theme token hex values (web/common/chart_theme.py). Every known
 * token (also as #rrggbbaa with alpha) is swapped for the value of its CSS variable from app.css, so the
 * figures follow the light/dark theme; "atlas:themechange" (and the OS preference) re-applies the colors.
 * Nothing is drawn into a hidden container: pending charts are watched with a ResizeObserver and drawn
 * when they get a size (opening a <details>, switching a tab).
 */
(function () {
  "use strict";

  /* Token hex (light theme) -> CSS variable of app.css. Keep in sync with chart_theme.CSS_VARIABLES. */
  var TOKENS = {
    "#2a78d6": "--series-1", "#eb6834": "--series-2", "#1baf7a": "--series-3", "#eda100": "--series-4",
    "#e87ba4": "--series-5", "#008300": "--series-6", "#4a3aa7": "--series-7", "#e34948": "--series-8",
    "#0f172a": "--text-1", "#475569": "--text-2", "#5f6b7e": "--text-3", "#9aa3b2": "--text-disabled",
    "#e3e7ed": "--border", "#cfd5de": "--border-strong", "#ffffff": "--surface-1", "#e8ebf0": "--surface-3",
    "#16a34a": "--positive-chart", "#dc2626": "--negative-chart",
    "#104281": "--corr-neg-3", "#256abf": "--corr-neg-2", "#86b6ef": "--corr-neg-1", "#f0efec": "--corr-mid",
    "#f3a29f": "--corr-pos-1", "#d03b3b": "--corr-pos-2", "#8f1d1d": "--corr-pos-3"
  };
  var HEX_COLOR = /^#[0-9a-f]{6}([0-9a-f]{2})?$/i;
  var LIBRARY_ERROR = "No se pudo cargar la librería de gráficos. Revisa tu conexión y recarga la página; los valores exactos están en las tablas.";
  var DATA_ERROR = "No se pudieron leer los datos del gráfico.";
  var numberFormats = {};

  /* ------------------------------------------------------------------ colors */

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function palette() {
    var map = {};
    Object.keys(TOKENS).forEach(function (hex) { map[hex] = cssVar(TOKENS[hex], hex); });
    return map;
  }

  function parseColor(color) {
    var match;
    color = String(color).trim();
    if ((match = /^#([0-9a-f]{3})$/i.exec(color))) {
      color = "#" + match[1].split("").map(function (c) { return c + c; }).join("");
    }
    if ((match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})?$/i.exec(color))) {
      return {
        r: parseInt(match[1], 16), g: parseInt(match[2], 16), b: parseInt(match[3], 16),
        a: match[4] ? parseInt(match[4], 16) / 255 : 1
      };
    }
    if ((match = /^rgba?\(([^)]+)\)$/i.exec(color))) {
      var parts = match[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat);
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    }
    return null;
  }

  function withAlpha(color, alpha) {
    var rgb = parseColor(color);
    if (!rgb) { return color; }
    return "rgba(" + rgb.r + "," + rgb.g + "," + rgb.b + "," + +(alpha * rgb.a).toFixed(3) + ")";
  }

  function resolveColor(value, map) {
    if (typeof value !== "string" || !HEX_COLOR.test(value)) { return value; }
    var base = value.slice(0, 7).toLowerCase();
    if (!map[base]) { return value; }
    if (value.length === 9) { return withAlpha(map[base], parseInt(value.slice(7), 16) / 255); }
    return map[base];
  }

  function themed(node, map) {
    if (Array.isArray(node)) { return node.map(function (item) { return themed(item, map); }); }
    if (node && typeof node === "object") {
      var copy = {};
      Object.keys(node).forEach(function (key) { copy[key] = themed(node[key], map); });
      return copy;
    }
    return resolveColor(node, map);
  }

  /* ------------------------------------------------------------------ helpers */

  function isVisible(el) {
    return el.getClientRects().length > 0 && el.offsetWidth > 0;
  }

  function readJson(id) {
    var script = document.getElementById(id);
    if (!script) { return null; }
    try { return JSON.parse(script.textContent); } catch (error) { return null; }
  }

  function showMessage(el, text) {
    if (el.querySelector(".chart-message")) { return; }
    var message = document.createElement("p");
    message.className = "chart-message";
    message.setAttribute("role", "status");
    message.textContent = text;
    el.appendChild(message);
  }

  /* Point decimal, comma thousands: same convention as the tables and Plotly (separators ".,"). */
  function formatNumber(value, decimals) {
    if (!numberFormats[decimals]) {
      numberFormats[decimals] = new Intl.NumberFormat("en-US", {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals
      });
    }
    return numberFormats[decimals].format(value).replace("-", "−");
  }

  function setPath(target, path, value) {
    var keys = path.split(".");
    var node = target;
    for (var i = 0; i < keys.length - 1; i += 1) {
      if (!node[keys[i]] || typeof node[keys[i]] !== "object") { node[keys[i]] = {}; }
      node = node[keys[i]];
    }
    node[keys[keys.length - 1]] = value;
  }

  /* Pending containers are drawn as soon as they get a size. */
  var pendingObserver = typeof ResizeObserver === "function"
    ? new ResizeObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.contentRect.width > 0 && isVisible(entry.target)) { activate(entry.target); }
        });
      })
    : null;

  function watch(el) {
    if (pendingObserver && !el.dataset.chartWatched) {
      el.dataset.chartWatched = "true";
      pendingObserver.observe(el);
    }
  }

  function unwatch(el) {
    if (pendingObserver && el.dataset.chartWatched) {
      pendingObserver.unobserve(el);
      delete el.dataset.chartWatched;
    }
  }

  function activate(el) {
    if (el.hasAttribute("data-plotly")) { renderPlot(el); } else { initPriceExplorer(el); }
  }

  /* ------------------------------------------------------------------ Plotly */

  var pristine = new WeakMap();
  /* Figure shown instead of the page's own JSON (full-history matrix); absent = the original. */
  var alternates = new WeakMap();

  function plotConfig(el) {
    return {
      responsive: true,
      displaylogo: false,
      locale: "es",
      modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
      toImageButtonOptions: {
        format: "png",
        scale: 2,
        filename: (el.dataset.plotly || "grafico").replace(/^chart-/, "")
      }
    };
  }

  function syncHeight(el, layout) {
    if (layout && layout.height) {
      el.style.height = layout.height + "px";
      el.style.minHeight = "0";
    }
  }

  /* Index of the first updatemenu button whose ``field`` ("label" by default, or "name") equals value. */
  function menuButton(el, value, field) {
    var menus = (el.layout && el.layout.updatemenus) || [];
    if (!menus.length) { return -1; }
    var buttons = menus[0].buttons || [];
    var key = field || "label";
    for (var i = 0; i < buttons.length; i += 1) {
      if (buttons[i][key] !== undefined && String(buttons[i][key]) === String(value)) { return i; }
    }
    return -1;
  }

  function selectMenuButton(el, value, field) {
    var index = menuButton(el, value, field);
    if (index < 0) { return; }
    if (((el.layout.updatemenus || [])[0] || {}).active === index) { return; }
    var args = el.layout.updatemenus[0].buttons[index].args || [];
    var map = palette();
    var layoutArgs = Object.assign({}, themed(args[1] || {}, map), { "updatemenus[0].active": index });
    window.Plotly.update(el, themed(args[0] || {}, map), layoutArgs);
  }

  function renderPlot(el) {
    var state = el.dataset.plotlyState;
    if (state === "rendered") { if (isVisible(el)) { window.Plotly.Plots.resize(el); } return; }
    if (state === "pending" || state === "error") { return; }
    if (!isVisible(el)) { watch(el); return; }
    if (!window.Plotly) { el.dataset.plotlyState = "error"; showMessage(el, LIBRARY_ERROR); return; }
    var spec = alternates.has(el) ? alternates.get(el) : readJson(el.dataset.plotly);
    if (!spec || !Array.isArray(spec.data)) { el.dataset.plotlyState = "error"; showMessage(el, DATA_ERROR); return; }
    unwatch(el);
    pristine.set(el, spec);
    el.dataset.plotlyState = "pending";
    var figure = themed(spec, palette());
    figure.layout = figure.layout || {};
    syncHeight(el, figure.layout);
    window.Plotly.newPlot(el, figure.data, figure.layout, plotConfig(el)).then(function () {
      el.dataset.plotlyState = "rendered";
      el.on("plotly_relayout", function () { syncHeight(el, el.layout); });
      el.on("plotly_update", function () { syncHeight(el, el.layout); });
      var control = document.querySelector('select[data-plotly-control="' + el.dataset.plotly + '"]');
      if (control && control.value) { selectMenuButton(el, control.value); }
      var linked = el.dataset.plotlyLinked;
      if (linked !== undefined) {
        delete el.dataset.plotlyLinked;
        selectMenuButton(el, linked, "name");
      }
    }, function () {
      el.dataset.plotlyState = "error";
      showMessage(el, DATA_ERROR);
    });
  }

  /* Rebuild from the pristine spec with the new colors, keeping trace visibility and the menu choice. */
  function rethemePlot(el, map) {
    var spec = pristine.get(el);
    if (!spec) { return; }
    var figure = themed(spec, map);
    figure.layout = figure.layout || {};
    (el.data || []).forEach(function (trace, index) {
      if (figure.data[index] && trace.visible !== undefined) { figure.data[index].visible = trace.visible; }
    });
    (figure.layout.updatemenus || []).forEach(function (menu, index) {
      var current = ((el.layout && el.layout.updatemenus) || [])[index];
      if (!current || current.active === undefined || current.active === menu.active) { return; }
      var button = (menu.buttons || [])[current.active];
      menu.active = current.active;
      if (button && button.args && button.args[1]) {
        Object.keys(button.args[1]).forEach(function (key) { setPath(figure.layout, key, button.args[1][key]); });
      }
    });
    syncHeight(el, figure.layout);
    window.Plotly.react(el, figure.data, figure.layout, plotConfig(el));
  }

  /* Swap a chart's figure for ``spec`` (null = back to the page's own figure). ``subtitle`` replaces the
   * card subtitle while the alternate figure is shown. Charts not drawn yet pick it up at render time. */
  function swapFigure(el, spec, subtitle) {
    var card = el.closest("figure");
    var caption = card && card.querySelector(".chart-card__subtitle");
    if (caption) {
      if (caption.dataset.original === undefined) { caption.dataset.original = caption.textContent; }
      caption.textContent = spec && subtitle ? subtitle : caption.dataset.original;
    }
    if (spec) { alternates.set(el, spec); } else { alternates.delete(el); }
    if (el.dataset.plotlyState !== "rendered") { return; }
    var next = spec || readJson(el.dataset.plotly);
    if (!next || !Array.isArray(next.data)) { return; }
    pristine.set(el, next);
    var figure = themed(next, palette());
    figure.layout = figure.layout || {};
    syncHeight(el, figure.layout);
    window.Plotly.react(el, figure.data, figure.layout, plotConfig(el));
  }

  /* ------------------------------------------------------------------ price explorer */

  var explorers = [];
  var RANGE_MONTHS = { "1M": 1, "3M": 3, "6M": 6, "1Y": 12, "5Y": 60 };

  function timeKey(time) {
    if (typeof time === "string") { return time; }
    if (typeof time === "number") { return new Date(time * 1000).toISOString().slice(0, 10); }
    if (time && time.year) {
      return time.year + "-" + String(time.month).padStart(2, "0") + "-" + String(time.day).padStart(2, "0");
    }
    return "";
  }

  function shiftDate(iso, months) {
    var date = new Date(iso + "T00:00:00Z");
    date.setUTCMonth(date.getUTCMonth() - months);
    return date.toISOString().slice(0, 10);
  }

  function rangeStart(range, first, last) {
    var start = first;
    if (range === "YTD") { start = last.slice(0, 4) + "-01-01"; }
    else if (RANGE_MONTHS[range]) { start = shiftDate(last, RANGE_MONTHS[range]); }
    return start < first ? first : start;
  }

  function formatDay(iso) {
    var date = new Date(iso + "T00:00:00Z");
    return date.toLocaleDateString("es-CO", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  }

  function explorerColors() {
    var series = cssVar("--series-1", "#2a78d6");
    return {
      series: series,
      fill: cssVar("--series-1-fill", withAlpha(series, 0.22)),
      fillEnd: cssVar("--series-1-fill-end", withAlpha(series, 0)),
      text3: cssVar("--text-3", "#5f6b7e"),
      border: cssVar("--border", "#e3e7ed"),
      inverse: cssVar("--surface-inverse", "#0f172a")
    };
  }

  function chartOptions(LW, colors) {
    return {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "transparent" },
        textColor: colors.text3,
        fontFamily: "Inter, system-ui, sans-serif",
        fontSize: 12,
        attributionLogo: true
      },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.border } },
      crosshair: {
        mode: LW.CrosshairMode.Magnet,
        vertLine: { color: colors.text3, width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: colors.inverse },
        horzLine: { color: colors.text3, width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: colors.inverse }
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderVisible: false, rightOffset: 4, fixLeftEdge: true, fixRightEdge: true },
      localization: { locale: "es-CO" }
    };
  }

  function seriesOptions(LW, colors, precision) {
    return {
      lineColor: colors.series,
      topColor: colors.fill,
      bottomColor: colors.fillEnd,
      lineWidth: 2,
      priceLineVisible: true,
      priceLineStyle: LW.LineStyle.Dotted,
      priceLineColor: colors.text3,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
      priceFormat: { type: "price", precision: precision, minMove: Math.pow(10, -precision) }
    };
  }

  function benchmarkOptions(LW, colors) {
    return {
      color: colors.text3,
      lineWidth: 2,
      lineStyle: LW.LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 3,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 }
    };
  }

  function points(entry, base) {
    var factor = base ? 100 / base : 1;
    return entry.time.map(function (time, index) {
      return { time: time, value: entry.value[index] * factor };
    });
  }

  /* First value on or after ``start`` (the base of the period). */
  function baseValue(entry, start) {
    for (var i = 0; i < entry.time.length; i += 1) {
      if (entry.time[i] >= start) { return entry.value[i]; }
    }
    return entry.value[entry.value.length - 1];
  }

  /* Last value on or before ``iso`` (binary search over ascending ISO dates). */
  function valueAt(entry, iso) {
    var low = 0;
    var high = entry.time.length - 1;
    var found = -1;
    while (low <= high) {
      var mid = (low + high) >> 1;
      if (entry.time[mid] <= iso) { found = mid; low = mid + 1; } else { high = mid - 1; }
    }
    return found < 0 ? null : entry.value[found];
  }

  function legendItem(className, text) {
    var node = document.createElement("span");
    node.className = className;
    node.textContent = text;
    return node;
  }

  function initPriceExplorer(root) {
    if (root.dataset.priceState) { return; }
    if (!isVisible(root)) { watch(root); return; }
    unwatch(root);
    var canvas = root.querySelector("[data-price-canvas]") || root;
    var LW = window.LightweightCharts;
    if (!LW || !LW.createChart) {
      root.dataset.priceState = "error";
      showMessage(canvas, LIBRARY_ERROR);
      return;
    }
    var payload = readJson(root.dataset.priceExplorer);
    if (!payload || !Array.isArray(payload.series) || !payload.series.length) {
      root.dataset.priceState = "error";
      showMessage(canvas, DATA_ERROR);
      return;
    }
    root.dataset.priceState = "ready";

    var select = root.querySelector("[data-price-asset]");
    var base100 = root.querySelector("[data-price-base100]");
    var legend = root.querySelector("[data-price-legend]");
    var rangeButtons = Array.prototype.slice.call(root.querySelectorAll("[data-range]"));
    var historyUrl = root.dataset.historyUrl;
    var historyButtons = Array.prototype.slice.call(root.querySelectorAll("[data-history-mode]"));
    var historyStatus = root.querySelector("[data-history-status]");
    var linkedChart = root.dataset.linkedChart
      ? document.querySelector('[data-plotly="' + root.dataset.linkedChart + '"]') : null;
    var benchmark = payload.benchmark && payload.benchmark.time && payload.benchmark.time.length
      ? payload.benchmark : null;
    var byTicker = {};
    payload.series.forEach(function (entry) { byTicker[entry.ticker] = entry; });
    var analysisByTicker = Object.assign({}, byTicker);
    var fullHistory = {};

    if (select) {
      var preferred = select.dataset.selected || select.value;
      select.innerHTML = "";
      payload.series.forEach(function (entry) {
        var option = document.createElement("option");
        option.value = entry.ticker;
        option.textContent = entry.name && entry.name !== entry.ticker ? entry.ticker + " · " + entry.name : entry.ticker;
        select.appendChild(option);
      });
      if (byTicker[preferred]) { select.value = preferred; }
      select.disabled = payload.series.length < 2;
    }
    if (base100) {
      var toggle = base100.closest("label") || base100;
      if (!benchmark) {
        toggle.hidden = true;
      } else if (toggle.lastChild && toggle.lastChild.nodeType === 3) {
        toggle.lastChild.textContent = " Base 100 vs " + benchmark.ticker;
      }
    }
    var activeButton = rangeButtons.filter(function (button) { return button.classList.contains("is-active"); })[0];

    var state = {
      ticker: select && byTicker[select.value] ? select.value : payload.series[0].ticker,
      range: activeButton ? activeButton.dataset.range : "ALL",
      base100: Boolean(base100 && base100.checked && benchmark),
      main: null,
      bench: null,
      full: false
    };
    var colors = explorerColors();
    var chart = LW.createChart(canvas, chartOptions(LW, colors));

    function current() { return byTicker[state.ticker]; }

    function precisionFor(entry) {
      return state.base100 || entry.value[entry.value.length - 1] >= 1 ? 2 : 4;
    }

    function period() {
      var entry = current();
      var last = entry.time[entry.time.length - 1];
      return { from: rangeStart(state.range, entry.time[0], last), to: last };
    }

    /* Legend: ticker, name, price at the cursor (or last) and change since the start of the range. */
    function updateLegend(iso, price) {
      if (!legend) { return; }
      var entry = current();
      var range = period();
      var day = iso || range.to;
      var value = price != null ? price : valueAt(entry, day);
      var start = baseValue(entry, range.from);
      if (value == null || !start) { return; }
      var change = value / start - 1;
      var up = change >= 0;
      var shown = state.base100 ? value / start * 100 : value;
      legend.innerHTML = "";
      legend.appendChild(legendItem("price-explorer__ticker", entry.ticker));
      if (entry.name && entry.name !== entry.ticker) {
        legend.appendChild(legendItem("price-explorer__name", entry.name));
      }
      legend.appendChild(legendItem(
        "price-explorer__price", formatNumber(shown, precisionFor(entry)) + (state.base100 ? " (base 100)" : "")
      ));
      var delta = legendItem(
        "price-explorer__change " + (up ? "is-up" : "is-down"),
        (up ? "▲ +" : "▼ ") + formatNumber(change * 100, 2) + " %"
      );
      delta.title = "Variación desde el " + formatDay(range.from);
      legend.appendChild(delta);
      legend.appendChild(legendItem("price-explorer__date", formatDay(day)));
    }

    function applyRange() {
      if (state.range === "ALL") { chart.timeScale().fitContent(); return; }
      try { chart.timeScale().setVisibleRange(period()); } catch (error) { chart.timeScale().fitContent(); }
    }

    function draw() {
      var entry = current();
      var range = period();
      if (state.main) { chart.removeSeries(state.main); state.main = null; }
      if (state.bench) { chart.removeSeries(state.bench); state.bench = null; }
      state.main = chart.addSeries(LW.AreaSeries, seriesOptions(LW, colors, precisionFor(entry)));
      state.main.setData(points(entry, state.base100 ? baseValue(entry, range.from) : null));
      if (state.base100 && benchmark && benchmark.ticker !== entry.ticker) {
        state.bench = chart.addSeries(LW.LineSeries, Object.assign(benchmarkOptions(LW, colors), { title: benchmark.ticker }));
        state.bench.setData(points(benchmark, baseValue(benchmark, range.from)));
      }
      applyRange();
      updateLegend(null, null);
    }

    function setRange(range) {
      state.range = range;
      rangeButtons.forEach(function (other) {
        var active = other.dataset.range === range;
        other.classList.toggle("is-active", active);
        other.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function setStatus(text, isError) {
      if (!historyStatus) { return; }
      historyStatus.textContent = text || "";
      historyStatus.hidden = !text;
      historyStatus.classList.toggle("is-error", Boolean(isError));
    }

    /* Base 100 compares against the benchmark of the analysis window only. */
    function setBase100Available(available) {
      if (!base100 || !benchmark) { return; }
      var label = base100.closest("label") || base100;
      base100.disabled = !available;
      label.classList.toggle("is-disabled", !available);
      label.title = available ? "" : "Disponible solo con el periodo del análisis";
      if (!available && base100.checked) { base100.checked = false; state.base100 = false; }
    }

    function historyText(data) {
      return "Historial completo de " + data.ticker + " · desde el " + formatDay(data.start) + " · " +
        data.years + (data.years === 1 ? " año" : " años") + " · " +
        formatNumber(data.observations, 0) + " cotizaciones";
    }

    function showFullHistory(data) {
      var entry = Object.assign({}, data.prices);
      var analysis = analysisByTicker[data.ticker];
      if (analysis && analysis.name) { entry.name = analysis.name; }
      byTicker[data.ticker] = entry;
      draw();
      setStatus(historyText(data), false);
      if (linkedChart && data.matrix) {
        swapFigure(linkedChart, data.matrix,
          "Historial completo de " + (entry.name || data.ticker) + " · rendimiento mensual compuesto y anual");
      }
    }

    function requestHistory(ticker) {
      var cached = fullHistory[ticker];
      if (cached) { return cached; }
      var url = historyUrl + (historyUrl.indexOf("?") < 0 ? "?" : "&") + "ticker=" + encodeURIComponent(ticker);
      cached = fetch(url, { headers: { Accept: "application/json" } }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok || !body.prices) {
            throw new Error(body.error || "No se pudo descargar el historial de " + ticker + ".");
          }
          return body;
        });
      });
      fullHistory[ticker] = cached;
      /* Failures are not cached: the next attempt retries. */
      cached.catch(function () { delete fullHistory[ticker]; });
      return cached;
    }

    function loadFullHistory(ticker) {
      root.classList.add("is-loading");
      setStatus("Cargando el historial completo de " + ticker + "…", false);
      requestHistory(ticker).then(function (data) {
        if (!state.full || state.ticker !== ticker) { return; }
        root.classList.remove("is-loading");
        showFullHistory(data);
      }, function (error) {
        if (!state.full || state.ticker !== ticker) { return; }
        root.classList.remove("is-loading");
        setStatus(error.message + " Se muestra el periodo del análisis.", true);
      });
    }

    function setHistoryMode(full) {
      state.full = full;
      historyButtons.forEach(function (button) {
        var active = (button.dataset.historyMode === "full") === full;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
      setBase100Available(!full);
      if (full) {
        setRange("ALL");
        loadFullHistory(state.ticker);
        return;
      }
      root.classList.remove("is-loading");
      setStatus("", false);
      byTicker = Object.assign({}, analysisByTicker);
      draw();
      if (linkedChart) {
        swapFigure(linkedChart, null);
        if (linkedChart.dataset.plotlyState === "rendered") { selectMenuButton(linkedChart, state.ticker, "name"); }
        else { linkedChart.dataset.plotlyLinked = state.ticker; }
      }
    }

    if (historyUrl && historyButtons.length && window.fetch) {
      historyButtons.forEach(function (button) {
        button.addEventListener("click", function () {
          var full = button.dataset.historyMode === "full";
          if (full !== state.full) { setHistoryMode(full); }
        });
      });
    } else if (historyButtons.length) {
      historyButtons[0].parentNode.hidden = true;
    }

    chart.subscribeCrosshairMove(function (param) {
      if (!param || !param.time || !state.main) { updateLegend(null, null); return; }
      var data = param.seriesData && param.seriesData.get(state.main);
      var value = data && data.value != null ? data.value : null;
      if (value != null && state.base100) {
        /* The series holds base-100 values: convert back to a price. */
        value = value / 100 * baseValue(current(), period().from);
      }
      updateLegend(timeKey(param.time), value);
    });

    if (select) {
      select.addEventListener("change", function () {
        if (!byTicker[select.value]) { return; }
        state.ticker = select.value;
        draw();
        if (state.full) { loadFullHistory(state.ticker); }
      });
    }
    if (base100) {
      base100.addEventListener("change", function () {
        state.base100 = base100.checked && Boolean(benchmark);
        draw();
      });
    }
    rangeButtons.forEach(function (button) {
      button.setAttribute("aria-pressed", button.dataset.range === state.range ? "true" : "false");
      button.addEventListener("click", function () {
        setRange(button.dataset.range);
        /* In base 100 the base moves with the range start, so the data is rebuilt. */
        if (state.base100) { draw(); } else { applyRange(); updateLegend(null, null); }
      });
    });

    explorers.push(function retheme() {
      colors = explorerColors();
      chart.applyOptions(chartOptions(LW, colors));
      if (state.main) { state.main.applyOptions(seriesOptions(LW, colors, precisionFor(current()))); }
      if (state.bench) { state.bench.applyOptions(benchmarkOptions(LW, colors)); }
    });
    draw();
  }

  /* Explorer -> linked Plotly chart: apply now if drawn, otherwise remember it for renderPlot. */
  function followLinkedChart(chartId, ticker) {
    var el = document.querySelector('[data-plotly="' + chartId + '"]');
    if (!el || !ticker) { return; }
    if (el.dataset.plotlyState === "rendered") {
      delete el.dataset.plotlyLinked;
      selectMenuButton(el, ticker, "name");
    } else if (el.dataset.plotlyState !== "error") {
      el.dataset.plotlyLinked = ticker;
    }
  }

  /* ------------------------------------------------------------------ wiring */

  function renderWithin(scope) {
    scope.querySelectorAll("[data-plotly]").forEach(renderPlot);
    scope.querySelectorAll("[data-price-explorer]").forEach(initPriceExplorer);
  }

  function rethemeAll() {
    var map = palette();
    if (window.Plotly) {
      document.querySelectorAll('[data-plotly][data-plotly-state="rendered"]').forEach(function (el) {
        rethemePlot(el, map);
      });
    }
    explorers.forEach(function (retheme) { retheme(); });
  }

  function setSections(open) {
    document.querySelectorAll("details.analysis-section").forEach(function (section) { section.open = open; });
  }

  function init() {
    /* Wait for the web fonts: Plotly measures legend/label text when it draws. */
    var fontsReady = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    fontsReady.then(function () { renderWithin(document); }, function () { renderWithin(document); });

    /* "toggle" does not bubble: listen in the capture phase. Opening draws or resizes its charts. */
    document.addEventListener("toggle", function (event) {
      var target = event.target;
      if (target && target.tagName === "DETAILS" && target.open) { renderWithin(target); }
    }, true);

    document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-expand-all], [data-collapse-all]");
      if (!button) { return; }
      event.preventDefault();
      setSections(button.hasAttribute("data-expand-all"));
    });

    document.addEventListener("change", function (event) {
      var asset = event.target.closest && event.target.closest("select[data-price-asset]");
      var explorer = asset && asset.closest("[data-linked-chart]");
      if (explorer) { followLinkedChart(explorer.dataset.linkedChart, asset.value); return; }
      var control = event.target.closest && event.target.closest("select[data-plotly-control]");
      if (!control) { return; }
      var el = document.querySelector('[data-plotly="' + control.dataset.plotlyControl + '"]');
      if (el && el.dataset.plotlyState === "rendered") { selectMenuButton(el, control.value); }
    });

    document.addEventListener("atlas:themechange", function () {
      /* Let the new data-theme apply before reading the CSS variables. */
      window.requestAnimationFrame(rethemeAll);
    });
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () {
        if (!document.documentElement.hasAttribute("data-theme")) { rethemeAll(); }
      };
      if (query.addEventListener) { query.addEventListener("change", onChange); }
      else if (query.addListener) { query.addListener(onChange); }
    }

    if (!pendingObserver) {
      /* Browsers without ResizeObserver: retry pending charts on resize. */
      window.addEventListener("resize", function () { renderWithin(document); });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
