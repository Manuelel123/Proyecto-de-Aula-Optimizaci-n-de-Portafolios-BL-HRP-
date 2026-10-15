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
 *   [data-ohlc-url]                            on the explorer: daily OHLCV of the selected asset, fetched on
 *                                              demand from ``<url>?ticker=&start=&end=`` for the bar and candle
 *                                              chart types and the volume indicator. When the provider has no
 *                                              OHLC (or no volume) those options are disabled with the reason;
 *                                              nothing is estimated from the closes.
 *   select[data-chart-type]                    chart type: area, line, baseline, histogram, bars, candles.
 *   [data-indicators-toggle] / [data-indicators-panel]
 *                                              technical indicators panel (static/js/indicators.js): add/remove
 *                                              SMA, EMA, WMA, Bollinger, RSI, MACD and volume with editable
 *                                              periods; RSI and MACD get their own pane below the price.
 *                                              Chart type and indicators are remembered in localStorage.
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

  /* Chart types of the [data-chart-type] selector. Bars and candles need the daily OHLC fetched from
   * [data-ohlc-url]; the other types draw the closes of the page payload. */
  var OHLC_TYPES = { bars: true, candles: true };
  var LINE_TYPES = { area: true, line: true, baseline: true, histogram: true };
  var STORAGE_KEY = "atlas:price-explorer:v1";
  var MAX_STUDIES = 8;
  /* Height of each oscillator pane relative to the price pane (the canvas grows by one step per pane). */
  var OSCILLATOR_STRETCH = 0.4;
  var SERIES_FALLBACK = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
  var compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });

  /* Chart type and indicators are remembered per browser (a convenience; everything works without it). */
  function loadPreferences() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var prefs = raw ? JSON.parse(raw) : null;
      return prefs && typeof prefs === "object" ? prefs : {};
    } catch (error) { return {}; }
  }

  function savePreferences(prefs) {
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch (error) { /* storage blocked */ }
  }

  function explorerColors() {
    var series = cssVar("--series-1", SERIES_FALLBACK[0]);
    var studies = [];
    /* Indicators take slots 2..8 of the categorical palette (slot 1 is the price). */
    for (var slot = 2; slot <= 8; slot += 1) { studies.push(cssVar("--series-" + slot, SERIES_FALLBACK[slot - 1])); }
    return {
      series: series,
      fill: cssVar("--series-1-fill", withAlpha(series, 0.22)),
      fillEnd: cssVar("--series-1-fill-end", withAlpha(series, 0)),
      text3: cssVar("--text-3", "#5f6b7e"),
      border: cssVar("--border", "#e3e7ed"),
      inverse: cssVar("--surface-inverse", "#0f172a"),
      positive: cssVar("--positive-chart", "#16a34a"),
      negative: cssVar("--negative-chart", "#dc2626"),
      studies: studies
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
        attributionLogo: true,
        panes: { separatorColor: colors.border, separatorHoverColor: withAlpha(colors.text3, 0.15), enableResize: true }
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

  function priceFormat(precision) {
    return { type: "price", precision: precision, minMove: Math.pow(10, -precision) };
  }

  /* [series definition, options] of the main series for a chart type. ``basePrice``: baseline level;
   * ``floor``: base of the histogram columns (lowest close, so the columns show the variation). */
  function mainSeriesSpec(LW, type, colors, precision, basePrice, floor) {
    var common = {
      priceLineVisible: true,
      priceLineStyle: LW.LineStyle.Dotted,
      priceLineColor: colors.text3,
      lastValueVisible: true,
      priceFormat: priceFormat(precision)
    };
    switch (type) {
      case "line":
        return [LW.LineSeries, Object.assign(common, { color: colors.series, lineWidth: 2, crosshairMarkerRadius: 4 })];
      case "baseline":
        return [LW.BaselineSeries, Object.assign(common, {
          baseValue: { type: "price", price: basePrice },
          lineWidth: 2,
          topLineColor: colors.positive,
          topFillColor1: withAlpha(colors.positive, 0.22),
          topFillColor2: withAlpha(colors.positive, 0.02),
          bottomLineColor: colors.negative,
          bottomFillColor1: withAlpha(colors.negative, 0.02),
          bottomFillColor2: withAlpha(colors.negative, 0.22)
        })];
      case "histogram":
        return [LW.HistogramSeries, Object.assign(common, { color: withAlpha(colors.series, 0.75), base: floor })];
      case "bars":
        return [LW.BarSeries, Object.assign(common, { upColor: colors.positive, downColor: colors.negative, thinBars: false })];
      case "candles":
        return [LW.CandlestickSeries, Object.assign(common, {
          upColor: colors.positive, downColor: colors.negative, borderVisible: false,
          wickUpColor: colors.positive, wickDownColor: colors.negative
        })];
      default:
        return [LW.AreaSeries, Object.assign(common, {
          lineColor: colors.series, topColor: colors.fill, bottomColor: colors.fillEnd,
          lineWidth: 2, crosshairMarkerRadius: 4
        })];
    }
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

  /* Rows of the main series from a columnar source ({time, close[, open, high, low]}). */
  function priceRows(source, factor, ohlc) {
    return source.time.map(function (time, i) {
      if (ohlc) {
        return {
          time: time, open: source.open[i] * factor, high: source.high[i] * factor,
          low: source.low[i] * factor, close: source.close[i] * factor
        };
      }
      return { time: time, value: source.close[i] * factor };
    });
  }

  /* Line data from aligned values; warm-up nulls are left out. */
  function linePoints(times, values) {
    var rows = [];
    for (var i = 0; i < times.length; i += 1) {
      if (values[i] != null && isFinite(values[i])) { rows.push({ time: times[i], value: values[i] }); }
    }
    return rows;
  }

  function lastDefined(values) {
    for (var i = values.length - 1; i >= 0; i -= 1) { if (values[i] != null) { return values[i]; } }
    return null;
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
    var index = indexAt(entry.time, iso);
    return index < 0 ? null : entry.value[index];
  }

  function indexAt(times, iso) {
    var low = 0;
    var high = times.length - 1;
    var found = -1;
    while (low <= high) {
      var mid = (low + high) >> 1;
      if (times[mid] <= iso) { found = mid; low = mid + 1; } else { high = mid - 1; }
    }
    return found;
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
    var typeSelect = root.querySelector("[data-chart-type]");
    var typeNote = root.querySelector("[data-chart-note]");
    var studiesToggle = root.querySelector("[data-indicators-toggle]");
    var studiesPanel = root.querySelector("[data-indicators-panel]");
    var studiesCount = root.querySelector("[data-indicators-count]");
    var studyChoice = root.querySelector("[data-indicator-choice]");
    var studyAdd = root.querySelector("[data-indicator-add]");
    var studyList = root.querySelector("[data-indicator-list]");
    var studiesEmpty = root.querySelector("[data-indicators-empty]");
    var ohlcUrl = window.fetch ? root.dataset.ohlcUrl : null;
    var Studies = window.AtlasIndicators;
    var linkedChart = root.dataset.linkedChart
      ? document.querySelector('[data-plotly="' + root.dataset.linkedChart + '"]') : null;
    var benchmark = payload.benchmark && payload.benchmark.time && payload.benchmark.time.length
      ? payload.benchmark : null;
    var byTicker = {};
    payload.series.forEach(function (entry) { byTicker[entry.ticker] = entry; });
    var analysisByTicker = Object.assign({}, byTicker);
    var fullHistory = {};
    var ohlcCache = {};
    var prefs = loadPreferences();

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
    var initialType = typeSelect ? typeSelect.value : "area";
    if (OHLC_TYPES[prefs.type] || LINE_TYPES[prefs.type]) { initialType = prefs.type; }

    var state = {
      ticker: select && byTicker[select.value] ? select.value : payload.series[0].ticker,
      range: activeButton ? activeButton.dataset.range : "ALL",
      base100: Boolean(base100 && base100.checked && benchmark),
      main: null,
      bench: null,
      full: false,
      /* Chart type chosen by the user (kept when an asset lacks OHLC, so the next asset with OHLC shows it
       * again), and the line type shown while/when OHLC is not available. */
      type: initialType,
      lineType: LINE_TYPES[initialType] ? initialType : "area",
      /* What the last draw used: series type, source columns and base-100 factor. */
      drawn: [],
      watermarks: [],
      shown: { type: "area", source: null, factor: 1 },
      studies: [],
      studyLines: [],
      nextUid: 1
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

    /* ---------- OHLC on demand (bars, candles, volume) */

    function ohlcKey(entry) {
      return entry.ticker + "|" + entry.time[0] + "|" + entry.time[entry.time.length - 1];
    }

    /* Cached OHLC state of an entry ({status: "loading" | "ready" | "error", data, message}) or null. */
    function peekOhlc(entry) {
      if (!ohlcUrl) {
        return { status: "error", message: "Este gráfico no tiene acceso a precios de apertura, máximo, mínimo ni volumen." };
      }
      return ohlcCache[ohlcKey(entry)] || null;
    }

    function requestOhlc(entry) {
      var known = peekOhlc(entry);
      if (known && !(known.status === "error" && known.retry)) { return known; }
      var key = ohlcKey(entry);
      var loading = { status: "loading" };
      ohlcCache[key] = loading;
      var url = ohlcUrl + (ohlcUrl.indexOf("?") < 0 ? "?" : "&") + "ticker=" + encodeURIComponent(entry.ticker) +
        "&start=" + entry.time[0] + "&end=" + entry.time[entry.time.length - 1];
      fetch(url, { headers: { Accept: "application/json" } }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok || !Array.isArray(body.time)) {
            var error = new Error(body.error || "No se pudieron descargar los precios OHLC de " + entry.ticker + ".");
            /* 404 = the provider has no OHLC for this window (final); other failures can be retried. */
            error.retry = response.status !== 404 && response.status !== 400;
            throw error;
          }
          return body;
        });
      }).then(function (data) {
        ohlcCache[key] = { status: "ready", data: data };
      }, function (error) {
        ohlcCache[key] = { status: "error", message: error.message, retry: error.retry !== false };
      }).then(function () {
        if (ohlcKey(current()) === key) { draw(true); }
      });
      return loading;
    }

    /* Why bars/candles cannot be drawn for the current asset ("" = available or not known yet). */
    function ohlcUnavailable(info) {
      if (!info) { return ""; }
      if (info.status === "error") { return info.message; }
      if (info.status === "ready" && !info.data.ohlc) { return info.data.ohlcReason || "Sin datos de apertura, máximo y mínimo."; }
      return "";
    }

    function syncTypeOptions(reason) {
      if (!typeSelect) { return; }
      Array.prototype.forEach.call(typeSelect.options, function (option) {
        if (!option.hasAttribute("data-needs-ohlc")) { return; }
        if (option.dataset.label === undefined) { option.dataset.label = option.textContent; }
        option.disabled = Boolean(reason);
        option.textContent = option.dataset.label + (reason ? " (no disponible)" : "");
        option.title = reason || "";
      });
    }

    function setNote(text, isError) {
      if (!typeNote) { return; }
      typeNote.textContent = text || "";
      typeNote.hidden = !text;
      typeNote.classList.toggle("is-error", Boolean(isError));
    }

    /* ---------- series bookkeeping */

    function addSeries(definition, options, pane) {
      /* addSeries creates the pane when ``pane`` equals the current number of panes. */
      var series = chart.addSeries(definition, options, pane);
      state.drawn.push({ series: series, pane: pane });
      return series;
    }

    function clearSeries() {
      state.watermarks.forEach(function (mark) { try { mark.detach(); } catch (error) { /* pane gone */ } });
      state.watermarks = [];
      /* A pane left without series is removed by the library. */
      state.drawn.forEach(function (item) { chart.removeSeries(item.series); });
      for (var index = chart.panes().length - 1; index > 0; index -= 1) { chart.removePane(index); }
      state.drawn = [];
      state.studyLines = [];
      state.main = null;
      state.bench = null;
    }

    function paneLabel(pane, text) {
      if (!LW.createTextWatermark) { return; }
      var target = chart.panes()[pane];
      if (!target) { return; }
      state.watermarks.push(LW.createTextWatermark(target, {
        horzAlign: "left",
        vertAlign: "top",
        lines: [{ text: text, color: colors.text3, fontSize: 11, fontFamily: "Inter, system-ui, sans-serif" }]
      }));
    }

    /* ---------- indicators */

    function studyColor(study) { return colors.studies[study.slot % colors.studies.length]; }

    function studyLine(times, values, options, pane, name) {
      var series = addSeries(LW.LineSeries, Object.assign({
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false
      }, options), pane);
      series.setData(linePoints(times, values));
      return { series: series, name: name || "", last: lastDefined(values) };
    }

    /* Draws every active indicator over ``times``/``closes`` (already in the plotted scale) and returns
     * the number of oscillator panes. ``ohlc``: the OHLCV payload when loaded (volume), else null. */
    function drawStudies(times, closes, ohlc, precision) {
      var pane = 0;
      var hasVolume = false;
      state.studies.forEach(function (study) {
        var def = Studies.definition(study.id);
        var color = studyColor(study);
        var label = Studies.shortLabel(study.id, study.params);
        var lines = [];
        study.status = "";
        if (def.pane === "volume") {
          var info = requestOhlc(current());
          if (info.status === "loading") { study.status = "Cargando el volumen…"; }
          else if (info.status === "error") { study.status = info.message; }
          else if (!info.data.hasVolume) { study.status = info.data.volumeReason || "Sin datos de volumen."; }
          else {
            var data = info.data;
            var series = addSeries(LW.HistogramSeries, {
              priceScaleId: "volume",
              priceFormat: { type: "volume" },
              priceLineVisible: false,
              lastValueVisible: false
            }, 0);
            series.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: false });
            var rows = [];
            for (var i = 0; i < data.time.length; i += 1) {
              if (data.volume[i] == null) { continue; }
              var up = data.open ? data.close[i] >= data.open[i] : i === 0 || data.close[i] >= data.close[i - 1];
              rows.push({ time: data.time[i], value: data.volume[i], color: withAlpha(up ? colors.positive : colors.negative, 0.4) });
            }
            series.setData(rows);
            hasVolume = true;
            lines.push({ series: series, name: "", last: rows.length ? rows[rows.length - 1].value : null, volume: true });
          }
        } else if (def.pane === "price") {
          var out = Studies.compute(study.id, closes, study.params);
          var format = priceFormat(precision);
          if (study.id === "bb") {
            lines.push(studyLine(times, out.upper, { color: color, lineWidth: 1, priceFormat: format }, 0, "sup."));
            lines.push(studyLine(times, out.middle, { color: color, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, priceFormat: format }, 0, "media"));
            lines.push(studyLine(times, out.lower, { color: color, lineWidth: 1, priceFormat: format }, 0, "inf."));
          } else {
            lines.push(studyLine(times, out.value, { color: color, priceFormat: format }, 0));
          }
        } else {
          pane += 1;
          var result = Studies.compute(study.id, closes, study.params);
          if (study.id === "rsi") {
            var rsiLine = studyLine(times, result.value, {
              color: color,
              lastValueVisible: true,
              priceFormat: priceFormat(2),
              autoscaleInfoProvider: function () { return { priceRange: { minValue: 0, maxValue: 100 } }; }
            }, pane);
            [study.params.upper, study.params.lower].forEach(function (level) {
              rsiLine.series.createPriceLine({
                price: level, color: colors.text3, lineWidth: 1, lineStyle: LW.LineStyle.Dashed,
                axisLabelVisible: true, title: ""
              });
            });
            lines.push(rsiLine);
          } else {
            var macdFormat = priceFormat(Math.max(2, precision));
            var histogram = addSeries(LW.HistogramSeries, {
              priceLineVisible: false, lastValueVisible: false, priceFormat: macdFormat
            }, pane);
            var bars = [];
            for (var k = 0; k < times.length; k += 1) {
              var value = result.histogram[k];
              if (value == null) { continue; }
              bars.push({ time: times[k], value: value, color: withAlpha(value >= 0 ? colors.positive : colors.negative, 0.5) });
            }
            histogram.setData(bars);
            lines.push(studyLine(times, result.macd, { color: color, lastValueVisible: true, priceFormat: macdFormat }, pane, "MACD"));
            lines.push(studyLine(times, result.signal, { color: colors.text3, lineWidth: 1, priceFormat: macdFormat }, pane, "señal"));
            lines.push({ series: histogram, name: "hist.", last: lastDefined(result.histogram) });
          }
          paneLabel(pane, label);
        }
        state.studyLines.push({ study: study, label: label, color: color, lines: lines, precision: def.pane === "oscillator" ? (study.id === "rsi" ? 2 : Math.max(2, precision)) : precision });
      });
      if (state.main) {
        state.main.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: hasVolume ? 0.24 : 0.08 } });
      }
      return pane;
    }

    function studiesNeedOhlc() {
      return state.studies.some(function (study) { return study.id === "volume"; });
    }

    /* ---------- legend */

    function studyLegend(param) {
      var box = document.createElement("span");
      box.className = "price-explorer__study-values";
      state.studyLines.forEach(function (item) {
        var node = document.createElement("span");
        node.className = "price-explorer__study-value";
        node.style.setProperty("--swatch", item.color);
        var values = item.lines.map(function (line) {
          var point = param && param.seriesData ? param.seriesData.get(line.series) : null;
          var value = param && param.time ? (point && point.value != null ? point.value : null) : line.last;
          if (value == null) { return "—"; }
          return line.volume ? compactNumber.format(value) : formatNumber(value, item.precision);
        });
        node.textContent = item.label + " " + (values.length ? values.join(" · ") : "—");
        if (item.study.status) { node.textContent = item.label + " · no disponible"; }
        box.appendChild(node);
      });
      return box;
    }

    /* Legend: ticker, name, price at the cursor (or last), change since the start of the range, the OHLC
     * of the day for bars/candles and the value of every indicator. */
    function updateLegend(iso, price, param) {
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
      var source = state.shown.source;
      if (OHLC_TYPES[state.shown.type] && source) {
        var index = indexAt(source.time, day);
        if (index >= 0) {
          var digits = precisionFor(entry);
          var f = state.shown.factor;
          legend.appendChild(legendItem("price-explorer__ohlc",
            "A " + formatNumber(source.open[index] * f, digits) + " · Máx " + formatNumber(source.high[index] * f, digits) +
            " · Mín " + formatNumber(source.low[index] * f, digits) + " · C " + formatNumber(source.close[index] * f, digits)));
        }
      }
      if (state.studyLines.length) { legend.appendChild(studyLegend(param)); }
    }

    /* ---------- drawing */

    function applyRange() {
      if (state.range === "ALL") { chart.timeScale().fitContent(); return; }
      try { chart.timeScale().setVisibleRange(period()); } catch (error) { chart.timeScale().fitContent(); }
    }

    /* Rebuilds every series. ``keepView`` keeps the visible dates (type, indicator and theme changes). */
    function draw(keepView) {
      var entry = current();
      var range = period();
      var view = null;
      if (keepView && state.main) {
        try { view = chart.timeScale().getVisibleRange(); } catch (error) { view = null; }
      }
      clearSeries();
      var factor = state.base100 ? 100 / baseValue(entry, range.from) : 1;
      var type = state.type;
      var note = "";
      var noteIsError = false;
      var ohlc = null;
      if (OHLC_TYPES[type]) {
        var info = requestOhlc(entry);
        var reason = ohlcUnavailable(info);
        if (info.status === "ready" && !reason) {
          ohlc = info.data;
        } else if (info.status === "loading") {
          note = "Cargando apertura, máximo y mínimo de " + entry.ticker + "…";
          type = state.lineType;
        } else {
          /* Not available: the line type is drawn, with the explanation next to the selector. A failed
           * download drops the choice (choosing it again retries); missing data keeps it for other assets. */
          note = "Barras y velas no disponibles para " + entry.ticker + ". " + reason;
          noteIsError = info.status === "error";
          if (info.status === "error" && info.retry) { state.type = state.lineType; }
          type = state.lineType;
        }
      }
      var known = peekOhlc(entry);
      syncTypeOptions(known && known.status === "error" && known.retry ? "" : ohlcUnavailable(known));
      if (typeSelect) { typeSelect.value = OHLC_TYPES[state.type] && type !== state.type && !ohlcUnavailable(known) ? state.type : type; }
      if (!note && ohlcUrl && ohlcUnavailable(known)) {
        note = "Barras y velas no disponibles para " + entry.ticker + ". " + ohlcUnavailable(known);
      }
      var source = ohlc || { time: entry.time, close: entry.value };
      var closes = source.close.map(function (value) { return value * factor; });
      var precision = precisionFor(entry);
      var floor = Infinity;
      closes.forEach(function (value) { if (value < floor) { floor = value; } });
      var spec = mainSeriesSpec(LW, type, colors, precision, state.base100 ? 100 : baseValue(entry, range.from), floor);
      state.main = addSeries(spec[0], spec[1], 0);
      state.main.setData(priceRows(source, factor, Boolean(ohlc)));
      state.shown = { type: type, source: source, factor: factor };
      if (state.base100 && benchmark && benchmark.ticker !== entry.ticker) {
        state.bench = addSeries(LW.LineSeries, Object.assign(benchmarkOptions(LW, colors), { title: benchmark.ticker }), 0);
        state.bench.setData(points(benchmark, baseValue(benchmark, range.from)));
      }
      var oscillators = Studies ? drawStudies(source.time, closes, ohlc, precision) : 0;
      canvas.style.setProperty("--oscillator-panes", String(oscillators));
      chart.panes().forEach(function (pane, index) {
        if (pane.setStretchFactor) { pane.setStretchFactor(index === 0 ? 1 : OSCILLATOR_STRETCH); }
      });
      if (view) {
        try { chart.timeScale().setVisibleRange(view); } catch (error) { applyRange(); }
      } else {
        applyRange();
      }
      setNote(note, noteIsError);
      refreshStudyStatus();
      updateLegend(null, null, null);
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

    /* ---------- chart type selector and indicators panel */

    function persist() {
      savePreferences({
        type: state.type,
        studies: state.studies.map(function (study) { return { id: study.id, params: study.params, slot: study.slot }; })
      });
    }

    function freeSlot() {
      var used = state.studies.map(function (study) { return study.slot; });
      for (var slot = 0; slot < colors.studies.length; slot += 1) { if (used.indexOf(slot) < 0) { return slot; } }
      return state.studies.length % colors.studies.length;
    }

    function addStudy(id, params, slot) {
      if (!Studies || !Studies.definition(id) || state.studies.length >= MAX_STUDIES) { return null; }
      var study = {
        uid: state.nextUid++,
        id: id,
        params: Studies.normalizeParams(id, params || {}),
        slot: typeof slot === "number" && slot >= 0 ? slot % colors.studies.length : freeSlot(),
        status: ""
      };
      state.studies.push(study);
      return study;
    }

    function numberInput(study, spec) {
      var label = document.createElement("label");
      label.className = "price-explorer__param";
      var text = document.createElement("span");
      text.textContent = spec.label;
      var input = document.createElement("input");
      input.type = "number";
      input.className = "input";
      input.min = spec.min;
      input.max = spec.max;
      input.step = spec.step;
      input.value = study.params[spec.key];
      input.inputMode = spec.int ? "numeric" : "decimal";
      input.dataset.param = spec.key;
      input.addEventListener("change", function () {
        var raw = {};
        label.parentNode.querySelectorAll("input[data-param]").forEach(function (field) { raw[field.dataset.param] = field.value; });
        study.params = Studies.normalizeParams(study.id, raw);
        label.parentNode.querySelectorAll("input[data-param]").forEach(function (field) { field.value = study.params[field.dataset.param]; });
        persist();
        draw(true);
      });
      label.appendChild(text);
      label.appendChild(input);
      return label;
    }

    function renderStudyList() {
      if (!studyList) { return; }
      studyList.innerHTML = "";
      state.studies.forEach(function (study) {
        var def = Studies.definition(study.id);
        var item = document.createElement("li");
        item.className = "price-explorer__study";
        item.dataset.uid = String(study.uid);
        var head = document.createElement("div");
        head.className = "price-explorer__study-head";
        var swatch = document.createElement("span");
        swatch.className = "price-explorer__swatch";
        swatch.setAttribute("aria-hidden", "true");
        var name = document.createElement("span");
        name.className = "price-explorer__study-name";
        name.textContent = def.label + (def.pane === "oscillator" ? " · panel inferior" : "");
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "price-explorer__remove";
        remove.setAttribute("aria-label", "Quitar " + def.label);
        remove.title = "Quitar";
        remove.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
        remove.addEventListener("click", function () {
          state.studies = state.studies.filter(function (other) { return other !== study; });
          persist();
          renderStudyList();
          draw(true);
          if (studyChoice) { studyChoice.focus(); }
        });
        head.appendChild(swatch);
        head.appendChild(name);
        head.appendChild(remove);
        item.appendChild(head);
        if (def.params.length) {
          var params = document.createElement("div");
          params.className = "price-explorer__params";
          def.params.forEach(function (spec) { params.appendChild(numberInput(study, spec)); });
          item.appendChild(params);
        }
        var status = document.createElement("p");
        status.className = "price-explorer__study-status";
        status.setAttribute("role", "status");
        status.hidden = true;
        item.appendChild(status);
        studyList.appendChild(item);
      });
      var count = state.studies.length;
      if (studiesEmpty) { studiesEmpty.hidden = count > 0; }
      if (studiesCount) { studiesCount.hidden = !count; studiesCount.textContent = String(count); }
      if (studyAdd) {
        studyAdd.disabled = count >= MAX_STUDIES;
        studyAdd.title = count >= MAX_STUDIES ? "Máximo " + MAX_STUDIES + " indicadores a la vez" : "";
      }
      refreshStudyStatus();
    }

    /* Swatch colors (theme) and the "not available / loading" message of every listed indicator. */
    function refreshStudyStatus() {
      if (!studyList) { return; }
      state.studies.forEach(function (study) {
        var item = studyList.querySelector('[data-uid="' + study.uid + '"]');
        if (!item) { return; }
        item.querySelector(".price-explorer__swatch").style.background = studyColor(study);
        var status = item.querySelector(".price-explorer__study-status");
        status.textContent = study.status || "";
        status.hidden = !study.status;
        item.classList.toggle("is-unavailable", Boolean(study.status) && !/^Cargando/.test(study.status));
      });
    }

    function setupStudies() {
      if (!Studies || !studiesToggle || !studiesPanel) {
        if (studiesToggle) { studiesToggle.hidden = true; }
        return;
      }
      if (studyChoice) {
        studyChoice.innerHTML = "";
        Studies.DEFINITIONS.forEach(function (def) {
          var option = document.createElement("option");
          option.value = def.id;
          option.textContent = def.label;
          studyChoice.appendChild(option);
        });
      }
      (Array.isArray(prefs.studies) ? prefs.studies : []).forEach(function (saved) {
        if (saved && typeof saved.id === "string") { addStudy(saved.id, saved.params, saved.slot); }
      });
      studiesToggle.addEventListener("click", function () {
        var open = studiesPanel.hidden;
        studiesPanel.hidden = !open;
        studiesToggle.setAttribute("aria-expanded", open ? "true" : "false");
        studiesToggle.classList.toggle("is-active", open);
        if (open && studyChoice) { studyChoice.focus(); }
      });
      if (studyAdd && studyChoice) {
        studyAdd.addEventListener("click", function () {
          var study = addStudy(studyChoice.value);
          if (!study) { return; }
          persist();
          renderStudyList();
          draw(true);
        });
      }
      renderStudyList();
    }

    if (typeSelect) {
      typeSelect.value = state.type;
      typeSelect.addEventListener("change", function () {
        var type = typeSelect.value;
        if (!OHLC_TYPES[type] && !LINE_TYPES[type]) { return; }
        state.type = type;
        if (LINE_TYPES[type]) { state.lineType = type; }
        persist();
        draw(true);
      });
    }
    setupStudies();

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
      if (!param || !param.time || !state.main) { updateLegend(null, null, null); return; }
      var data = param.seriesData && param.seriesData.get(state.main);
      var value = null;
      if (data) { value = data.value != null ? data.value : (data.close != null ? data.close : null); }
      if (value != null && state.base100) {
        /* The series holds base-100 values: convert back to a price. */
        value = value / 100 * baseValue(current(), period().from);
      }
      updateLegend(timeKey(param.time), value, param);
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
        /* In base 100 the base moves with the range start, so the data is rebuilt. Baseline also
         * re-anchors at the first price of the range. */
        if (state.base100 || state.shown.type === "baseline") { draw(); } else { applyRange(); updateLegend(null, null, null); }
      });
    });

    explorers.push(function retheme() {
      colors = explorerColors();
      chart.applyOptions(chartOptions(LW, colors));
      draw(true);
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
