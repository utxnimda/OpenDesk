(function (global) {
  "use strict";

  var active = null, nextId = 0, PAGE_SIZE = 20;
  function text(value) { return String(value == null ? "" : value); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function cleanPath(value) {
    var raw = text(value), previous;
    do {
      previous = raw;
      raw = raw.trim().replace(/^[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]+|[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]+$/g, "").trim();
      if (raw.length > 1 && raw[0] === '"' && raw[raw.length - 1] === '"') raw = raw.slice(1, -1);
    } while (previous !== raw);
    return raw.replace(/\\/g, "/").replace(/\/+$/, "");
  }
  function absolute(value) { return /^[a-z]:\//i.test(value) || /^\/\/[^/]+\/[^/]+(?:\/|$)/.test(value); }
  function safeParts(value) {
    var parts = value.replace(/^[a-z]:\//i, "").replace(/^\/\//, "").split("/");
    return parts.length && parts.every(function (part) { return part && part !== "." && part !== ".." && !/[<>:"|?*\x00-\x1f]/.test(part) && !/[. ]$/.test(part); });
  }
  function relativePath(value, workRoot) {
    var root = cleanPath(workRoot), path = cleanPath(value);
    if (!absolute(root) || !safeParts(root)) throw new Error("无法安全定位作品根目录，请先在编辑作品中填写绝对作品根目录。");
    if (!path || !safeParts(path)) throw new Error("目录路径无效或包含上级跳转：" + path);
    if (absolute(path)) {
      if (path.toLowerCase().indexOf(root.toLowerCase() + "/") !== 0) throw new Error("资源目录必须位于当前作品根目录下：" + path);
      path = path.slice(root.length + 1);
    } else if (/^(?:\/|[a-z]:)/i.test(path)) throw new Error("资源目录必须是作品根目录内的相对路径：" + path);
    return path;
  }
  function key(value) { return cleanPath(value).toLowerCase(); }
  function resourceIdentity(row) {
    return JSON.stringify([key(row.press_path), text(row.press_format).trim(), text(row.press_group).trim(), row.segment || "main", row.continuation_index == null ? null : row.continuation_index]);
  }
  function open(options) {
    options = options || {};
    if (active) { active.dialog.focus(); return active; }
    if (!global.NimdaWorkRecordForm || !global.NimdaEnumFields) throw new Error("资源编辑组件未加载，请重新打开应用。");
    if (typeof options.onSubmit !== "function") throw new Error("资源保存接口不可用。");
    var original = clone(options.record), projection = global.NimdaWorkRecordForm.fromRecord(original);
    var rootPath = cleanPath(options.workRoot || projection.path), rootReady = absolute(rootPath) && safeParts(rootPath);
    var existing = projection.collectioned_ordered.slice(), pending = [], closed = false, busy = false, loading = false, sequence = 0;
    var browseNode = null, browsePage = 0, opener = document.activeElement, prefix = "library-batch-resources-" + (++nextId) + "-";
    var attrs = original.attributes || [], collection = attrs.find(function (attr) { return attr.type === "collection-type"; });
    (collection && collection.data.continuations || []).forEach(function (part, index) { (part.collectioned || []).forEach(function (row) { existing.push(Object.assign({}, row, { segment: "continuation", continuation_index: index })); }); });
    var existingPaths = new Set(existing.map(function (row) { return key(row.press_path); }).filter(Boolean));
    var existingIdentities = new Set(existing.map(resourceIdentity));
    function el(tag, className, value) { var node = document.createElement(tag); if (className) node.className = className; if (value != null) node.textContent = text(value); return node; }
    function append(parent) { for (var i = 1; i < arguments.length; i++) parent.appendChild(arguments[i]); return parent; }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function button(label, action, className) {
      var node = el("button", "library-button " + (className || ""), label); node.type = "button";
      node.addEventListener("click", function () { if (!closed && !busy && !node.disabled) Promise.resolve().then(action).catch(fail); }); return node;
    }
    function fail(error) { if (closed) return; message.textContent = error && error.message || text(error); message.hidden = false; if (options.setStatus) options.setStatus(message.textContent, true); }
    function field(parent, label, name, enumKey) {
      var wrapper = el("label", "library-field"), caption = el("span", "", label), input = el("input");
      input.name = name; input.setAttribute("aria-label", label); input.type = "text";
      if (enumKey) input.setAttribute("list", prefix + enumKey);
      append(wrapper, caption, input); parent.appendChild(wrapper); return input;
    }
    var dialog = el("dialog", "library-batch-resource-dialog"); dialog.setAttribute("aria-labelledby", prefix + "title"); dialog.setAttribute("aria-modal", "true");
    var header = el("header", "library-batch-resource-header"), heading = el("h2", "", "批量关联资源目录"); heading.id = prefix + "title";
    append(header, heading, button("关闭", requestClose)); dialog.appendChild(header);
    var content = el("div", "library-batch-resource-body"), location = el("p", "library-path", rootPath || "未设置作品根目录"); content.appendChild(location);
    var message = el("p", "library-batch-resource-error"); message.setAttribute("role", "alert"); message.hidden = true; content.appendChild(message);
    if (!rootReady) fail(new Error("无法安全定位作品根目录，请先在编辑作品中填写绝对作品根目录。"));
    ["press_format", "press_group"].forEach(function (enumKey) {
      var list = el("datalist"); list.id = prefix + enumKey;
      global.NimdaEnumFields.choices(options.enumOptions || {}, options.enumLabels || {}, enumKey).forEach(function (entry) { var choice = el("option"); choice.value = entry.value; choice.label = entry.label; list.appendChild(choice); }); content.appendChild(list);
    });
    var defaults = el("div", "library-batch-resource-defaults");
    var batchFormat = field(defaults, "统一压制格式", "batch-format", "press_format"), batchGroup = field(defaults, "统一压制 / 字幕组", "batch-group", "press_group");
    defaults.appendChild(button("应用到待关联项", function () { pending.forEach(function (row) { row.press_format = batchFormat.value.trim(); row.press_group = batchGroup.value.trim(); }); renderPending(); })); content.appendChild(defaults);
    var inputLabel = el("label", "library-field"), paths = el("textarea"); paths.name = "resource-paths"; paths.rows = 4; paths.setAttribute("aria-label", "资源目录，每行一个"); paths.placeholder = "每行一个资源目录，可粘贴绝对路径或作品根目录内相对路径";
    append(inputLabel, el("span", "", "资源目录，每行一个"), paths); content.appendChild(inputLabel);
    var tools = el("div", "library-batch-resource-actions"), paste = button("加入待关联列表", function () { addPaths(paths.value.split(/\r?\n/).filter(function (value) { return value.trim(); })); paths.value = ""; });
    var browse = button("从资源库多选目录", function () { browser.hidden = false; return loadNode(""); }); append(tools, paste, browse); content.appendChild(tools);
    var browser = el("section", "library-batch-resource-browser"); browser.hidden = true; content.appendChild(browser);
    var saved = el("details", "library-batch-resource-existing"), summary = el("summary", "", "已关联资源（" + existing.length + "）"), savedList = el("ul");
    existing.forEach(function (row) { savedList.appendChild(el("li", "library-path", [row.press_format, row.press_group, row.press_path || "未绑定目录"].filter(Boolean).join(" · "))); }); append(saved, summary, savedList); content.appendChild(saved);
    var pendingArea = el("section", "library-batch-resource-pending"); content.appendChild(pendingArea);
    var discard = el("div", "library-batch-resource-discard"); discard.hidden = true; append(discard, el("span", "", "尚未保存，确定放弃？"), button("继续编辑", function () { discard.hidden = true; }), button("放弃修改", finishClose)); content.appendChild(discard);
    var footer = el("footer", "library-batch-resource-footer"), count = el("span"), submit = button("一次保存全部资源", save, "is-primary"); append(footer, count, button("取消", requestClose), submit); append(dialog, content, footer);
    function addPaths(values) {
      if (!rootReady) throw new Error("请先编辑作品根目录。");
      var candidates = values.map(function (value) { return relativePath(value, rootPath); }), seen = new Set(pending.map(resourceIdentity)), skipped = 0, additions = [];
      candidates.forEach(function (path) {
        var row = { press_format: batchFormat.value.trim(), press_group: batchGroup.value.trim(), press_path: path, segment: "main" }, identity = resourceIdentity(row);
        if (seen.has(identity)) { skipped++; return; }
        seen.add(identity); additions.push(row);
      });
      if (pending.length + additions.length + projection.collectioned_ordered.length > 200) throw new Error("单个作品最多支持 200 项主收集资源。");
      pending = pending.concat(additions);
      message.hidden = !skipped; message.textContent = skipped ? "已跳过 " + skipped + " 个目录、格式与组均相同的重复草稿。" : "";
      renderPending(); renderBrowser();
    }
    function renderPending() {
      clear(pendingArea); pendingArea.appendChild(el("h3", "", "待关联资源（" + pending.length + "）"));
      pending.forEach(function (row, index) {
        var line = el("div", "library-batch-resource-row"); line.appendChild(el("span", "library-path", row.press_path));
        var format = field(line, "压制格式 · " + (index + 1), "resource-format-" + index, "press_format"), group = field(line, "压制 / 字幕组 · " + (index + 1), "resource-group-" + index, "press_group");
        format.value = row.press_format; group.value = row.press_group;
        format.addEventListener("input", function () { row.press_format = format.value.trim(); }); group.addEventListener("input", function () { row.press_group = group.value.trim(); });
        line.appendChild(button("移除", function () { pending.splice(pending.indexOf(row), 1); renderPending(); renderBrowser(); })); pendingArea.appendChild(line);
      });
      count.textContent = "新增 " + pending.length + " 项"; submit.disabled = busy || !rootReady || !pending.length;
    }
    function renderBrowser() {
      if (browser.hidden) return;
      clear(browser);
      var toolbar = el("div", "library-batch-resource-actions"), relpath = browseNode && text(browseNode.relpath);
      var up = button("上级目录", function () { return loadNode(relpath.indexOf("/") >= 0 ? relpath.slice(0, relpath.lastIndexOf("/")) : ""); }); up.disabled = loading || !relpath;
      append(toolbar, up, el("span", "library-path", loading ? "正在读取资源目录…" : browseNode && (browseNode.path || browseNode.name) || "资源库")); browser.appendChild(toolbar);
      if (loading || !browseNode) return;
      var folders = (browseNode.children || []).filter(function (node) { return node && (node.type === "folder" || node.kind === "directory"); });
      var totalPages = Math.max(1, Math.ceil(folders.length / PAGE_SIZE)); browsePage = Math.min(browsePage, totalPages - 1);
      folders.slice(browsePage * PAGE_SIZE, (browsePage + 1) * PAGE_SIZE).forEach(function (node) {
        var line = el("div", "library-batch-resource-directory"), select = el("input"), relative = "", reason = "";
        try { relative = relativePath(node.path, rootPath); } catch (error) { reason = error.message; }
        var already = relative && existingPaths.has(key(relative)); select.type = "checkbox"; select.setAttribute("aria-label", "选择目录 " + node.name);
        select.checked = pending.some(function (row) { return key(row.press_path) === key(relative); }); select.disabled = busy || !relative || node.exists === false;
        select.title = node.exists === false ? "目录不存在" : reason || (already ? "已有资源，可追加其他格式或组" : "");
        select.addEventListener("change", function () { if (busy || closed || select.disabled) return; if (select.checked) { try { addPaths([node.path]); } catch (error) { fail(error); select.checked = false; } } else { pending = pending.filter(function (row) { return key(row.press_path) !== key(relative); }); renderPending(); } });
        var enter = button(node.name || node.path, function () { return loadNode(node.relpath); }); enter.disabled = node.exists === false; enter.title = node.path;
        append(line, select, enter); if (already) line.appendChild(el("span", "library-meta", "已有关联，可追加其他格式 / 组")); browser.appendChild(line);
      });
      if (!folders.length) browser.appendChild(el("p", "library-meta", "此目录没有子目录"));
      var pager = el("div", "library-batch-resource-actions"), prev = button("上一页", function () { browsePage--; renderBrowser(); }), next = button("下一页", function () { browsePage++; renderBrowser(); }); prev.disabled = browsePage === 0; next.disabled = browsePage + 1 >= totalPages; append(pager, prev, el("span", "", (browsePage + 1) + " / " + totalPages), next); browser.appendChild(pager);
    }
    async function loadNode(relpath) {
      var epoch = ++sequence; loading = true; renderBrowser();
      try {
        var request = options.request || global.NimdaCommon && global.NimdaCommon.fetchJson;
        if (!request) throw new Error("资源库目录读取接口不可用。");
        var response = await request("/api/collection-detail/resource-libraries/node?relpath=" + encodeURIComponent(text(relpath)), { method: "GET" }), data = response && response.data !== undefined ? response.data : response;
        if (closed || epoch !== sequence) return;
        if (!data || data.ok === false || response.res && !response.res.ok || !data.node) throw new Error(data && data.error || "资源库目录读取失败。");
        browseNode = data.node; browsePage = 0;
      } catch (error) { if (!closed && epoch === sequence) fail(error); }
      finally { if (!closed && epoch === sequence) { loading = false; renderBrowser(); } }
    }
    async function save() {
      if (busy || !pending.length || !rootReady) return;
      if (loading) throw new Error("请等待目录读取完成后保存。");
      var identities = new Set(existingIdentities);
      pending.forEach(function (row) {
        relativePath(row.press_path, rootPath); if (!row.press_format) throw new Error("请填写每项资源的压制格式：" + row.press_path);
        var identity = resourceIdentity(row);
        if (identities.has(identity)) throw new Error("资源重复（目录、格式、组与收集段相同），请修改或移除重复项：" + [row.press_path, row.press_format, row.press_group || "无组"].join(" · "));
        identities.add(identity);
      });
      var patch = { collectioned_ordered: projection.collectioned_ordered.concat(clone(pending)) }, next = global.NimdaWorkRecordForm.applyPatch(original, patch);
      var controls = Array.prototype.map.call(dialog.querySelectorAll("input, textarea, button, select"), function (node) { return { node: node, disabled: node.disabled }; });
      busy = true; controls.forEach(function (entry) { entry.node.disabled = true; }); message.hidden = true;
      try {
        var receipt = await options.onSubmit(next, patch);
        if (!receipt || receipt.ok === false || receipt.db_committed !== true) throw new Error("未收到数据库保存成功的确认，草稿已保留；请先核对数据库结果，不要直接重复提交。");
        finishClose();
      }
      catch (error) { fail(error); }
      finally { if (!closed) { busy = false; controls.forEach(function (entry) { if (entry.node.isConnected) entry.node.disabled = entry.disabled; }); renderPending(); renderBrowser(); } }
    }
    function requestClose() { if (busy) return; if (pending.length || paths.value.trim()) discard.hidden = false; else finishClose(); }
    function finishClose() { if (closed) return; closed = true; sequence++; dialog.close(); dialog.remove(); active = null; if (opener && opener.isConnected !== false && opener.focus) opener.focus(); if (options.onClose) options.onClose(); }
    dialog.addEventListener("cancel", function (event) { event.preventDefault(); requestClose(); });
    dialog.addEventListener("close", function () { if (!closed && !busy) finishClose(); });
    paste.disabled = !rootReady; browse.disabled = !rootReady; renderPending(); document.body.appendChild(dialog);
    active = { dialog: dialog, close: finishClose }; dialog.showModal(); paths.focus(); return active;
  }
  global.NimdaBatchResourceDialog = { open: open, relativePath: relativePath, dispose: function () { if (active) active.close(); } };
})(window);
