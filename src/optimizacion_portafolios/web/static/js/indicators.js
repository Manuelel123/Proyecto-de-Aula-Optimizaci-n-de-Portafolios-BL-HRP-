/* Technical indicators for the price explorer (static/js/charts.js).
 *
 * Lightweight Charts has no official indicator package (its repository only ships source examples), so the
 * calculations live here, written to match TradingView's built-in definitions:
 *   SMA, WMA      arithmetic / linearly weighted mean of the last ``period`` values.
 *   EMA           alpha = 2 / (period + 1), seeded with the SMA of the first ``period`` values.
 *   Bollinger     SMA ± k · population standard deviation (TradingView's ta.stdev).
 *   RSI           Wilder's smoothing (RMA) of gains and losses, seeded with their simple mean.
 *   MACD          EMA(fast) − EMA(slow); signal = EMA(signal) of the MACD line; histogram = MACD − signal.
 * Every function takes an array of numbers and returns arrays of the same length, with null where the
 * indicator is not defined yet (warm-up), so the outputs stay aligned with the input dates.
 *
 * Exposed as window.AtlasIndicators (and module.exports, so the maths can be checked with Node).
 */
(function (root) {
  "use strict";

  function filled(length) {
    var out = new Array(length);
    for (var i = 0; i < length; i += 1) { out[i] = null; }
    return out;
  }

  function sma(values, period) {
    var out = filled(values.length);
    var sum = 0;
    for (var i = 0; i < values.length; i += 1) {
      sum += values[i];
      if (i >= period) { sum -= values[i - period]; }
      if (i >= period - 1) { out[i] = sum / period; }
    }
    return out;
  }

  /* EMA of ``values`` ignoring leading nulls (used for the MACD signal line). */
  function ema(values, period) {
    var out = filled(values.length);
    var alpha = 2 / (period + 1);
    var start = 0;
    while (start < values.length && values[start] == null) { start += 1; }
    if (values.length - start < period) { return out; }
    var seed = 0;
    for (var i = start; i < start + period; i += 1) { seed += values[i]; }
    var previous = seed / period;
    out[start + period - 1] = previous;
    for (var j = start + period; j < values.length; j += 1) {
      previous = alpha * values[j] + (1 - alpha) * previous;
      out[j] = previous;
    }
    return out;
  }

  function wma(values, period) {
    var out = filled(values.length);
    var weights = period * (period + 1) / 2;
    for (var i = period - 1; i < values.length; i += 1) {
      var total = 0;
      for (var k = 0; k < period; k += 1) { total += values[i - k] * (period - k); }
      out[i] = total / weights;
    }
    return out;
  }

  function bollinger(values, period, multiplier) {
    var middle = sma(values, period);
    var upper = filled(values.length);
    var lower = filled(values.length);
    for (var i = period - 1; i < values.length; i += 1) {
      var mean = middle[i];
      var squares = 0;
      for (var k = i - period + 1; k <= i; k += 1) { squares += (values[k] - mean) * (values[k] - mean); }
      var deviation = Math.sqrt(squares / period);
      upper[i] = mean + multiplier * deviation;
      lower[i] = mean - multiplier * deviation;
    }
    return { middle: middle, upper: upper, lower: lower };
  }

  function rsi(values, period) {
    var out = filled(values.length);
    if (values.length <= period) { return out; }
    var gain = 0;
    var loss = 0;
    for (var i = 1; i <= period; i += 1) {
      var change = values[i] - values[i - 1];
      if (change > 0) { gain += change; } else { loss -= change; }
    }
    gain /= period;
    loss /= period;
    out[period] = loss === 0 ? (gain === 0 ? 50 : 100) : 100 - 100 / (1 + gain / loss);
    for (var j = period + 1; j < values.length; j += 1) {
      var delta = values[j] - values[j - 1];
      gain = (gain * (period - 1) + Math.max(delta, 0)) / period;
      loss = (loss * (period - 1) + Math.max(-delta, 0)) / period;
      out[j] = loss === 0 ? (gain === 0 ? 50 : 100) : 100 - 100 / (1 + gain / loss);
    }
    return out;
  }

  function macd(values, fast, slow, signalPeriod) {
    var fastLine = ema(values, fast);
    var slowLine = ema(values, slow);
    var line = values.map(function (_, i) {
      return fastLine[i] == null || slowLine[i] == null ? null : fastLine[i] - slowLine[i];
    });
    var signal = ema(line, signalPeriod);
    var histogram = line.map(function (value, i) {
      return value == null || signal[i] == null ? null : value - signal[i];
    });
    return { macd: line, signal: signal, histogram: histogram };
  }

  /* Catalog shown in the "Indicadores" panel. ``pane``: "price" overlays the price, "oscillator" gets its
   * own pane below it, "volume" draws bars at the bottom of the price pane. ``int`` params are rounded. */
  var DEFINITIONS = [
    { id: "sma", label: "Media móvil simple (SMA)", short: "SMA", pane: "price",
      params: [{ key: "period", label: "Periodo", value: 20, min: 2, max: 500, step: 1, int: true }] },
    { id: "ema", label: "Media móvil exponencial (EMA)", short: "EMA", pane: "price",
      params: [{ key: "period", label: "Periodo", value: 20, min: 2, max: 500, step: 1, int: true }] },
    { id: "wma", label: "Media móvil ponderada (WMA)", short: "WMA", pane: "price",
      params: [{ key: "period", label: "Periodo", value: 20, min: 2, max: 500, step: 1, int: true }] },
    { id: "bb", label: "Bandas de Bollinger", short: "BB", pane: "price",
      params: [
        { key: "period", label: "Periodo", value: 20, min: 2, max: 500, step: 1, int: true },
        { key: "mult", label: "Desviaciones", value: 2, min: 0.5, max: 5, step: 0.1 }
      ] },
    { id: "rsi", label: "Índice de fuerza relativa (RSI)", short: "RSI", pane: "oscillator",
      params: [
        { key: "period", label: "Periodo", value: 14, min: 2, max: 200, step: 1, int: true },
        { key: "upper", label: "Sobrecompra", value: 70, min: 50, max: 100, step: 1, int: true },
        { key: "lower", label: "Sobreventa", value: 30, min: 0, max: 50, step: 1, int: true }
      ] },
    { id: "macd", label: "MACD", short: "MACD", pane: "oscillator",
      params: [
        { key: "fast", label: "Rápida", value: 12, min: 2, max: 200, step: 1, int: true },
        { key: "slow", label: "Lenta", value: 26, min: 3, max: 400, step: 1, int: true },
        { key: "signal", label: "Señal", value: 9, min: 2, max: 200, step: 1, int: true }
      ] },
    { id: "volume", label: "Volumen", short: "Vol.", pane: "volume", needsVolume: true, params: [] }
  ];

  function definition(id) {
    for (var i = 0; i < DEFINITIONS.length; i += 1) {
      if (DEFINITIONS[i].id === id) { return DEFINITIONS[i]; }
    }
    return null;
  }

  /* Valid parameters for ``id`` from user input: defaults for missing values, clamped to [min, max],
   * integers where required and MACD's fast period kept below the slow one. */
  function normalizeParams(id, raw) {
    var def = definition(id);
    var params = {};
    if (!def) { return params; }
    def.params.forEach(function (spec) {
      var value = raw && raw[spec.key] !== undefined && raw[spec.key] !== "" ? Number(raw[spec.key]) : spec.value;
      if (!isFinite(value)) { value = spec.value; }
      value = Math.min(spec.max, Math.max(spec.min, value));
      params[spec.key] = spec.int ? Math.round(value) : Math.round(value * 100) / 100;
    });
    /* fast <= 200 < max(slow), so fast + 1 is always a valid slow period. */
    if (id === "macd" && params.fast >= params.slow) { params.slow = params.fast + 1; }
    if (id === "rsi" && params.lower >= params.upper) { params.lower = def.params[2].value; params.upper = def.params[1].value; }
    return params;
  }

  function shortLabel(id, params) {
    var def = definition(id);
    if (!def) { return id; }
    var values = def.params
      .filter(function (spec) { return id !== "rsi" || spec.key === "period"; })
      .map(function (spec) { return String(params[spec.key]); });
    return values.length ? def.short + " " + values.join(", ") : def.short;
  }

  /* Lines of an indicator: {key: values[]} aligned with ``closes``. */
  function compute(id, closes, params) {
    switch (id) {
      case "sma": return { value: sma(closes, params.period) };
      case "ema": return { value: ema(closes, params.period) };
      case "wma": return { value: wma(closes, params.period) };
      case "bb": return bollinger(closes, params.period, params.mult);
      case "rsi": return { value: rsi(closes, params.period) };
      case "macd": return macd(closes, params.fast, params.slow, params.signal);
      default: return {};
    }
  }

  var api = {
    sma: sma, ema: ema, wma: wma, bollinger: bollinger, rsi: rsi, macd: macd,
    DEFINITIONS: DEFINITIONS, definition: definition, normalizeParams: normalizeParams,
    shortLabel: shortLabel, compute: compute
  };
  if (typeof module === "object" && module.exports) { module.exports = api; }
  root.AtlasIndicators = api;
})(typeof window !== "undefined" ? window : this);
