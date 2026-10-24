(function (global) {
  "use strict";
  var active = null;
  function node(tag, className, value) {
    var element = document.createElement(tag); if (className) element.className = className;
    if (value != null) element.textContent = String(value); return element;
  }
  function close() {
    if (!active) return; var old = active; active = null;
    if (old.dialog.open) old.dialog.close(); old.dialog.remove();
    if (old.focus && old.focus.isConnected) old.focus.focus();
  }
  function priorityField(parent, label, value) {
    var field = node("label", "library-display-setting"), select = node("select");
    field.appendChild(node("span", "", label));
    [["original", "原语言优先"], ["chinese", "中文优先"]].forEach(function (entry) {
      var option = node("option", "", entry[1]); option.value = entry[0]; select.appendChild(option);
    });
    select.value = value; select.setAttribute("aria-label", label + "标题、人物与角色名语言");
    field.appendChild(select); parent.appendChild(field); return select;
  }
  async function open(options) {
    close();
    var dialog = node("dialog", "library-display-dialog"), form = node("form"), status = node("p", "library-meta", "正在读取显示设置…");
    var title = node("h2", "", "章节标题、人物与角色名"), rows = node("div", "library-display-settings"), controls = node("div", "library-display-actions");
    title.id = "library-display-settings-title"; dialog.setAttribute("aria-labelledby", title.id);
    var cancel = node("button", "library-button", "取消"), save = node("button", "library-button is-primary", "保存");
    cancel.type = "button"; save.type = "submit"; save.disabled = true;
    controls.appendChild(cancel); controls.appendChild(save);
    form.appendChild(title); form.appendChild(status); form.appendChild(rows); form.appendChild(controls); dialog.appendChild(form);
    var session = { dialog: dialog, focus: document.activeElement, busy: false }, snapshot, defaultControl, entries = [];
    active = session; document.body.appendChild(dialog); dialog.showModal();
    cancel.addEventListener("click", function () { if (!session.busy) close(); });
    dialog.addEventListener("cancel", function (event) { if (session.busy) event.preventDefault(); else close(); });
    dialog.addEventListener("close", function () { if (active === session) close(); });
    form.addEventListener("submit", async function (event) {
      event.preventDefault(); if (active !== session || !snapshot || session.busy) return;
      session.busy = true; save.disabled = cancel.disabled = true; status.hidden = false; status.textContent = "正在保存…";
      var selectors = [defaultControl].concat(entries.map(function (entry) { return entry.select; }));
      selectors.forEach(function (select) { select.disabled = true; });
      try {
        var result = await options.save({ revision: snapshot.revision, config: { schema_version: 1, default_priority: defaultControl.value,
          chapter_titles: entries.map(function (entry) { return { domain: entry.row.domain, country: entry.row.country, priority: entry.select.value }; }) } });
        if (!result || result.ok !== true) throw new Error(result && (result.error || result.message) || "保存结果未确认，请重新打开设置核对；不要重复提交。");
        if (active === session) close();
      } catch (error) {
        if (active === session) { status.textContent = error.message || String(error); status.setAttribute("role", "alert"); if (options.setStatus) options.setStatus(status.textContent, true); }
      } finally {
        session.busy = false;
        if (active === session) { save.disabled = cancel.disabled = false; selectors.forEach(function (select) { select.disabled = false; }); }
      }
    });
    try {
      snapshot = await options.load(); if (active !== session) return;
      if (!snapshot || !snapshot.config || !Array.isArray(snapshot.collection_types)) throw new Error("显示设置响应不完整，请重新打开。");
      status.textContent = "标题、人物姓名与饰演角色共用语言优先级；悬停查看另一种语言，缺失时自动回退。";
      defaultControl = priorityField(rows, "其他收集类型", snapshot.config.default_priority);
      entries = snapshot.collection_types.map(function (row) { return { row: row, select: priorityField(rows, row.label, row.priority) }; });
      save.disabled = false; defaultControl.focus();
    } catch (error) {
      if (active === session) { status.textContent = error.message || String(error); status.setAttribute("role", "alert"); if (options.setStatus) options.setStatus(status.textContent, true); }
    }
  }
  global.NimdaLibraryDisplaySettings = { open: open, close: close };
})(window);
