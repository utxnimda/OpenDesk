(function (global) {
  "use strict";
  var nextId = 0, PAGE_SIZE = 8;
  function text(value) { return String(value == null ? "" : value); }
  function array(value) { return Array.isArray(value) ? value : []; }
  function el(tag, className, value) { var node = document.createElement(tag); if (className) node.className = className; if (value != null) node.textContent = text(value); return node; }
  function append(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function naturalCompare(left, right) { return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }) || left.localeCompare(right); }

  function open(options) {
    var chapters = array(options.chapters), files = Array.from(new Set(array(options.files).map(function (file) { return text(file.relative_path); }).filter(Boolean))).sort(naturalCompare);
    var byKey = new Map(), chapterOrder = [], selectedChapters = [], selectedFiles = [], pages = { chapters: 0, files: 0, preview: 0 };
    chapters.forEach(function (row, index) { if (row.binding_allowed !== false && row.chapter_key && !byKey.has(row.chapter_key)) { byKey.set(row.chapter_key, { row: row, index: index }); chapterOrder.push(row.chapter_key); } });
    var positions = { chapters: new Map(chapterOrder.map(function (key, index) { return [key, index]; })), files: new Map(files.map(function (key, index) { return [key, index]; })) };
    var disposed = false, busy = false, preview = false, ordered = false, controls = [], pickerInputs = {}, orderControls = new Map();
    var dialog = el("dialog", "chapter-player-dialog chapter-batch-dialog"), form = el("form", "chapter-batch-form"), heading = el("h2", "", "批量设置章节文件");
    heading.id = "chapter-batch-heading-" + (++nextId); dialog.setAttribute("aria-labelledby", heading.id);
    var content = el("div", "chapter-batch-content"), footer = el("div", "chapter-player-dialog-actions");
    append(form, heading, content, footer); dialog.appendChild(form);
    function button(label, action, disabled, className) {
      var node = el("button", "library-button" + (className ? " " + className : ""), label); node.type = "button"; node.disabled = busy || !!disabled; controls.push(node);
      node.addEventListener("click", function () { if (!node.disabled && !disposed) action(); }); return node;
    }
    function title(row, index) {
      var value = global.NimdaChapterTitles ? global.NimdaChapterTitles.display(row, options.titlePreference) : { text: row.name || row.title || row.name_cn || "未命名章节", tooltip: row.name_cn || row.name || row.title || "" };
      return { text: global.NimdaChapterNumbers.label(row, index) + " · " + value.text, tooltip: value.tooltip };
    }
    function selected(kind) { return kind === "chapters" ? selectedChapters : selectedFiles; }
    function select(kind, key, checked) {
      var values = selected(kind), index = values.indexOf(key);
      if (checked && index < 0) values.push(key); else if (!checked && index >= 0) values.splice(index, 1);
      if (!ordered) values.sort(function (a, b) { return positions[kind].get(a) - positions[kind].get(b); });
    }
    function paginator(parent, key, total, render) {
      var count = Math.max(1, Math.ceil(total / PAGE_SIZE)), current = Math.min(pages[key], count - 1); pages[key] = current;
      var pager = el("div", "chapter-batch-pager");
      append(pager, button("上一页", function () { pages[key]--; render(); }, !current), el("span", "library-meta", (current + 1) + " / " + count), button("下一页", function () { pages[key]++; render(); }, current + 1 >= count)); parent.appendChild(pager);
      return [current * PAGE_SIZE, (current + 1) * PAGE_SIZE];
    }
    function picker(kind, label, values) {
      var panel = el("section", "chapter-batch-picker"), bar = el("div", "chapter-batch-picker-heading"), picked = selected(kind);
      append(bar, el("h3", "", label + " · 已选 " + picked.length), button("全选", function () { var existing = new Set(picked); values.forEach(function (key) { if (!existing.has(key)) picked.push(key); }); if (!ordered) picked.sort(function (a, b) { return positions[kind].get(a) - positions[kind].get(b); }); render(); }, !values.length), button("清空", function () { picked.splice(0); render(); }, !picked.length)); panel.appendChild(bar);
      var list = el("div", "chapter-batch-options"), range = paginator(panel, kind, values.length, render);
      values.slice(range[0], range[1]).forEach(function (key) {
        var item = el("label", "chapter-batch-option"), checkbox = el("input"), value;
        checkbox.type = "checkbox"; checkbox.checked = picked.indexOf(key) >= 0; checkbox.disabled = busy; controls.push(checkbox);
        if (kind === "chapters") { var chapter = byKey.get(key); value = title(chapter.row, chapter.index); }
        else value = { text: key, tooltip: key };
        checkbox.setAttribute("aria-label", "选择" + (kind === "chapters" ? "章节 " : "文件 ") + value.text);
        pickerInputs[kind].set(key, checkbox);
        checkbox.addEventListener("change", function () { if (!busy && !disposed) { select(kind, key, checkbox.checked); render(); var replacement = pickerInputs[kind].get(key); if (replacement) replacement.focus(); } });
        var name = el("span", "chapter-batch-option-name", value.text); name.title = value.tooltip; append(item, checkbox, name); list.appendChild(item);
      });
      panel.appendChild(list); if (!values.length) list.appendChild(el("p", "library-meta", kind === "chapters" ? "没有可以绑定的章节" : "没有已识别的视频文件"));
      return panel;
    }
    function move(values, index, offset, kind) {
      var target = index + offset; if (target < 0 || target >= values.length) return;
      var value = values[index]; values[index] = values[target]; values[target] = value; ordered = true; pages.preview = Math.floor(target / PAGE_SIZE); render();
      var focus = orderControls.get(kind + ":" + target + ":" + offset);
      if (!focus || focus.disabled) focus = orderControls.get(kind + ":" + target + ":" + (-offset));
      if (focus && !focus.disabled) focus.focus();
    }
    function changes() { return selectedChapters.map(function (key, index) { return { chapter_key: key, relative_path: selectedFiles[index] }; }).filter(function (change) { var row = byKey.get(change.chapter_key).row; return row.status !== "confirmed" || row.relative_path !== change.relative_path; }); }
    function previewRows() {
      var count = selectedChapters.length, list = el("div", "chapter-batch-pairs"), range = paginator(content, "preview", count, render);
      content.appendChild(el("p", "library-meta", "按下列顺序对应；可分别调整章节与文件顺序。"));
      selectedChapters.slice(range[0], range[1]).forEach(function (key, offset) {
        var index = range[0] + offset, chapter = byKey.get(key), pair = el("div", "chapter-batch-pair"), label = title(chapter.row, chapter.index);
        function cell(value, values, kind, previous) {
          var node = el("div", "chapter-batch-pair-cell"), name = el("span", "chapter-batch-pair-name", value); name.title = value;
          var textGroup = el("span", "chapter-batch-pair-text"); textGroup.appendChild(name);
          if (previous) { var before = el("span", "chapter-batch-existing", previous); before.title = previous; textGroup.appendChild(before); }
          var arrows = el("span", "chapter-batch-order"), up = button("↑", function () { move(values, index, -1, kind); }, !index), down = button("↓", function () { move(values, index, 1, kind); }, index + 1 >= count);
          up.setAttribute("aria-label", "上移第 " + (index + 1) + " 项" + kind); down.setAttribute("aria-label", "下移第 " + (index + 1) + " 项" + kind);
          orderControls.set(kind + ":" + index + ":-1", up); orderControls.set(kind + ":" + index + ":1", down);
          append(arrows, up, down); append(node, textGroup, arrows); return node;
        }
        var previous = (chapter.row.status === "confirmed" || chapter.row.status === "missing") && chapter.row.relative_path && chapter.row.relative_path !== selectedFiles[index] ?
          (chapter.row.status === "missing" ? "原文件已缺失：" : "将替换已确认文件：") + chapter.row.relative_path : "";
        append(pair, el("span", "chapter-batch-pair-number", String(index + 1)), cell(label.text, selectedChapters, "章节", previous), el("span", "chapter-batch-pair-arrow", "→"), cell(selectedFiles[index], selectedFiles, "文件")); list.appendChild(pair);
      }); content.appendChild(list);
    }
    function render() {
      if (disposed) return; controls = []; pickerInputs = { chapters: new Map(), files: new Map() }; orderControls = new Map(); clear(content); clear(footer);
      if (preview) previewRows();
      else {
        var picks = el("div", "chapter-batch-pickers"); append(picks, picker("chapters", "章节", chapterOrder), picker("files", "视频文件", files)); content.appendChild(picks);
        var count = el("p", "chapter-batch-selection-summary", "已选 " + selectedChapters.length + " 个章节 / " + selectedFiles.length + " 个文件"); count.setAttribute("aria-live", "polite"); content.appendChild(count);
      }
      append(footer, button("取消", function () { options.onCancel(); }));
      if (preview) {
        append(footer, button("返回选择", function () { preview = false; render(); }), button("恢复默认顺序", function () { selectedChapters.sort(function (a, b) { return positions.chapters.get(a) - positions.chapters.get(b); }); selectedFiles.sort(naturalCompare); ordered = false; render(); }));
        var save = el("button", "library-button is-primary", "确认保存 " + changes().length + " 项"); save.type = "submit"; save.disabled = busy || !changes().length; controls.push(save); footer.appendChild(save);
      } else append(footer, button("预览对应关系", function () { preview = true; pages.preview = 0; render(); }, !selectedChapters.length || selectedChapters.length !== selectedFiles.length || selectedChapters.length > 5000, "is-primary"));
      form.setAttribute("aria-busy", busy ? "true" : "false");
    }
    function closeRequested(event) { if (event) event.preventDefault(); if (!busy && !disposed) options.onCancel(); }
    dialog.addEventListener("cancel", closeRequested); dialog.addEventListener("close", closeRequested);
    form.addEventListener("submit", async function (event) {
      event.preventDefault(); if (disposed || busy || !preview || !selectedChapters.length || selectedChapters.length !== selectedFiles.length) return;
      var assignments = changes(); if (!assignments.length || assignments.length > 5000) return;
      busy = true; controls.forEach(function (node) { node.disabled = true; }); form.setAttribute("aria-busy", "true");
      try { await options.onSave(assignments); }
      catch (error) { if (!disposed && options.onError) options.onError(error); }
      finally { if (!disposed) { busy = false; render(); } }
    });
    render(); document.body.appendChild(dialog); if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    var first = controls.find(function (node) { return !node.disabled; }); if (first) first.focus();
    return { node: dialog, dispose: function () { if (disposed) return; disposed = true; if (dialog.open && typeof dialog.close === "function") dialog.close(); dialog.remove(); } };
  }
  global.NimdaChapterBatchDialog = { open: open, naturalCompare: naturalCompare };
})(window);
