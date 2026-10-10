/*
 * Atlas · form behaviour (vanilla JS, no dependencies, progressive enhancement).
 * Loaded with `defer` on every page by base.html. Without JS every form still
 * works with the normal POST; this file only enhances the markup of
 * templates/macros/forms.html. Hooks for page authors:
 *
 * [data-universe-select] + <script type="application/json" id="universes">
 *   On `change`, rebuilds the chips of the [data-asset-selector] of the same form
 *   from the payload ({name: {assets: [{ticker, label, group}], default: [...]}}).
 *   Checked tickers present in the new universe stay checked; if none remain the
 *   universe `default` is applied. [data-universe-summary] is updated. If the
 *   payload has no entry for the value, the form's button[name=action][value=refresh]
 *   (rendered with .js-hidden as no-JS fallback) is clicked instead.
 *
 * [data-asset-selector] (macro asset_selector)
 *   Search box (filters by ticker or label, Esc clears, Enter toggles the single
 *   match), counter "N de M seleccionados", Todos / Ninguno / Invertir (act on the
 *   visible chips), group headings when the payload has several groups, and a
 *   "+ Añadir ticker" chip that focuses [data-custom-tickers] (opening a closed
 *   <details> around it). Emits on the selector element (bubbles):
 *     CustomEvent("atlas:selectionchange", {detail: {tickers, initial}})
 *   `tickers` = checked chips (DOM order) + valid custom tickers, de-duplicated.
 *   It is also emitted once on DOMContentLoaded with initial: true, after every
 *   deferred page script has had the chance to register listeners.
 *
 * [data-min-selected="N"] on the selector or on its closest ancestor
 *   (macro parameter min_selected, or a wrapper element written by the page)
 *   While fewer than N tickers are selected the form's primary submit buttons
 *   (button.button-primary or [data-primary-submit], except action=refresh) are
 *   disabled and a message is shown just before the first one.
 *
 * [data-ticker-rows="<template-id>"] + <template id="<template-id>">
 *   Keeps one direct child [data-ticker="X"] per selected ticker, in selection
 *   order. New rows clone the template replacing `__TICKER__` (and `__LABEL__`,
 *   the asset label) in attributes and text. Typed values are kept, including
 *   for rows removed and re-added later. An optional element
 *   [data-ticker-rows-empty="<template-id>"] is shown when there are no rows.
 *   The container may be a <tbody>; the <template> may live inside it.
 *
 * [data-custom-tickers] (macro custom_tickers_input)
 *   Turns the text input into chips: Enter, comma, semicolon, space or paste
 *   create chips; Backspace on an empty field removes the last one. Values are
 *   upper-cased and checked against ^[A-Z0-9^=.\-]{1,24}$; invalid chips are
 *   flagged "Símbolo no válido" and block submission (setCustomValidity). The
 *   submitted value is a hidden input name="custom_tickers" joined by commas.
 *
 * [data-date-presets="<input-id>"] with buttons [data-years="N"]
 *   Writes the date N years ago (ISO, Feb 29 -> Feb 28, clamped to the input
 *   `min`/`max`) and fires input/change. [data-custom-date] focuses the input.
 *   aria-pressed reflects the preset matching the current value.
 *
 * Validation (all forms): number and date inputs get Spanish messages via
 *   setCustomValidity (required, número, rango, fecha máxima) shown inline in a
 *   .field-error after the field once touched or on submit.
 *
 * [data-weight-limits] (HRP)
 *   Container with per-asset inputs min_<T>/max_<T>; the globals min_weight /
 *   max_weight are looked up inside it, then in the form. Checks min <= max per
 *   asset and globally, and Σmín <= 100 % <= Σmáx over the selected tickers
 *   (empty per-asset value = global), mirroring WeightBounds.resolve. Per-asset
 *   placeholders mirror the global values. A [data-feasibility] element in the
 *   container or form receives a live status (class is-ok / is-error).
 *
 * Tooltips (.help-tip, macro help_tip): hover, keyboard focus or tap opens;
 *   Esc or a click outside closes. Positioned with `position: fixed` so they are
 *   not clipped by scrolling panels.
 *
 * Public API: window.AtlasForms = {selection(selectorOrForm), refresh(root)}.
 */
