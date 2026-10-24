(function (global) {
  "use strict";
  function create(options) {
    if (!options.payload || !/^[1-9][0-9]*$/.test(String(options.payload.subject_id || ""))) throw new Error("请选择明确的豆瓣作品条目。");
    options.payload = { ref: options.payload.ref, subject_id: String(options.payload.subject_id), binding_role: "supplement", ...(options.payload.replace_binding_id ? { replace_binding_id: options.payload.replace_binding_id } : {}) };
    var state = null, retired = false, pending = false, sessionId = "", completed = false;
    var timer = null, deadlineTimer = null, cycle = false, ticket = 0, polls = 0, release = null, stopped = null;
    var POLL_DELAY = 750, READ_TIMEOUT = 30000, MAX_POLLS = 40;
    function current() { return !retired && (!options.current || options.current()); }
    function notify() { if (current() && options.onChange) options.onChange(state); }
    function endCycle() {
      if (timer !== null) global.clearTimeout(timer);
      if (deadlineTimer !== null) global.clearTimeout(deadlineTimer);
      timer = deadlineTimer = null; cycle = false; pending = false; ticket += 1;
      if (release) release(null);
      release = null;
    }
    function cancelRemote(id) {
      if (!id) return Promise.resolve();
      return Promise.resolve().then(function () { return options.request("douban/browser/cancel", { ref: options.payload.ref, session_id: id }); }).catch(function () {});
    }
    function cancel() {
      if (retired) return Promise.resolve();
      retired = true; endCycle(); return cancelRemote(sessionId);
    }
    function fail(message) {
      endCycle();
      state = Object.assign({}, state, { status: "failed", busy: false, polling: false, auto_continue: false, message: message }); notify();
    }
    function timeout() {
      if (!current()) { cancel(); return; }
      fail(sessionId ? "评分读取等待已超过 30 秒，已暂停；请检查评分窗口，页面就绪后继续读取，或重新打开所选条目。" :
        "评分窗口打开等待已超过 30 秒，已暂停；请取消后重新发起读取。");
    }
    function accept(value) {
      if (sessionId && value.session_id && value.session_id !== sessionId) throw new Error("评分读取会话已变化，请取消后重新打开所选条目。");
      sessionId = value.session_id || sessionId;
      state = Object.assign({}, value, { busy: false, polling: value.status === "loading" && value.auto_continue === true });
      if (state.polling && !sessionId) throw new Error("评分读取会话缺失，请取消后重新打开所选条目。");
      if (state.status === "complete") {
        if (!state.preview || state.preview.provider !== "douban" || (Array.isArray(state.preview.diff) && state.preview.diff.some(function (row) { return row.field !== "metadata.ratings.douban"; })) || state.preview.people_preview || (Array.isArray(state.preview.automatic_aliases) && state.preview.automatic_aliases.length)) throw new Error("评分读取返回了非评分资料，已停止；请重新读取。");
        if (!completed) { completed = true; if (options.onComplete) options.onComplete(state.preview); }
      }
      if (!state.polling) endCycle();
      notify(); return state;
    }
    async function read(route, body, serial) {
      if (!current() || pending || serial !== ticket) return null;
      pending = true;
      state = Object.assign({}, state || { status: "loading", message: "正在打开豆瓣评分页面…" }, { busy: true, polling: true }); notify();
      try {
        // Keep the late response observable so a cancelled/timed-out start can
        // close the session it created. No retry opens another page implicitly.
        var response = Promise.resolve(options.request(route, body)).then(function (value) {
          if (!current() || serial !== ticket) {
            if (!current() || route === "douban/browser/start") cancelRemote(value && value.session_id);
            return null;
          }
          return value;
        });
        var value = await Promise.race([response, stopped]);
        if (!current()) { cancel(); return null; }
        if (serial !== ticket || !value) return null;
        accept(value);
        if (state.polling && current() && serial === ticket) {
          timer = global.setTimeout(function () {
            timer = null;
            if (!current()) { cancel(); return; }
            if (!cycle || serial !== ticket) return;
            if (++polls > MAX_POLLS) { timeout(); return; }
            read("douban/browser/step", { ref: options.payload.ref, session_id: sessionId, action: "continue" }, serial);
          }, POLL_DELAY);
        }
        return state;
      }
      catch (error) {
        if (current() && serial === ticket) fail(error && error.message || "浏览器读取失败，请检查浏览器后重试。");
        return null;
      } finally { if (serial === ticket) pending = false; }
    }
    function begin(route, body) {
      endCycle(); cycle = true; polls = 0;
      stopped = new Promise(function (resolve) { release = resolve; });
      deadlineTimer = global.setTimeout(timeout, READ_TIMEOUT);
      return read(route, body, ticket);
    }
    function step(action) {
      if (!current() || pending || cycle || !sessionId || !state || state.status === "complete") return Promise.resolve(null);
      if (["continue", "reopen"].indexOf(action) < 0 || action === "reopen" && ["verification", "failed", "closed"].indexOf(state.status) < 0) return Promise.resolve(null);
      return begin("douban/browser/step", { ref: options.payload.ref, session_id: sessionId, action: action });
    }
    return { start: function () { if (state || retired) return Promise.resolve(null); return begin("douban/browser/start", options.payload); }, step: step, cancel: cancel, state: function () { return state; } };
  }
  function render(parent, state, handlers) {
    if (!state) return;
    function node(tag, cls, value) { var element = document.createElement(tag); element.className = cls; if (value != null) element.textContent = String(value); return element; }
    var panel = node("section", "library-rating-browser"), status = node("p", "library-meta", state.message || "正在读取浏览器页面…"), actions = node("div", "library-rating-browser-actions");
    panel.setAttribute("aria-label", "豆瓣评分读取"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    panel.appendChild(node("h3", "library-section-title", "豆瓣评分读取")); panel.appendChild(status);
    if (state.total != null) panel.appendChild(node("p", "library-meta", "已读取 " + (Number(state.completed) || 0) + " / " + (Number(state.total) || 0) + " 项"));
    if (state.status === "verification") panel.appendChild(node("p", "library-meta", "请在已打开的浏览器中正常登录或完成验证，再点击继续读取。"));
    if (state.status !== "complete") {
      panel.appendChild(node("p", "library-meta", "只读取作品评分与评价人数；确认预览后才保存，不读取人物资料。"));
      function action(label, fn, disabled) {
        var button = node("button", "library-button", label); button.type = "button"; button.disabled = !!disabled;
        button.addEventListener("click", function () { if (!button.disabled) Promise.resolve().then(fn).catch(function () {}); }); actions.appendChild(button);
      }
      if (state.session_id) action(state.busy || state.polling ? "正在读取…" : "继续读取", function () { return handlers.step("continue"); }, state.busy || state.polling);
      if (state.session_id && ["verification", "failed", "closed"].indexOf(state.status) >= 0) action("重新打开所选条目", function () { return handlers.step("reopen"); }, state.busy);
      action("取消采集", handlers.cancel, false); panel.appendChild(actions);
    }
    parent.appendChild(panel); return panel;
  }
  global.NimdaRatingBrowser = { create: create, render: render };
})(window);
