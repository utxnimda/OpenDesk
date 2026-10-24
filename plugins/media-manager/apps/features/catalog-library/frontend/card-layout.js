(function (global) {
  "use strict";

  var attached = typeof WeakMap === "function" ? new WeakMap() : null;
  function number(value) { value = Number(value); return Number.isFinite(value) && value > 0 ? value : 0; }
  function normalizeSize(value) { return ["small", "medium", "large", "auto"].indexOf(value) >= 0 ? value : "medium"; }
  // Array entries are indexed by the number of hidden items, not by item index.
  function overflowWidth(value, hidden) {
    if (typeof value === "function") return number(value(hidden));
    if (Array.isArray(value)) return number(value[hidden]);
    return number(value);
  }
  function total(widths, gap) {
    return widths.reduce(function (sum, width) { return sum + number(width); }, 0) + Math.max(0, widths.length - 1) * gap;
  }
  function fitCount(widths, available, gap, moreWidth, moreGap) {
    widths = Array.isArray(widths) ? widths : [];
    available = number(available); gap = number(gap);
    moreGap = moreGap == null ? gap : number(moreGap);
    var count = widths.length, used = total(widths, gap);
    if (used <= available) return count;
    while (count > 0) {
      used -= number(widths[--count]) + (count ? gap : 0);
      if (used + (count ? moreGap : 0) + overflowWidth(moreWidth, widths.length - count) <= available) return count;
    }
    return 0;
  }
  function fitFacts(widths, priorities, available, gap, moreWidth, forceMore, moreGap) {
    widths = Array.isArray(widths) ? widths : [];
    priorities = Array.isArray(priorities) ? priorities : [];
    available = number(available); gap = number(gap);
    moreGap = moreGap == null ? gap : number(moreGap);
    var indices = widths.map(function (_width, index) { return index; });
    var completeWidth = total(widths, gap) + (forceMore ? (widths.length ? moreGap : 0) + overflowWidth(moreWidth, 0) : 0);
    if (completeWidth <= available) return indices;
    indices.sort(function (a, b) { return (Number(priorities[b]) || 0) - (Number(priorities[a]) || 0) || a - b; });
    var kept = [], used = 0;
    indices.forEach(function (index) {
      var candidate = used + number(widths[index]);
      // Dots separate retained facts; only a plain gap precedes the ellipsis.
      if (candidate + kept.length * gap + moreGap + overflowWidth(moreWidth, widths.length - kept.length - 1) <= available) {
        used = candidate; kept.push(index);
      }
    });
    return kept.sort(function (a, b) { return a - b; });
  }
  function elements(parent, selector) { return Array.prototype.slice.call(parent.querySelectorAll(selector)); }
  function measured(node) { return node && typeof node.getBoundingClientRect === "function" ? number(node.getBoundingClientRect().width) : 0; }
  function computed(node) { return typeof global.getComputedStyle === "function" ? global.getComputedStyle(node) : {}; }
  function px(value) { return number(parseFloat(value)); }
  function contentWidth(node) {
    var style = computed(node);
    return Math.max(0, measured(node) - px(style.paddingLeft) - px(style.paddingRight) - px(style.borderLeftWidth) - px(style.borderRightWidth));
  }
  function rowGap(node, fallback) {
    var value = parseFloat(computed(node).columnGap);
    return Number.isFinite(value) && value >= 0 ? value : fallback;
  }
  function text(node, value) { if (node.textContent !== value) node.textContent = value; }
  function attribute(node, key, value) { if (node.getAttribute(key) !== value) node.setAttribute(key, value); }
  function separators(row, gap) {
    var nodes = elements(row, ".library-card-dot");
    nodes.forEach(function (node) { node.hidden = false; });
    return { nodes: nodes, gap: nodes.length ? measured(nodes[0]) + gap * 2 : gap };
  }
  function syncSeparators(dots, items) {
    var preceding = false;
    items.forEach(function (item, index) {
      if (index && dots[index - 1]) dots[index - 1].hidden = item.hidden || !preceding;
      if (!item.hidden) preceding = true;
    });
  }

  function resourceRow(row) {
    var available = contentWidth(row);
    if (!available) return; // Detached/hidden tabs must retain their last useful layout.
    var chips = elements(row, ".library-press-chip"), more = row.querySelector(".library-press-more");
    if (!more) return;
    if (!chips.length) { more.hidden = true; return; }
    var previous = chips.map(function (chip) { var hidden = chip.hidden; chip.hidden = false; return hidden; });
    var moreHidden = more.hidden, moreText = more.textContent;
    more.hidden = false;
    var widths = chips.map(measured), gap = rowGap(row, 5), dots = separators(row, gap);
    if (widths.some(function (width) { return !width; })) {
      chips.forEach(function (chip, index) { chip.hidden = previous[index]; }); syncSeparators(dots.nodes, chips); more.hidden = moreHidden; text(more, moreText); return;
    }
    var cached = {};
    var visible = fitCount(widths, available, dots.gap, function (hidden) {
      if (!Object.prototype.hasOwnProperty.call(cached, hidden)) { text(more, "+" + hidden); cached[hidden] = measured(more); }
      return cached[hidden];
    }, gap);
    chips.forEach(function (chip, index) { chip.hidden = index >= visible; });
    syncSeparators(dots.nodes, chips);
    var hidden = chips.length - visible;
    more.hidden = !hidden;
    if (hidden) { text(more, "+" + hidden); attribute(more, "aria-label", "另有 " + hidden + " 种压制格式，查看全部资源"); }
  }
  function factsRow(row) {
    var available = contentWidth(row);
    if (!available) return;
    var facts = elements(row, ".library-card-fact"), more = row.querySelector(".library-facts-more");
    if (!more) return;
    var previous = facts.map(function (fact) { var hidden = fact.hidden; fact.hidden = false; return hidden; });
    var moreHidden = more.hidden;
    more.hidden = false; text(more, "…");
    var widths = facts.map(measured), gap = rowGap(row, 4), dots = separators(row, gap);
    if (widths.some(function (width) { return !width; })) {
      facts.forEach(function (fact, index) { fact.hidden = previous[index]; }); syncSeparators(dots.nodes, facts); more.hidden = moreHidden; return;
    }
    var extra = row.getAttribute("data-extra-facts") === "true";
    var kept = fitFacts(widths, facts.map(function (fact) { return Number(fact.getAttribute("data-fact-priority")) || 0; }), available, dots.gap, measured(more), extra, gap);
    facts.forEach(function (fact, index) { fact.hidden = kept.indexOf(index) < 0; });
    syncSeparators(dots.nodes, facts);
    more.hidden = !extra && kept.length === facts.length;
  }
  function attach(grid, options) {
    options = options || {};
    if (attached && attached.has(grid)) attached.get(grid).dispose();
    var size = normalizeSize(options.size), disposed = false, scheduled = false, handle = null, useRaf = false;
    var doc = grid.ownerDocument || global.document, fonts = doc && doc.fonts;
    var previousSize = grid.getAttribute("data-card-size"), previousColumns = grid.style.gridTemplateColumns || "";
    attribute(grid, "data-card-size", size);
    function run() {
      scheduled = false; handle = null;
      if (disposed) return;
      var width = contentWidth(grid);
      if (!width) return;
      var listMode = /(?:^|\s)is-list(?:\s|$)/.test(grid.className || "");
      if (size === "auto" && !listMode) {
        var gap = rowGap(grid, 18), maximum = Math.max(1, Math.floor((width + gap) / (100 + gap)));
        var columns = Math.max(1, Math.min(maximum, Math.round((width + gap) / (160 + gap))));
        var template = "repeat(" + columns + ", minmax(0, 1fr))";
        if (grid.style.gridTemplateColumns !== template) grid.style.gridTemplateColumns = template;
      } else if (size === "auto" && grid.style.gridTemplateColumns !== previousColumns) grid.style.gridTemplateColumns = previousColumns;
      elements(grid, ".library-card-resources").forEach(resourceRow);
      elements(grid, ".library-card-facts").forEach(factsRow);
    }
    function refresh() {
      if (disposed || scheduled) return;
      scheduled = true;
      if (typeof global.requestAnimationFrame === "function") { useRaf = true; handle = global.requestAnimationFrame(run); }
      else if (typeof global.setTimeout === "function") { useRaf = false; handle = global.setTimeout(run, 0); }
      else run();
    }
    var resizeObserver = typeof global.ResizeObserver === "function" ? new global.ResizeObserver(refresh) : null;
    if (resizeObserver) resizeObserver.observe(grid);
    var themeObserver = typeof global.MutationObserver === "function" ? new global.MutationObserver(refresh) : null;
    if (themeObserver && doc) {
      if (doc.documentElement) themeObserver.observe(doc.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme", "data-font", "data-appearance"] });
      if (doc.body && doc.body !== doc.documentElement) themeObserver.observe(doc.body, { attributes: true, attributeFilter: ["class", "style", "data-theme", "data-font", "data-appearance"] });
    }
    if (typeof global.addEventListener === "function") global.addEventListener("resize", refresh);
    if (fonts && typeof fonts.addEventListener === "function") fonts.addEventListener("loadingdone", refresh);
    if (fonts && fonts.ready && typeof fonts.ready.then === "function") fonts.ready.then(refresh, function () {});
    var manager = { refresh: refresh, dispose: function () {
      if (disposed) return;
      disposed = true;
      if (resizeObserver) resizeObserver.disconnect();
      if (themeObserver) themeObserver.disconnect();
      if (typeof global.removeEventListener === "function") global.removeEventListener("resize", refresh);
      if (fonts && typeof fonts.removeEventListener === "function") fonts.removeEventListener("loadingdone", refresh);
      if (handle !== null) {
        if (useRaf && typeof global.cancelAnimationFrame === "function") global.cancelAnimationFrame(handle);
        else if (!useRaf && typeof global.clearTimeout === "function") global.clearTimeout(handle);
      }
      if (size === "auto") grid.style.gridTemplateColumns = previousColumns;
      if (previousSize === null) grid.removeAttribute("data-card-size"); else attribute(grid, "data-card-size", previousSize);
      if (attached && attached.get(grid) === manager) attached.delete(grid);
    } };
    if (attached) attached.set(grid, manager);
    refresh(); return manager;
  }
  global.NimdaLibraryCardLayout = { normalizeSize: normalizeSize, fitCount: fitCount, fitFacts: fitFacts, attach: attach };
})(window);