(function () {
  "use strict";

  var TICKER_PATTERN = /^[A-Z0-9^=.\-]{1,24}$/;
  var SELECTION_EVENT = "atlas:selectionchange";
  var SCROLL_THRESHOLD = 24;
  var doc = document;

  /* ---------- helpers ---------- */

  function $all(root, selector) {
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  function unique(values) {
    var seen = new Set();
    return values.filter(function (value) {
      if (seen.has(value)) return false;
      seen.add(value);
      return true;
    });
  }

  function fold(text) {
    return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  }

  function scopeOf(element) {
    return (element && element.closest("form")) || doc;
  }

  function create(tag, attributes, text) {
    var node = doc.createElement(tag);
    Object.keys(attributes || {}).forEach(function (key) {
      node.setAttribute(key, attributes[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function shortLabel(label, ticker) {
    var text = String(label || "").split("(" + ticker + ")").join("").trim();
    return text && text !== ticker ? text : "";
  }

  function formatNumber(value) {
    return (Math.round(value * 100) / 100).toFixed(2);
  }

  function isValidTicker(ticker) {
    return TICKER_PATTERN.test(ticker);
  }

  function parseTickers(text) {
    return String(text || "")
      .split(/[\s,;]+/)
      .map(function (part) { return part.trim().toUpperCase(); })
      .filter(Boolean);
  }

  var idCounter = 0;
  function ensureId(element, prefix) {
    if (!element.id) {
      idCounter += 1;
      element.id = prefix + "-" + idCounter;
    }
    return element.id;
  }

  function addToken(element, attribute, token) {
    var tokens = (element.getAttribute(attribute) || "").split(/\s+/).filter(Boolean);
    if (tokens.indexOf(token) === -1) tokens.push(token);
    element.setAttribute(attribute, tokens.join(" "));
  }

  function removeToken(element, attribute, token) {
    var tokens = (element.getAttribute(attribute) || "").split(/\s+/).filter(function (item) {
      return item && item !== token;
    });
    if (tokens.length) element.setAttribute(attribute, tokens.join(" "));
    else element.removeAttribute(attribute);
  }

  /* ---------- universes payload ---------- */

  var universesCache;
  function universes() {
    if (universesCache === undefined) {
      var node = doc.getElementById("universes");
      try {
        universesCache = node ? JSON.parse(node.textContent || "{}") : null;
      } catch (error) {
        universesCache = null;
      }
    }
    return universesCache;
  }

  function labelFor(ticker, scope) {
    var chip = $all(scope, ".chip[data-ticker]").filter(function (node) {
      return node.dataset.ticker === ticker;
    })[0];
    if (chip && chip.dataset.label) return chip.dataset.label;
    var data = universes();
    if (data) {
      for (var name in data) {
        var assets = (data[name] && data[name].assets) || [];
        for (var i = 0; i < assets.length; i += 1) {
          if (assets[i].ticker === ticker && assets[i].label) return assets[i].label;
        }
      }
    }
    return ticker;
  }

  /* ---------- asset selector ---------- */

  function checkboxes(selector) {
    return $all(selector, '.chip input[type="checkbox"]');
  }

  function checkedTickers(selector) {
    return checkboxes(selector)
      .filter(function (input) { return input.checked; })
      .map(function (input) { return input.value; });
  }

  var customStates = new WeakMap();

  function customEntry(scope) {
    return scope.querySelector("[data-custom-tickers]");
  }

  function customTickers(scope, validOnly) {
    var entry = customEntry(scope);
    if (!entry) return [];
    var state = customStates.get(entry);
    var tickers = state ? state.tickers.slice() : parseTickers(entry.value);
    return validOnly ? tickers.filter(isValidTicker) : tickers;
  }

  function selectorIn(scope) {
    return scope.querySelector("[data-asset-selector]");
  }

  function selectionOf(selector) {
    var scope = scopeOf(selector);
    var checked = selector ? checkedTickers(selector) : [];
    return unique(checked.concat(customTickers(scope, true)));
  }

  function updateCount(selector, tickers) {
    var boxes = checkboxes(selector);
    var checked = boxes.filter(function (input) { return input.checked; }).length;
    var custom = tickers.length - unique(checkedTickers(selector)).length;
    var selected = selector.querySelector("[data-count-selected]");
    var total = selector.querySelector("[data-count-total]");
    var extra = selector.querySelector("[data-count-custom]");
    if (selected) selected.textContent = String(checked);
    if (total) total.textContent = String(boxes.length);
    if (extra) {
      extra.textContent = custom > 0
        ? " · +" + custom + (custom === 1 ? " personalizado" : " personalizados")
        : "";
    }
  }

  function primaryButtons(form) {
    return $all(form, "[data-primary-submit], button.button-primary").filter(function (button) {
      var type = (button.getAttribute("type") || "submit").toLowerCase();
      return type === "submit" && button.value !== "refresh";
    });
  }

  function updateMinSelected(selector, tickers) {
    var holder = selector.closest("[data-min-selected]");
    var minimum = holder ? parseInt(holder.getAttribute("data-min-selected"), 10) : 0;
    var form = selector.closest("form");
    if (!minimum || !form) return;
    var buttons = primaryButtons(form);
    if (!buttons.length) return;
    var ok = tickers.length >= minimum;
    var messageId = ensureId(selector, "asset-selector") + "-min-message";
    var message = doc.getElementById(messageId);
    if (!message) {
      message = create("p", { id: messageId, class: "form-blocker", role: "status" });
      buttons[0].parentNode.insertBefore(message, buttons[0]);
    }
    var text = minimum === 1
      ? "Selecciona al menos un activo para continuar."
      : "Selecciona al menos " + minimum + " activos para continuar.";
    message.textContent = ok ? "" : text;
    message.hidden = ok;
    selector.classList.toggle("is-insufficient", !ok);
    buttons.forEach(function (button) {
      button.disabled = !ok;
      if (ok) {
        removeToken(button, "aria-describedby", messageId);
        if (button.title === text) button.removeAttribute("title");
      } else {
        addToken(button, "aria-describedby", messageId);
        button.title = text;
      }
    });
  }

  function emitSelection(selector, initial) {
    var tickers = selectionOf(selector);
    updateCount(selector, tickers);
    updateMinSelected(selector, tickers);
    selector.dispatchEvent(new CustomEvent(SELECTION_EVENT, {
      bubbles: true,
      detail: { tickers: tickers, initial: !!initial }
    }));
  }

  /* Emits for the selector of `scope`, or from `fallback` when there is none
     (e.g. a form with custom tickers only). */
  function emitForScope(scope, fallback) {
    var selector = selectorIn(scope);
    if (selector) {
      emitSelection(selector, false);
    } else if (fallback) {
      fallback.dispatchEvent(new CustomEvent(SELECTION_EVENT, {
        bubbles: true,
        detail: { tickers: unique(customTickers(scope, true)), initial: false }
      }));
    }
  }

  function buildChip(asset, name, checked) {
    var ticker = asset.ticker;
    var short = shortLabel(asset.label, ticker);
    var label = create("label", { class: "chip", "data-ticker": ticker });
    if (short) {
      label.dataset.label = asset.label;
      label.title = asset.label;
    }
    var input = create("input", { type: "checkbox", class: "chip__input visually-hidden", name: name, value: ticker });
    input.checked = checked;
    label.appendChild(input);
    label.appendChild(create("span", { class: "chip__check", "aria-hidden": "true" }, "✓"));
    label.appendChild(create("span", { class: "chip__ticker" }, ticker));
    if (short) label.appendChild(create("span", { class: "visually-hidden" }, ", " + short));
    return label;
  }

  function renderSelector(selector, assets, checked) {
    var options = selector.querySelector(".asset-selector__options");
    if (!options) return;
    var name = selector.dataset.name || "tickers";
    var baseId = ensureId(selector, "asset-selector");
    var seen = new Set();
    assets = assets.filter(function (asset) {
      if (!asset || !asset.ticker || seen.has(asset.ticker)) return false;
      seen.add(asset.ticker);
      return true;
    });
    var groups = unique(assets.map(function (asset) { return asset.group || "Otros"; }));
    var grouped = groups.length > 1;
    var addChip = options.querySelector("[data-add-ticker]");
    options.textContent = "";
    if (grouped) {
      groups.forEach(function (group, index) {
        var titleId = baseId + "-group-" + (index + 1);
        var wrapper = create("div", { class: "asset-group", role: "group", "aria-labelledby": titleId });
        wrapper.appendChild(create("p", { class: "asset-group__title", id: titleId }, group));
        var chips = create("div", { class: "asset-group__chips" });
        assets.forEach(function (asset) {
          if ((asset.group || "Otros") === group) chips.appendChild(buildChip(asset, name, checked.has(asset.ticker)));
        });
        wrapper.appendChild(chips);
        options.appendChild(wrapper);
      });
    } else {
      assets.forEach(function (asset) {
        options.appendChild(buildChip(asset, name, checked.has(asset.ticker)));
      });
    }
    if (addChip) options.appendChild(addChip);
    if (grouped) selector.setAttribute("data-grouped", "");
    else selector.removeAttribute("data-grouped");
    options.classList.toggle("asset-selector__options--grouped", grouped);
    options.classList.toggle("asset-selector__options--scroll", assets.length > SCROLL_THRESHOLD);
    applyFilter(selector);
  }

  function chipLabels(selector) {
    return $all(selector, ".chip[data-ticker]");
  }

  function applyFilter(selector) {
    var search = selector.querySelector("[data-asset-search]");
    var raw = search ? search.value.trim() : "";
    var query = fold(raw);
    var visible = 0;
    chipLabels(selector).forEach(function (chip) {
      var haystack = fold(chip.dataset.ticker + " " + (chip.dataset.label || ""));
      var show = !query || haystack.indexOf(query) !== -1;
      chip.hidden = !show;
      if (show) visible += 1;
    });
    $all(selector, ".asset-group").forEach(function (group) {
      group.hidden = !group.querySelector(".chip[data-ticker]:not([hidden])");
    });
    var addChip = selector.querySelector("[data-add-ticker]");
    if (addChip) addChip.hidden = !!query;
    var empty = selector.querySelector("[data-asset-empty]");
    if (empty) {
      empty.hidden = !query || visible > 0;
      empty.textContent = query && !visible
        ? "Ningún activo coincide con «" + raw + "». Puedes añadirlo como ticker personalizado."
        : "";
    }
    return chipLabels(selector).filter(function (chip) { return !chip.hidden; });
  }

  function bulkSelect(selector, mode) {
    applyFilter(selector).forEach(function (chip) {
      var input = chip.querySelector('input[type="checkbox"]');
      if (!input || input.disabled) return;
      input.checked = mode === "all" ? true : mode === "none" ? false : !input.checked;
    });
    emitSelection(selector, false);
  }

  function addTickerChip(selector) {
    var scope = scopeOf(selector);
    var entry = customEntry(scope);
    var options = selector.querySelector(".asset-selector__options");
    if (!entry || !options || options.querySelector("[data-add-ticker]")) return;
    var button = create("button", { type: "button", class: "chip chip--add", "data-add-ticker": "" }, "+ Añadir ticker");
    button.setAttribute("aria-controls", entry.id || ensureId(entry, "custom-tickers"));
    button.addEventListener("click", function () {
      var details = entry.closest("details");
      if (details && !details.open) details.open = true;
      entry.focus();
    });
    options.appendChild(button);
  }

  /* Groups server-rendered chips from #universes when the page did not pass
     `groups` and the chips match the selected universe. */
  function groupFromPayload(selector) {
    if (selector.hasAttribute("data-grouped")) return;
    var scope = scopeOf(selector);
    var select = scope.querySelector("[data-universe-select]");
    var data = universes();
    var entry = select && data && data[select.value];
    if (!entry || !entry.assets) return;
    var groups = unique(entry.assets.map(function (asset) { return asset.group || "Otros"; }));
    if (groups.length < 2) return;
    var current = checkboxes(selector).map(function (input) { return input.value; });
    var expected = entry.assets.map(function (asset) { return asset.ticker; });
    if (current.length !== unique(expected).length) return;
    var known = new Set(expected);
    if (!current.every(function (ticker) { return known.has(ticker); })) return;
    renderSelector(selector, entry.assets, new Set(checkedTickers(selector)));
  }

  function initSelector(selector) {
    if (selector.dataset.enhanced) return;
    selector.dataset.enhanced = "true";
    $all(selector, "[data-asset-search-wrap], [data-asset-actions]").forEach(function (node) {
      node.hidden = false;
    });
    groupFromPayload(selector);
    addTickerChip(selector);
    var search = selector.querySelector("[data-asset-search]");
    if (search) {
      search.addEventListener("input", function () { applyFilter(selector); });
      search.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && search.value) {
          event.preventDefault();
          event.stopPropagation();
          search.value = "";
          applyFilter(selector);
        } else if (event.key === "Enter") {
          event.preventDefault();
          var visible = applyFilter(selector);
          if (visible.length === 1) {
            var input = visible[0].querySelector("input");
            input.checked = !input.checked;
            emitSelection(selector, false);
          }
        }
      });
    }
    selector.addEventListener("click", function (event) {
      var button = event.target.closest("[data-select]");
      if (button && selector.contains(button)) bulkSelect(selector, button.dataset.select);
    });
    selector.addEventListener("change", function (event) {
      if (event.target.matches('.chip input[type="checkbox"]')) emitSelection(selector, false);
    });
  }

  /* ---------- universe select ---------- */

  function universeSummary(entry) {
    var assets = entry.assets || [];
    var groups = unique(assets.map(function (asset) { return asset.group || "Otros"; }));
    var text = assets.length + " activos";
    if (groups.length > 1) {
      text += " · " + groups.slice(0, 3).join(", ");
      if (groups.length > 3) text += " y " + (groups.length - 3) + " más";
    }
    return text;
  }

  function onUniverseChange(select) {
    var scope = scopeOf(select);
    var data = universes();
    var entry = data && data[select.value];
    if (!entry || !entry.assets) {
      var refresh = scope.querySelector('button[name="action"][value="refresh"]');
      if (refresh) refresh.click();
      return;
    }
    var selector = selectorIn(scope);
    if (selector) {
      var current = new Set(checkedTickers(selector));
      var tickers = entry.assets.map(function (asset) { return asset.ticker; });
      var keep = tickers.filter(function (ticker) { return current.has(ticker); });
      if (!keep.length) keep = entry["default"] || tickers;
      renderSelector(selector, entry.assets, new Set(keep));
      emitSelection(selector, false);
    }
    var summaryId = select.getAttribute("aria-describedby");
    var summary = scope.querySelector("[data-universe-summary]") ||
      (summaryId && doc.getElementById(summaryId.split(/\s+/)[0]));
    if (summary) summary.textContent = universeSummary(entry);
  }

  /* ---------- custom tickers ---------- */

  function renderCustom(state) {
    state.list.textContent = "";
    state.tickers.forEach(function (ticker) {
      var valid = isValidTicker(ticker);
      var item = create("li", { class: "ticker-chip" + (valid ? "" : " is-invalid"), "data-value": ticker });
      item.appendChild(create("span", { class: "ticker-chip__text" }, ticker));
      if (!valid) item.appendChild(create("span", { class: "ticker-chip__error" }, "Símbolo no válido"));
      var remove = create("button", { type: "button", class: "ticker-chip__remove", "aria-label": "Quitar " + ticker }, "×");
      remove.addEventListener("click", function () {
        state.tickers = state.tickers.filter(function (value) { return value !== ticker; });
        syncCustom(state);
        state.entry.focus();
      });
      item.appendChild(remove);
      state.list.appendChild(item);
    });
    state.list.hidden = !state.tickers.length;
  }

  function syncCustom(state, silent) {
    state.hidden.value = state.tickers.join(",");
    var invalid = state.tickers.filter(function (ticker) { return !isValidTicker(ticker); });
    var message = invalid.length
      ? (invalid.length === 1 ? "Símbolo no válido: " : "Símbolos no válidos: ") + invalid.join(", ") +
        ". Corrígelo o quítalo antes de calcular."
      : "";
    state.entry.setCustomValidity(message);
    if (invalid.length) state.entry.setAttribute("aria-invalid", "true");
    else state.entry.removeAttribute("aria-invalid");
    state.box.classList.toggle("is-invalid", invalid.length > 0);
    if (state.error) {
      state.error.textContent = message;
      state.error.hidden = !message;
    }
    renderCustom(state);
    if (!silent) emitForScope(scopeOf(state.entry), state.entry);
  }

  function commitCustom(state, text) {
    var added = parseTickers(text);
    if (!added.length) return false;
    added.forEach(function (ticker) {
      if (state.tickers.indexOf(ticker) === -1) {
        state.tickers.push(ticker);
      } else {
        var existing = state.list.querySelector('[data-value="' + ticker.replace(/["\\]/g, "") + '"]');
        if (existing) {
          existing.classList.remove("is-duplicate");
          void existing.offsetWidth;
          existing.classList.add("is-duplicate");
        }
      }
    });
    syncCustom(state);
    return true;
  }

  function initCustomTickers(entry) {
    if (customStates.has(entry)) return;
    var name = entry.getAttribute("name") || "custom_tickers";
    var wrap = entry.closest("[data-custom-tickers-wrap]") || entry.parentNode;
    var hidden = create("input", { type: "hidden", name: name, value: "" });
    entry.removeAttribute("name");
    var box = create("div", { class: "ticker-input__box" });
    var list = create("ul", { class: "ticker-input__chips", role: "list", "aria-label": "Tickers añadidos" });
    entry.parentNode.insertBefore(box, entry);
    box.appendChild(list);
    box.appendChild(entry);
    box.parentNode.insertBefore(hidden, box.nextSibling);
    entry.classList.add("ticker-input__entry");
    var error = wrap.querySelector("[data-custom-tickers-error]");
    if (!error) {
      error = create("p", { class: "field-error", "aria-live": "polite", id: ensureId(entry, "custom-tickers") + "-error" });
      error.hidden = true;
      box.parentNode.insertBefore(error, hidden.nextSibling);
    }
    addToken(entry, "aria-describedby", ensureId(error, "custom-tickers-error"));
    var state = { entry: entry, hidden: hidden, list: list, box: box, error: error, tickers: [] };
    customStates.set(entry, state);
    state.tickers = unique(parseTickers(entry.value));
    entry.value = "";
    syncCustom(state, true);

    box.addEventListener("click", function (event) {
      if (event.target === box || event.target === list) entry.focus();
    });
    entry.addEventListener("keydown", function (event) {
      var key = event.key;
      if ((key === "Enter" || key === "," || key === ";" || key === " ") && entry.value.trim()) {
        event.preventDefault();
        commitCustom(state, entry.value);
        entry.value = "";
      } else if (key === "," || key === ";" || key === " ") {
        event.preventDefault();
      } else if (key === "Backspace" && !entry.value && state.tickers.length) {
        event.preventDefault();
        state.tickers.pop();
        syncCustom(state);
      }
    });
    entry.addEventListener("input", function () {
      if (/[\s,;]/.test(entry.value)) {
        var parts = entry.value.split(/[\s,;]+/);
        var rest = parts.pop();
        commitCustom(state, parts.join(","));
        entry.value = rest;
      }
    });
    entry.addEventListener("paste", function (event) {
      var data = event.clipboardData && event.clipboardData.getData("text");
      if (!data) return;
      event.preventDefault();
      commitCustom(state, entry.value + "," + data);
      entry.value = "";
    });
    entry.addEventListener("blur", function () {
      if (entry.value.trim()) {
        commitCustom(state, entry.value);
        entry.value = "";
      }
    });
    var form = entry.closest("form");
    if (form) {
      form.addEventListener("submit", function () {
        if (entry.value.trim()) {
          commitCustom(state, entry.value);
          entry.value = "";
        }
      }, true);
    }
  }

  /* ---------- dependent rows ---------- */

  var stashedRows = new WeakMap();

  function isRow(node) {
    return node.nodeType === 1 && node.tagName !== "TEMPLATE" && node.hasAttribute("data-ticker");
  }

  function nextRow(node) {
    var current = node;
    while (current && !isRow(current)) current = current.nextSibling;
    return current;
  }

  function replaceTokens(root, ticker, label) {
    function swap(text) {
      return text.split("__TICKER__").join(ticker).split("__LABEL__").join(label);
    }
    var walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    var node = walker.nextNode();
    while (node) {
      if (node.nodeType === 3) {
        if (node.nodeValue.indexOf("__") !== -1) node.nodeValue = swap(node.nodeValue);
      } else {
        Array.prototype.slice.call(node.attributes).forEach(function (attribute) {
          if (attribute.value.indexOf("__") !== -1) node.setAttribute(attribute.name, swap(attribute.value));
        });
      }
      node = walker.nextNode();
    }
  }

  function createRow(template, ticker, scope) {
    var fragment = template.content.cloneNode(true);
    replaceTokens(fragment, ticker, labelFor(ticker, scope));
    var row = fragment.firstElementChild;
    if (!row) return null;
    row.setAttribute("data-ticker", ticker);
    return row;
  }

  function syncRows(container, tickers) {
    var template = doc.getElementById(container.dataset.tickerRows);
    if (!template || !template.content) return;
    var scope = scopeOf(container);
    var existing = new Map();
    Array.prototype.slice.call(container.children).forEach(function (child) {
      if (isRow(child)) existing.set(child.getAttribute("data-ticker"), child);
    });
    var stash = stashedRows.get(container) || new Map();
    stashedRows.set(container, stash);
    var cursor = nextRow(container.firstChild);
    tickers.forEach(function (ticker) {
      var row = existing.get(ticker) || stash.get(ticker) || createRow(template, ticker, scope);
      if (!row) return;
      existing.delete(ticker);
      stash.delete(ticker);
      if (row === cursor) {
        cursor = nextRow(cursor.nextSibling);
      } else {
        container.insertBefore(row, cursor);
      }
    });
    existing.forEach(function (row, ticker) {
      stash.set(ticker, row);
      row.parentNode.removeChild(row);
    });
    var empty = doc.querySelector('[data-ticker-rows-empty="' + container.dataset.tickerRows + '"]');
    if (empty) empty.hidden = tickers.length > 0;
  }

  /* ---------- validation ---------- */

  var touched = new WeakSet();

  function errorElementFor(input, createIfMissing) {
    var id = ensureId(input, "field") + "-error";
    var element = doc.getElementById(id);
    if (!element && createIfMissing) {
      element = create("p", { class: "field-error", id: id, "aria-live": "polite" });
      element.hidden = true;
      var anchor = input.closest(".input-affix") || input;
      anchor.parentNode.insertBefore(element, anchor.nextSibling);
    }
    return element;
  }

  function showFieldError(input) {
    var message = input.validationMessage;
    var invalid = !input.validity.valid;
    var shown = invalid && touched.has(input);
    var element = errorElementFor(input, shown);
    if (shown) {
      input.setAttribute("aria-invalid", "true");
      addToken(input, "aria-describedby", element.id);
      element.textContent = message;
      element.hidden = false;
    } else {
      input.removeAttribute("aria-invalid");
      if (element) {
        element.hidden = true;
        element.textContent = "";
        removeToken(input, "aria-describedby", element.id);
      }
    }
    var affix = input.closest(".input-affix");
    if (affix) affix.classList.toggle("is-invalid", shown);
  }

  function formatDate(iso) {
    var parts = String(iso || "").split("-");
    return parts.length === 3 ? parts[2] + "/" + parts[1] + "/" + parts[0] : iso;
  }

  /* Native constraint message in Spanish; returns "" when valid. */
  function nativeMessage(input) {
    input.setCustomValidity("");
    var validity = input.validity;
    if (validity.valid) return "";
    var percent = input.closest(".input-affix") ? " %" : "";
    if (input.type === "date") {
      if (validity.valueMissing) return "Selecciona una fecha.";
      if (validity.badInput) return "Introduce una fecha válida.";
      if (validity.rangeOverflow) return "La fecha no puede ser posterior al " + formatDate(input.max) + ".";
      if (validity.rangeUnderflow) return "La fecha no puede ser anterior al " + formatDate(input.min) + ".";
      return "Introduce una fecha válida.";
    }
    if (validity.valueMissing) return "Completa este campo.";
    if (validity.badInput) return "Introduce un número válido; usa punto como separador decimal (p. ej. 2.5).";
    if (validity.rangeUnderflow) return "El valor debe ser mayor o igual que " + input.min + percent + ".";
    if (validity.rangeOverflow) return "El valor debe ser menor o igual que " + input.max + percent + ".";
    if (validity.stepMismatch) return "El valor no es válido para este campo.";
    return "Revisa este valor.";
  }

  function validateInput(input) {
    input.setCustomValidity(nativeMessage(input));
    showFieldError(input);
  }

  function isValidatable(element) {
    return element && element.matches &&
      element.matches('input[type="number"], input[type="date"]') && !!element.closest("form");
  }

  /* ---------- HRP weight limits ---------- */

  function numberValue(input, fallback) {
    if (!input || input.value.trim() === "") return fallback;
    var value = parseFloat(input.value);
    return isFinite(value) ? value : fallback;
  }

  function limitInputs(container) {
    var result = { min: {}, max: {} };
    $all(container, 'input[name^="min_"], input[name^="max_"]').forEach(function (input) {
      if (input.name === "min_weight" || input.name === "max_weight") return;
      var kind = input.name.slice(0, 3);
      result[kind][input.name.slice(4)] = input;
    });
    return result;
  }

  function checkLimits(container) {
    var scope = scopeOf(container);
    var globalMin = container.querySelector('input[name="min_weight"]') || scope.querySelector('input[name="min_weight"]');
    var globalMax = container.querySelector('input[name="max_weight"]') || scope.querySelector('input[name="max_weight"]');
    var inputs = limitInputs(container);
    var all = [globalMin, globalMax].filter(Boolean);
    Object.keys(inputs.min).forEach(function (t) { all.push(inputs.min[t]); });
    Object.keys(inputs.max).forEach(function (t) { all.push(inputs.max[t]); });
    all.forEach(function (input) { input.setCustomValidity(nativeMessage(input)); });

    var gmin = numberValue(globalMin, 0);
    var gmax = numberValue(globalMax, 100);
    Object.keys(inputs.min).forEach(function (t) { inputs.min[t].placeholder = globalMin ? globalMin.value || "0" : String(gmin); });
    Object.keys(inputs.max).forEach(function (t) { inputs.max[t].placeholder = globalMax ? globalMax.value || "100" : String(gmax); });

    var selector = selectorIn(scope);
    var tickers = selector ? selectionOf(selector) : unique(Object.keys(inputs.min).concat(Object.keys(inputs.max)));
    var errors = [];
    var sumMin = 0;
    var sumMax = 0;
    var custom = false;

    function flag(input, message) {
      if (input && input.validity.valid) input.setCustomValidity(message);
      errors.push(message);
    }

    if (gmin > gmax + 1e-9) flag(globalMin, "El peso mínimo global (" + formatNumber(gmin) + " %) supera al máximo global (" + formatNumber(gmax) + " %).");
    tickers.forEach(function (ticker) {
      var lowInput = inputs.min[ticker];
      var highInput = inputs.max[ticker];
      if ((lowInput && lowInput.value.trim()) || (highInput && highInput.value.trim())) custom = true;
      var low = numberValue(lowInput, gmin);
      var high = numberValue(highInput, gmax);
      sumMin += low;
      sumMax += high;
      if (low > high + 1e-9) {
        flag(lowInput || highInput || globalMin, "El mínimo de " + ticker + " (" + formatNumber(low) + " %) supera su máximo (" + formatNumber(high) + " %).");
      }
    });
    if (tickers.length) {
      if (sumMin > 100 + 1e-7) {
        flag(globalMin || inputs.min[tickers[0]], "La suma de los pesos mínimos (" + formatNumber(sumMin) + " %) supera el 100 %; no existe un portafolio que cumpla los límites.");
      }
      if (sumMax < 100 - 1e-7) {
        flag(globalMax || inputs.max[tickers[0]], "La suma de los pesos máximos (" + formatNumber(sumMax) + " %) es menor que el 100 %; no existe un portafolio totalmente invertido que cumpla los límites.");
      }
    }
    all.forEach(showFieldError);

    var indicator = container.querySelector("[data-feasibility]") || scope.querySelector("[data-feasibility]");
    if (indicator) {
      if (!indicator.hasAttribute("role")) indicator.setAttribute("role", "status");
      indicator.classList.remove("is-ok", "is-error");
      if (!tickers.length) {
        indicator.textContent = "Selecciona activos para comprobar los límites.";
      } else if (errors.length) {
        indicator.classList.add("is-error");
        indicator.textContent = "✕ " + errors[0];
      } else {
        var equal = formatNumber(100 / tickers.length);
        var count = tickers.length === 1 ? "1 activo" : tickers.length + " activos";
        indicator.classList.add("is-ok");
        indicator.textContent = custom
          ? "✓ Límites factibles con " + count + ": Σ mínimos " + formatNumber(sumMin) + " % ≤ 100 % ≤ Σ máximos " + formatNumber(sumMax) + " %."
          : "✓ Límites factibles. Con " + count + ": mínimo global ≤ " + equal + " %, máximo global ≥ " + equal + " %.";
      }
    }
  }

  function limitContainersFor(element) {
    var scope = scopeOf(element);
    return $all(scope, "[data-weight-limits]").filter(function (container) {
      return container.contains(element) || element.name === "min_weight" || element.name === "max_weight";
    });
  }

  /* ---------- date presets ---------- */

  function isoDate(value) {
    var month = String(value.getMonth() + 1).padStart(2, "0");
    var day = String(value.getDate()).padStart(2, "0");
    return value.getFullYear() + "-" + month + "-" + day;
  }

  function yearsAgo(years) {
    var today = new Date();
    var year = today.getFullYear() - years;
    var month = today.getMonth();
    var day = today.getDate();
    if (month === 1 && day === 29 && new Date(year, 1, 29).getMonth() !== 1) day = 28;
    return isoDate(new Date(year, month, day));
  }

  function presetDate(target, years) {
    var value = yearsAgo(years);
    if (target.max && value > target.max) value = target.max;
    if (target.min && value < target.min) value = target.min;
    return value;
  }

  function syncPresets(group, target) {
    var matched = false;
    $all(group, "[data-years]").forEach(function (button) {
      var pressed = !!target.value && presetDate(target, parseInt(button.dataset.years, 10)) === target.value;
      matched = matched || pressed;
      button.setAttribute("aria-pressed", pressed ? "true" : "false");
    });
    var custom = group.querySelector("[data-custom-date]");
    if (custom) custom.setAttribute("aria-pressed", matched ? "false" : "true");
  }

  function initPresets(group) {
    var target = doc.getElementById(group.dataset.datePresets);
    if (!target || group.dataset.enhanced) return;
    group.dataset.enhanced = "true";
    group.hidden = false;
    group.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button || !group.contains(button)) return;
      if (button.hasAttribute("data-custom-date")) {
        var details = target.closest("details");
        if (details && !details.open) details.open = true;
        target.focus();
        if (typeof target.showPicker === "function") {
          try { target.showPicker(); } catch (error) { /* not allowed: focus is enough */ }
        }
        return;
      }
      var years = parseInt(button.dataset.years, 10);
      if (!years) return;
      target.value = presetDate(target, years);
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
    });
    target.addEventListener("input", function () { syncPresets(group, target); });
    target.addEventListener("change", function () { syncPresets(group, target); });
    syncPresets(group, target);
  }

  /* ---------- tooltips ---------- */

  function closeTips(except) {
    $all(doc, ".help-tip.is-open").forEach(function (tip) {
      if (tip !== except) tip.classList.remove("is-open");
    });
  }

  function positionTip(tip) {
    var button = tip.querySelector(".help-tip__button");
    var bubble = tip.querySelector(".help-tip__bubble");
    if (!button || !bubble) return;
    tip.classList.add("is-fixed");
    var anchor = button.getBoundingClientRect();
    var width = bubble.offsetWidth;
    var height = bubble.offsetHeight;
    var gap = 8;
    var margin = 8;
    var below = anchor.top - height - gap < margin;
    var left = anchor.left + anchor.width / 2 - width / 2;
    left = Math.max(margin, Math.min(left, doc.documentElement.clientWidth - width - margin));
    var top = below ? anchor.bottom + gap : anchor.top - height - gap;
    bubble.style.left = Math.round(left) + "px";
    bubble.style.top = Math.round(top) + "px";
    bubble.style.setProperty("--arrow-x", Math.round(anchor.left + anchor.width / 2 - left) + "px");
    tip.classList.toggle("is-below", below);
  }

  function initTooltips() {
    doc.addEventListener("click", function (event) {
      var button = event.target.closest(".help-tip__button");
      if (button) {
        var tip = button.closest(".help-tip");
        closeTips(tip);
        tip.classList.remove("is-dismissed");
        tip.classList.toggle("is-open");
        if (tip.classList.contains("is-open")) positionTip(tip);
        return;
      }
      if (!event.target.closest(".help-tip")) closeTips(null);
    });
    doc.addEventListener("mouseover", function (event) {
      var tip = event.target.closest && event.target.closest(".help-tip");
      if (tip && !tip.contains(event.relatedTarget)) positionTip(tip);
    });
    doc.addEventListener("mouseout", function (event) {
      var tip = event.target.closest && event.target.closest(".help-tip");
      if (tip && !tip.contains(event.relatedTarget)) tip.classList.remove("is-dismissed");
    });
    doc.addEventListener("focusin", function (event) {
      var tip = event.target.closest && event.target.closest(".help-tip");
      if (tip) positionTip(tip);
    });
    doc.addEventListener("focusout", function (event) {
      var tip = event.target.closest && event.target.closest(".help-tip");
      if (tip && !tip.contains(event.relatedTarget)) tip.classList.remove("is-open", "is-dismissed");
    });
    doc.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      var tips = $all(doc, ".help-tip").filter(function (tip) {
        return tip.classList.contains("is-open") || tip.matches(":hover") || tip.contains(doc.activeElement);
      });
      tips.forEach(function (tip) {
        tip.classList.remove("is-open");
        tip.classList.add("is-dismissed");
      });
    });
    window.addEventListener("scroll", function () {
      $all(doc, ".help-tip.is-fixed").forEach(function (tip) {
        if (tip.classList.contains("is-open") || tip.matches(":hover") || tip.contains(doc.activeElement)) positionTip(tip);
      });
    }, { passive: true, capture: true });
    window.addEventListener("resize", function () { closeTips(null); });
  }

  /* ---------- wiring ---------- */

  function onSelectionChange(event) {
    var source = event.target;
    var scope = scopeOf(source);
    var tickers = (event.detail && event.detail.tickers) || [];
    $all(doc, "[data-ticker-rows]").forEach(function (container) {
      if (scopeOf(container) === scope) syncRows(container, tickers);
    });
    $all(doc, "[data-weight-limits]").forEach(function (container) {
      if (scopeOf(container) === scope) checkLimits(container);
    });
  }

  function initGlobalListeners() {
    doc.addEventListener(SELECTION_EVENT, onSelectionChange);
    doc.addEventListener("change", function (event) {
      var target = event.target;
      if (target.matches && target.matches("[data-universe-select]")) onUniverseChange(target);
      if (isValidatable(target)) {
        touched.add(target);
        var containers = limitContainersFor(target);
        if (containers.length) containers.forEach(checkLimits);
        else validateInput(target);
      }
    });
    doc.addEventListener("input", function (event) {
      var target = event.target;
      if (!isValidatable(target)) return;
      var containers = limitContainersFor(target);
      if (containers.length) containers.forEach(checkLimits);
      else validateInput(target);
    });
    doc.addEventListener("focusout", function (event) {
      var target = event.target;
      if (isValidatable(target) && target.value !== "") {
        touched.add(target);
        showFieldError(target);
      }
    });
    doc.addEventListener("invalid", function (event) {
      var target = event.target;
      if (!isValidatable(target)) return;
      touched.add(target);
      if (!target.validity.customError) target.setCustomValidity(nativeMessage(target));
      showFieldError(target);
    }, true);
  }

  function refresh(root) {
    root = root || doc;
    $all(root, "[data-custom-tickers]").forEach(initCustomTickers);
    $all(root, "[data-asset-selector]").forEach(initSelector);
    $all(root, "[data-date-presets]").forEach(initPresets);
    $all(root, 'form input[type="number"], form input[type="date"]').forEach(function (input) {
      input.setCustomValidity(nativeMessage(input));
    });
    var selectors = $all(root, "[data-asset-selector]");
    selectors.forEach(function (selector) { emitSelection(selector, true); });
    $all(root, "[data-weight-limits]").forEach(function (container) {
      if (!selectorIn(scopeOf(container))) checkLimits(container);
    });
  }

  function init() {
    initGlobalListeners();
    initTooltips();
    refresh(doc);
  }

  window.AtlasForms = {
    selection: function (element) {
      var selector = element && element.matches && element.matches("[data-asset-selector]")
        ? element
        : selectorIn(element || doc);
      return selector ? selectionOf(selector) : unique(customTickers(element || doc, true));
    },
    refresh: refresh
  };

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init);
  else init();
})();
