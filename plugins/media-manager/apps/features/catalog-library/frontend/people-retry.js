(function (global) {
  "use strict";
  var array = function (value) { return Array.isArray(value) ? value : []; };
  var text = function (value) { return String(value == null ? "" : value); };
  var copy = function (value) { return JSON.parse(JSON.stringify(value)); };
  function stable(value) {
    if (Array.isArray(value)) return value.map(stable);
    if (value && typeof value === "object") { var result = {}; Object.keys(value).sort().forEach(function (key) { result[key] = stable(value[key]); }); return result; }
    return value;
  }
  function equal(left, right) { return JSON.stringify(stable(left)) === JSON.stringify(stable(right)); }
  function previews(root) {
    var result = [];
    function visit(value) { if (!value || typeof value !== "object") return; if (value.mode === "people-import") result.push(value); if (value.people_preview) visit(value.people_preview); array(value.sources).forEach(visit); }
    visit(root); return result;
  }
  function failures(preview) { return array(preview && preview.retry_failures).filter(function (row) { return row && row.retryable === true && text(row.id); }); }
  function hasFailures(root) { return previews(root).some(function (preview) { return failures(preview).length > 0; }); }
  function target(entry, action, identity) {
    if (action === "create") return entry.creation && entry.creation.allowed === true && !entry.creation.blocked ? entry.creation : null;
    return action === "reuse" ? array(entry.targets).find(function (row) { return text(row.id) === text(identity) && !row.blocked; }) : null;
  }
  function sameField(left, right) {
    return left && right && equal(left.before, right.before) && equal(left.after, right.after) &&
      (left.selectable !== false) === (right.selectable !== false) && !!left.must_override === !!right.must_override && !!left.required === !!right.required;
  }
  function relationValue(row) { var value = Object.assign({}, row); delete value.selected; return value; }
  function migrateEntry(previous, next) {
    if (previous.entity_type !== next.entity_type || text(previous.identity_owner_id) !== text(next.identity_owner_id)) {
      next.action = "skip"; next.target_id = ""; next.selected_fields = []; next.selected_relations = []; next.overrides = {};
      next._targetError = "重试后的来源身份或资料卡类型已变化，请重新选择并核对。"; return;
    }
    if (previous.action === "skip") { next.action = "skip"; next.target_id = ""; next.selected_fields = []; next.selected_relations = []; next.overrides = {}; next._userSkipped = previous._userSkipped; return; }
    var oldTarget = target(previous, previous.action, previous.target_id), nextTarget = target(next, previous.action, previous.target_id);
    if (!oldTarget || !nextTarget || text(oldTarget.id) !== text(nextTarget.id)) {
      next.action = "skip"; next.target_id = ""; next.selected_fields = []; next.selected_relations = []; next.overrides = {};
      next._targetError = "重试后的资料卡对应关系已变化，请重新选择并核对。"; return;
    }
    next.action = previous.action; next.target_id = previous.action === "reuse" ? previous.target_id : "";
    var selected = new Set(array(nextTarget.selected_fields)), overrides = {}, errors = {};
    array(nextTarget.diff).forEach(function (field) {
      var old = array(oldTarget.diff).find(function (row) { return row.field === field.field; }), key = field.field;
      if (field.required && field.selectable !== false) selected.add(key);
      if (field.selectable === false) { selected.delete(key); return; }
      var manual = old && field.editable !== false && key !== "avatar" && Object.prototype.hasOwnProperty.call(previous.overrides || {}, key) && equal(old.before, field.before);
      if (manual) { overrides[key] = copy(previous.overrides[key]); if (array(previous.selected_fields).indexOf(key) >= 0) selected.add(key); else selected.delete(key); }
      else if (sameField(old, field)) { if (array(previous.selected_fields).indexOf(key) >= 0) selected.add(key); else if (!field.required) selected.delete(key); }
      if (sameField(old, field) && previous._fieldErrors && previous._fieldErrors[key]) errors[key] = previous._fieldErrors[key];
    });
    next.selected_fields = Array.from(selected); next.overrides = overrides; next._fieldErrors = errors;
    var relations = new Set(array(next.selected_relations));
    array(next.relations).forEach(function (row) {
      var old = array(previous.relations).find(function (item) { return text(item.id) === text(row.id); });
      if (!old || !equal(relationValue(old), relationValue(row))) { relations.delete(text(row.id)); return; }
      if (row.selectable !== false && array(previous.selected_relations).indexOf(text(row.id)) >= 0) relations.add(text(row.id)); else relations.delete(text(row.id));
    });
    next.selected_relations = Array.from(relations); next._detailsOpen = previous._detailsOpen; next._autoFieldsOpen = previous._autoFieldsOpen;
  }
  function applyPatch(root, response) {
    if (!response || !response.preview_patch || !response.preview_patch.preview_token) throw new Error("重试未返回有效的确认预览，原预览已保留。");
    var old = previews(root), next = Object.assign({}, root, response.preview_patch), replacements = response.snapshot_replacements || {};
    previews(next).forEach(function (preview) {
      var previous = old.find(function (row) { return text(replacements[row.snapshot_id] || row.snapshot_id) === text(preview.snapshot_id) && row.provider === preview.provider; });
      if (!previous || previous === preview) return;
      preview._autoGroupOpen = previous._autoGroupOpen;
      array(preview.entries).forEach(function (entry) { var before = array(previous.entries).find(function (row) { return text(row.external_id) === text(entry.external_id); }); if (before) migrateEntry(before, entry); });
    });
    var warnings = response.warning_replacements;
    if (warnings) {
      next.warnings = array(next.warnings).filter(function (warning) { return !array(warnings.removed).some(function (removed) { return equal(removed, warning); }); });
      array(warnings.added).forEach(function (warning) { if (!next.warnings.some(function (oldWarning) { return equal(oldWarning, warning); })) next.warnings.push(warning); });
    }
    Object.assign(root, next); return root;
  }
  function render(parent, preview, options) {
    options = options || {}; var rows = failures(preview); if (!rows.length) return null;
    function node(tag, cls, value) { var element = document.createElement(tag); if (cls) element.className = cls; if (value != null) element.textContent = text(value); return element; }
    function button(label, action, disabled) { var element = node("button", "library-button", label); element.type = "button"; element.disabled = !!disabled; element.addEventListener("click", function () { if (!element.disabled && typeof action === "function") return Promise.resolve(action()).catch(function () { /* Controller reports the operation. */ }); }); return element; }
    var panel = node("section", "library-people-retry"), heading = node("div", "library-people-retry-heading"), active = text(options.retryingSnapshotId) === text(preview.snapshot_id);
    panel.setAttribute("aria-label", "人物资料失败项重试");
    heading.appendChild(node("h4", "library-section-title", "未完成读取（" + rows.length + "）"));
    heading.appendChild(button(active ? "正在重试失败项…" : "重试全部失败项", function () { return options.onRetry(preview); }, options.busy || !options.onRetry));
    if (active && options.onCancelRetry) heading.appendChild(button("取消重试", options.onCancelRetry));
    panel.appendChild(heading);
    rows.forEach(function (failure) {
      var row = node("div", "library-people-retry-row"), info = node("div"), identity = text(failure.label) || "人物 " + text(failure.object_id);
      if (failure.object_id && identity.indexOf(text(failure.object_id)) < 0) identity += " · ID " + failure.object_id;
      info.appendChild(node("strong", "", identity));
      var context = [failure.field === "avatar" ? "人物图片" : failure.field, failure.language, failure.path].filter(Boolean).join(" · "); if (context) info.appendChild(node("p", "library-meta", context));
      info.appendChild(node("p", "library-person-selection-error", failure.reason || failure.warning || "本项资料未完整读取"));
      row.appendChild(info); row.appendChild(button("重试此项", function () { return options.onRetry(preview, [failure.id]); }, options.busy || !options.onRetry)); panel.appendChild(row);
    });
    if (preview._retryError) { var error = node("p", "library-person-selection-error", preview._retryError); error.setAttribute("role", "status"); panel.appendChild(error); }
    parent.appendChild(panel); return panel;
  }
  global.NimdaPeopleRetry = { previews: previews, failures: failures, hasFailures: hasFailures, applyPatch: applyPatch, render: render };
})(window);
