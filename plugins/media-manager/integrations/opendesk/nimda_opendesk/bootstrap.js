(function () {
  "use strict";
  var params = new URLSearchParams(location.hash.slice(1));
  var token = params.get("nimda_session");
  var sessionKey = "opendesk.nimda.session";
  try {
    if (token) sessionStorage.setItem(sessionKey, token);
    else token = sessionStorage.getItem(sessionKey);
  } catch (_) {}
  if (params.has("nimda_session")) {
    params.delete("nimda_session");
    history.replaceState(null, "", location.pathname + location.search + (params.toString() ? "#" + params : ""));
  }
  var rawFetch = window.fetch.bind(window);
  function protectedUrl(value) {
    try {
      var u = new URL(String(value), location.href);
      return u.origin === location.origin && (/^\/api(?:\/|$)/.test(u.pathname) || /^\/__opendesk\/(?:status|preferences)$/.test(u.pathname)) ? u : null;
    } catch (_) { return null; }
  }
  window.fetch = function (input, init) {
    var url = input instanceof Request ? input.url : input;
    if (!token || !protectedUrl(url)) return rawFetch(input, init);
    var request = new Request(input, init);
    var headers = new Headers(request.headers);
    headers.set("X-Nimda-Session", token);
    return rawFetch(new Request(request, { headers: headers, referrerPolicy: "no-referrer" }));
  };
  function resource(value) {
    var u = protectedUrl(value);
    if (!token || !u || !(/^\/api\/catalog-library\/(?:people\/)?assets\/[^/]+$/.test(u.pathname) || /^\/api\/operations\/[^/]+\/log\/file$/.test(u.pathname))) return value;
    u.searchParams.set("__nimda_session", token);
    return u.href;
  }
  [[HTMLImageElement.prototype, "src"], [HTMLAnchorElement.prototype, "href"]].forEach(function (pair) {
    var descriptor = Object.getOwnPropertyDescriptor(pair[0], pair[1]);
    if (!descriptor || !descriptor.set) return;
    Object.defineProperty(pair[0], pair[1], Object.assign({}, descriptor, { set: function (value) { descriptor.set.call(this, resource(value)); } }));
  });
  var setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    if ((this instanceof HTMLImageElement && String(name).toLowerCase() === "src") || (this instanceof HTMLAnchorElement && String(name).toLowerCase() === "href")) value = resource(value);
    return setAttribute.call(this, name, value);
  };
  var preferenceKey = /^(?:nimda\.|jp-tv-browse-)/;
  var storageSet = Storage.prototype.setItem, storageRemove = Storage.prototype.removeItem;
  var memory = {}, saveChain = Promise.resolve();
  function notice(message) {
    var id = "nimda-opendesk-adapter-notice";
    var el = document.getElementById(id);
    if (!el) { el = document.createElement("div"); el.id = id; el.setAttribute("role", "alert"); el.style.cssText = "position:fixed;bottom:8px;right:8px;max-width:420px;z-index:2147483647;background:#7a251d;color:white;padding:10px;border-radius:6px;font:13px sans-serif"; document.body.appendChild(el); }
    el.textContent = message;
  }
  function save(key, value) {
    if (!preferenceKey.test(key)) return;
    memory[key] = value;
    saveChain = saveChain.catch(function () {}).then(function () {
      return window.fetch("/__opendesk/preferences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: key, value: value }) }).then(function (response) {
        if (!response.ok) throw Error("preference save failed");
      });
    }).catch(function () { notice("界面偏好已在当前窗口保留，但共享保存失败；重启前请检查模块连接。"); });
  }
  Storage.prototype.setItem = function (key, value) {
    storageSet.call(this, key, value);
    if (this === localStorage) save(String(key), String(value));
  };
  Storage.prototype.removeItem = function (key) {
    storageRemove.call(this, key);
    if (this === localStorage) save(String(key), null);
  };
  window.NimdaOpenDeskReady = token ? window.fetch("/__opendesk/preferences").then(function (r) {
    if (!r.ok) throw Error("preferences unavailable");
    return r.json();
  }).then(function (values) {
    Object.keys(values).forEach(function (key) {
      if (!preferenceKey.test(key)) return;
      memory[key] = values[key];
      if (values[key] === null) storageRemove.call(localStorage, key);
      else storageSet.call(localStorage, key, values[key]);
    });
  }).catch(function () { addEventListener("DOMContentLoaded", function () { notice("共享界面偏好暂不可用，当前窗口仍可使用。"); }, { once: true }); }) : Promise.resolve();
  window.NimdaOpenDeskAdapter = { resource: resource };
})();
