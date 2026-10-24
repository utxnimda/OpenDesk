(function (global) {
  "use strict";
  var current = null, sequence = 0, tooltipManagers = [];
  function connected(node) { return !!node && (typeof node.isConnected === "boolean" ? node.isConnected : typeof document.contains === "function" && document.contains(node)); }
  function contains(parent, node) { return !!parent && !!node && (parent === node || typeof parent.contains === "function" && parent.contains(node)); }
  function element(tag, className, value) { var node = document.createElement(tag); node.className = className; if (value != null) node.textContent = value; return node; }
  function focus(node) { if (connected(node) && typeof node.focus === "function") node.focus({ preventScroll: true }); }
  function close(restoreFocus) {
    if (!current) return;
    var state = current; current = null;
    document.removeEventListener("pointerdown", state.outside, true);
    document.removeEventListener("click", state.outside, true);
    document.removeEventListener("keydown", state.keydown, true);
    document.removeEventListener("scroll", state.scroll, true);
    global.removeEventListener("resize", state.resize);
    state.dismiss.removeEventListener("click", state.dismissClick);
    if (state.observer) state.observer.disconnect();
    state.popup.remove();
    Object.keys(state.attributes).forEach(function (key) { var value = state.attributes[key]; if (value == null) state.anchor.removeAttribute(key); else state.anchor.setAttribute(key, value); });
    if (restoreFocus !== false) focus(state.anchor);
    state.anchor = state.popup = null;
  }
  function position(state, preferAbove) {
    var viewport = document.documentElement || {}, width = global.innerWidth || viewport.clientWidth || 1, height = global.innerHeight || viewport.clientHeight || 1;
    var margin = Math.min(12, width / 4, height / 4), gap = 6;
    state.popup.style.position = "fixed";
    state.popup.style.maxWidth = Math.max(1, width - margin * 2) + "px";
    state.popup.style.maxHeight = Math.max(1, height - margin * 2) + "px";
    state.popup.style.left = margin + "px";
    state.popup.style.top = margin + "px";
    var anchor = state.anchor.getBoundingClientRect(), popup = state.popup.getBoundingClientRect();
    var popupWidth = Math.min(popup.width, width - margin * 2), popupHeight = Math.min(popup.height, height - margin * 2);
    var left = Math.max(margin, Math.min(anchor.left, width - margin - popupWidth));
    var above = anchor.top - gap - popupHeight, below = anchor.bottom + gap;
    var top = preferAbove ? above : below;
    if (preferAbove ? top < margin : top + popupHeight > height - margin) top = preferAbove ? below : above;
    top = Math.max(margin, Math.min(top, height - margin - popupHeight));
    state.popup.style.left = left + "px";
    state.popup.style.top = top + "px";
  }
  function open(anchor, options) {
    tooltipManagers.forEach(function (manager) { manager.hide(); });
    if (!connected(anchor) || typeof anchor.getBoundingClientRect !== "function") { if (current && !connected(current.anchor)) close(false); return null; }
    if (current && current.anchor === anchor) { close(); return null; }
    close(false); options = options || {};
    var title = typeof options.title === "string" && options.title.trim() ? options.title : "详情";
    var popup = element("div", "library-card-popover"), header = element("header", "library-card-popover__header"), heading = element("h3", "library-card-popover__title", title);
    popup.id = "library-card-popover-" + (++sequence); heading.id = popup.id + "-title";
    popup.setAttribute("role", "dialog"); popup.setAttribute("aria-modal", "false"); popup.setAttribute("aria-labelledby", heading.id);
    var dismiss = element("button", "library-card-popover__close", "关闭"); dismiss.type = "button"; dismiss.setAttribute("aria-label", "关闭" + title);
    header.appendChild(heading); header.appendChild(dismiss); popup.appendChild(header);
    var body = element("div", "library-card-popover__body");
    (Array.isArray(options.lines) ? options.lines : []).forEach(function (line) { if (typeof line === "string") body.appendChild(element("p", "library-card-popover__line", line)); });
    (Array.isArray(options.sections) ? options.sections : []).forEach(function (row) {
      if (!row || typeof row !== "object") return;
      var section = element("section", "library-card-popover__section");
      if (typeof row.title === "string" && row.title.trim()) section.appendChild(element("h4", "library-card-popover__section-title", row.title));
      var fields = element("dl", "library-card-popover__fields");
      (Array.isArray(row.fields) ? row.fields : []).forEach(function (field) {
        if (!field || typeof field.label !== "string" || typeof field.value !== "string") return;
        fields.appendChild(element("dt", "library-card-popover__field-label", field.label));
        fields.appendChild(element("dd", "library-card-popover__field-value", field.value));
      });
      if (fields.children.length) section.appendChild(fields);
      if (section.children.length) body.appendChild(section);
    });
    popup.appendChild(body);
    var state = { anchor: anchor, popup: popup, dismiss: dismiss, attributes: {}, observer: null };
    ["aria-expanded", "aria-controls", "aria-haspopup"].forEach(function (key) { state.attributes[key] = anchor.getAttribute(key); });
    anchor.setAttribute("aria-expanded", "true"); anchor.setAttribute("aria-controls", popup.id); anchor.setAttribute("aria-haspopup", "dialog");
    current = state; document.body.appendChild(popup);
    function active() { return current === state; }
    state.outside = function (event) { if (active() && !contains(popup, event.target) && !contains(anchor, event.target)) close(false); };
    state.keydown = function (event) { if (active() && event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } };
    state.scroll = function (event) { if (active() && !contains(popup, event.target)) close(false); };
    state.resize = function () { if (active()) close(false); };
    state.dismissClick = function () { if (active()) close(); };
    dismiss.addEventListener("click", state.dismissClick);
    document.addEventListener("pointerdown", state.outside, true);
    document.addEventListener("click", state.outside, true);
    document.addEventListener("keydown", state.keydown, true);
    document.addEventListener("scroll", state.scroll, true);
    global.addEventListener("resize", state.resize);
    if (typeof global.MutationObserver === "function") {
      state.observer = new global.MutationObserver(function () { if (active() && (!connected(anchor) || !connected(popup))) close(false); });
      state.observer.observe(document.body, { childList: true, subtree: true });
    }
    position(state); focus(dismiss);
    return popup;
  }
  function bindTooltip(root) {
    var state = null, observer = null, disposed = false;
    function hide() {
      if (!state) return;
      var previous = state; state = null;
      previous.popup.remove();
      if (previous.description == null) previous.anchor.removeAttribute("aria-describedby");
      else previous.anchor.setAttribute("aria-describedby", previous.description);
    }
    function findAnchor(node) {
      while (node && contains(root, node)) {
        if (typeof node.getAttribute === "function" && node.getAttribute("data-card-tooltip") != null) return node;
        if (node === root) break;
        node = node.parentElement || node.parentNode;
      }
      return null;
    }
    function show(anchor) {
      if (disposed || current || !connected(root) || !connected(anchor)) return;
      var text = anchor.getAttribute("data-card-tooltip");
      if (!text || !text.trim()) { hide(); return; }
      if (state && state.anchor === anchor && state.popup.textContent === text) return;
      tooltipManagers.forEach(function (manager) { manager.hide(); });
      var popup = element("div", "library-card-tooltip", text);
      popup.id = "library-card-tooltip-" + (++sequence); popup.setAttribute("role", "tooltip");
      state = { anchor: anchor, popup: popup, description: anchor.getAttribute("aria-describedby") };
      anchor.setAttribute("aria-describedby", (state.description ? state.description + " " : "") + popup.id);
      document.body.appendChild(popup); position(state, true);
    }
    function enter(event) {
      if (event.pointerType === "touch") return;
      var anchor = findAnchor(event.target);
      if (anchor) show(anchor);
    }
    function leave(event) {
      if (state && contains(state.anchor, event.target) && !contains(state.anchor, event.relatedTarget)) hide();
    }
    function escape(event) { if (event.key === "Escape") hide(); }
    function dispose() {
      if (disposed) return;
      disposed = true; hide();
      if (root && typeof root.removeEventListener === "function") {
        root.removeEventListener("pointerover", enter);
        root.removeEventListener("pointerout", leave);
        root.removeEventListener("focusin", enter);
        root.removeEventListener("focusout", leave);
      }
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("click", hide, true);
      document.removeEventListener("keydown", escape, true);
      document.removeEventListener("scroll", hide, true);
      global.removeEventListener("resize", hide);
      if (observer) observer.disconnect();
      tooltipManagers = tooltipManagers.filter(function (item) { return item !== manager; });
    }
    var manager = { hide: hide, dispose: dispose };
    if (!root || typeof root.addEventListener !== "function" || !connected(root)) { disposed = true; return manager; }
    tooltipManagers.push(manager);
    root.addEventListener("pointerover", enter);
    root.addEventListener("pointerout", leave);
    root.addEventListener("focusin", enter);
    root.addEventListener("focusout", leave);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("click", hide, true);
    document.addEventListener("keydown", escape, true);
    document.addEventListener("scroll", hide, true);
    global.addEventListener("resize", hide);
    if (typeof global.MutationObserver === "function") {
      observer = new global.MutationObserver(function () {
        if (!connected(root)) dispose();
        else if (state && (!connected(state.anchor) || !contains(root, state.anchor) || !connected(state.popup))) hide();
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    return manager;
  }
  global.NimdaLibraryCardPopover = { open: open, close: close, bindTooltip: bindTooltip, dispose: function () { close(false); tooltipManagers.slice().forEach(function (manager) { manager.dispose(); }); } };
})(window);
