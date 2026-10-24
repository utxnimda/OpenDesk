(function (global) {
  "use strict";

  var API = "/api/catalog-library/playback/", nextId = 0;
  function array(value) { return Array.isArray(value) ? value : []; }
  function text(value) { return String(value == null ? "" : value); }
  function clone(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
  function errorText(error) { return error && error.message ? error.message : text(error); }
  function el(tag, className, value) {
    var node = document.createElement(tag); if (className) node.className = className;
    if (value != null) node.textContent = text(value); return node;
  }
  function append(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function clear(parent) { while (parent.firstChild) parent.removeChild(parent.firstChild); }
  function durationLabel(value) {
    if (value == null || value === "" || !Number.isFinite(Number(value)) || Number(value) < 0) return "—";
    var seconds = Math.floor(Number(value)), hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
    return (hours ? hours + ":" + String(minutes).padStart(2, "0") : minutes) + ":" + String(seconds % 60).padStart(2, "0");
  }
  function positiveDuration(value) { return typeof value === "number" && Number.isFinite(value) && value > 0; }
  function durationDisplay(row, cached) {
    if (cached && positiveDuration(row.duration_seconds)) return { label: durationLabel(row.duration_seconds), source: "local", title: "本地视频时长" };
    var seconds = cached ? row.source_duration_seconds : row.duration_seconds, raw = text(cached ? row.source_duration : row.duration).trim();
    if (positiveDuration(seconds)) return { label: durationLabel(seconds), source: "source", title: "来源章节时长（尚无本地视频时长）" };
    if (raw && !/^(?:0+(?::0+)*|[-—]+)$/.test(raw)) return { label: raw, source: "source", title: "来源章节时长（尚无本地视频时长）" };
    return { label: "—", source: "none", title: "暂无时长" };
  }
  function dateLabel(value) { return text(value).replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3"); }
  function normalizedPath(value) { return text(value).trim().replace(/\\/g, "/"); }
  function pathIsRelative(value) { return !!value && !/^(?:\/|[a-z]:)/i.test(value) && !value.split("/").some(function (part) { return part === ".." || part === "." || !part; }); }
  function playable(row) { return !!row.relative_path && (row.status === "automatic" || row.status === "confirmed"); }
  function resourceLabel(row, index) {
    var group = text(row.press_group).trim();
    return [text(row.press_format).trim() || "资源 " + (index + 1), group && group !== "----" ? group : "—", row.continuation_title || row.title].filter(Boolean).join(" · ");
  }

  function mount(parent, options) {
    options = options || {};
    var detail = options.detail || {}, item = detail.item || detail.work || detail;
    var resources = array(detail.resources || item.resources || item.press), originalChapters = array(detail.chapters || item.chapters || (detail.record || {}).chapters);
    var ref = clone(detail.ref || item.ref), cached = detail.playback, selected = cached && Number.isInteger(cached.resource_index) && resources[cached.resource_index] ? cached.resource_index : -1;
    if (selected >= 0 && resources[selected].is_playback_resource === false) selected = -1;
    var disposed = false, generation = 0, busy = "", snapshot = null, dialogState = null, settingsButtons = {};
    if (selected >= 0 && cached.revision && Array.isArray(cached.chapters) && Array.isArray(cached.files)) snapshot = clone(cached);
    var root = el("section", "chapter-player"), id = "chapter-player-" + (++nextId);
    var toolbar = el("div", "chapter-player-toolbar"), currentResource = el("span", "chapter-player-current-resource");
    var chooseButton = button("选择播放资源", function () { if (options.onChooseResource) options.onChooseResource(); });
    var scanButton = button("重新识别文件", scan), batchButton = button("批量设置", editBatch), list = el("ol", "chapter-player-list");
    var heading = el("div", "chapter-player-row chapter-player-head"); heading.setAttribute("aria-hidden", "true");
    append(toolbar, currentResource, chooseButton, scanButton, batchButton); append(root, toolbar, heading, list); parent.appendChild(root);

    function active(epoch) { return !disposed && epoch === generation; }
    function status(value, error) { if (!disposed && options.setStatus) options.setStatus(value, !!error); }
    function button(label, action, disabled, className) {
      var node = el("button", "library-button" + (className ? " " + className : ""), label); node.type = "button"; node.disabled = !!disabled;
      node.addEventListener("click", function () {
        if (node.disabled || disposed) return;
        Promise.resolve().then(action).catch(function (error) { if (!disposed) status(errorText(error), true); });
      }); return node;
    }
    async function request(route, payload) {
      var perform = options.request || global.NimdaCommon && global.NimdaCommon.fetchJson;
      if (!perform) throw new Error("章节播放请求不可用，请重新加载页面。");
      var response = await perform(API + route, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      var data = response && response.data !== undefined ? response.data : response;
      if (!data || data.ok === false || response && response.res && !response.res.ok) throw new Error(data && (data.error || data.message) || "章节播放请求失败。");
      return data;
    }
    function payload() { return { ref: clone(ref), resource_index: selected, revision: snapshot && snapshot.revision }; }
    function acceptScan(data) {
      if (data.resource_index != null && Number(data.resource_index) !== selected) throw new Error("资源已变化，请重新识别本地文件。");
      if (!data.revision || !Array.isArray(data.chapters) || !Array.isArray(data.files)) throw new Error("本地文件识别结果不完整，请重新识别。");
      snapshot = clone(data); if (data.ref || data.detail && data.detail.ref) ref = clone(data.ref || data.detail.ref);
    }
    function scanMessage(data, prefix) {
      var rows = array(data.chapters), count = rows.filter(playable).length;
      var warnings = array(data.warnings).map(function (warning) { return typeof warning === "string" ? warning : warning.message || warning.reason || ""; }).filter(Boolean);
      return prefix + " " + count + " / " + rows.length + " 集可播放。" + (warnings.length ? " " + warnings.join("；") : "");
    }
    async function scan() {
      if (disposed || busy || selected < 0 || !ref) return;
      var epoch = generation; busy = "scan"; closeDialog(false); render();
      try {
        var result = await request("scan", { ref: clone(ref), resource_index: selected });
        if (!active(epoch)) return;
        acceptSaved(result, scanMessage(result, "已重新识别并保存。"));
      } catch (error) { if (active(epoch)) status(errorText(error), true); }
      finally { if (active(epoch)) { busy = ""; render(); } }
    }
    async function openChapter(row) {
      if (disposed || busy || dialogState || !snapshot || !playable(row)) return;
      var epoch = generation, requestPayload = payload();
      requestPayload.chapter_key = row.chapter_key; requestPayload.relative_path = row.relative_path;
      busy = "open"; render();
      try {
        await request("open", requestPayload);
        if (active(epoch)) status("已请求使用系统默认播放器播放第 " + row.number + " 集。");
      } catch (error) { if (active(epoch)) status(errorText(error), true); }
      finally { if (active(epoch)) { busy = ""; render(); } }
    }
    function acceptSaved(result, message) {
      if (result.requires_rescan && result.db_committed) {
        snapshot = null; ref = clone(result.ref || result.detail && result.detail.ref || null);
        closeDialog(false); status(result.message || "播放信息已保存，请重新加载作品后识别本地文件。");
      } else {
        acceptScan(result); closeDialog(false); status(message);
      }
      if (options.onSaved) options.onSaved(clone(result));
    }
    function closeDialog(restoreFocus) {
      if (!dialogState) return;
      var old = dialogState; dialogState = null;
      if (old.dispose) old.dispose();
      else { if (old.node.open && typeof old.node.close === "function") old.node.close(); old.node.remove(); }
      if (restoreFocus && !disposed) { var target = old.batch ? batchButton : settingsButtons[old.key]; if (target) target.focus(); }
    }
    function editBatch() {
      if (disposed || busy || !snapshot || dialogState || !global.NimdaChapterBatchDialog) return;
      var handle = global.NimdaChapterBatchDialog.open({ chapters: clone(snapshot.chapters), files: clone(snapshot.files), titlePreference: detail.chapter_title_preference,
        onCancel: function () { closeDialog(true); }, onSave: saveBatch,
        onError: function (error) { if (!disposed) status(errorText(error), true); } });
      dialogState = { node: handle.node, dispose: handle.dispose, batch: true };
    }
    async function saveBatch(assignments) {
      if (disposed || busy || !snapshot || !dialogState || !dialogState.batch) return;
      var epoch = generation, requestPayload = payload(); requestPayload.assignments = assignments;
      busy = "save"; render();
      try {
        var result = await request("save", requestPayload);
        if (active(epoch)) acceptSaved(result, "已批量保存 " + assignments.length + " 项章节文件对应关系。");
      } catch (error) { if (active(epoch)) status(errorText(error), true); }
      finally { if (active(epoch)) { busy = ""; render(); if (!dialogState) batchButton.focus(); } }
    }
    function editMatch(row) {
      if (disposed || busy || !snapshot || dialogState || row.binding_allowed === false) return;
      var dialog = el("dialog", "chapter-player-dialog"), form = el("form", "chapter-player-match-form");
      var heading = el("h2", "", "第 " + row.number + " 集 · 匹配文件"); heading.id = id + "-match-heading";
      dialog.setAttribute("aria-labelledby", heading.id);
      var fileField = el("label", "chapter-player-match-field"), files = el("select"), inputField = el("label", "chapter-player-match-field"), input = el("input");
      input.type = "text"; input.autocomplete = "off"; input.placeholder = "相对资源目录的文件路径"; input.value = text(row.relative_path);
      append(fileField, el("span", "", "本地文件"), files); append(inputField, el("span", "", "相对路径"), input);
      var automatic = el("option", "", "恢复自动匹配"); automatic.value = ""; files.appendChild(automatic);
      var paths = snapshot.files.map(function (file) { return text(file.relative_path); }).filter(function (path, index, all) { return path && all.indexOf(path) === index; });
      var candidates = array(row.candidates), ordered = paths.filter(function (path) { return candidates.indexOf(path) >= 0; }).concat(paths.filter(function (path) { return candidates.indexOf(path) < 0; }));
      ordered.forEach(function (path) { var option = el("option", "", (candidates.indexOf(path) >= 0 ? "候选 · " : "") + path); option.value = path; files.appendChild(option); });
      files.value = paths.indexOf(input.value) >= 0 ? input.value : "";
      var validation = el("p", "chapter-player-validation"); validation.setAttribute("role", "alert"); validation.hidden = true;
      var controls = el("div", "chapter-player-dialog-actions"), cancel = button("取消", function () { closeDialog(true); }), save = el("button", "library-button is-primary", "确认保存匹配"); save.type = "submit";
      append(controls, cancel, save); append(form, heading, fileField, inputField, validation, controls); dialog.appendChild(form);
      dialogState = { node: dialog, key: row.chapter_key };
      function clearValidation() { validation.hidden = true; validation.textContent = ""; input.removeAttribute("aria-invalid"); }
      files.addEventListener("change", function () { input.value = files.value; clearValidation(); });
      input.addEventListener("input", function () { var path = normalizedPath(input.value); files.value = paths.indexOf(path) >= 0 ? path : ""; clearValidation(); });
      dialog.addEventListener("cancel", function (event) { event.preventDefault(); if (busy !== "save") closeDialog(true); });
      dialog.addEventListener("close", function () { if (dialogState && dialogState.node === dialog && busy !== "save") closeDialog(true); });
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (disposed || busy || !dialogState || dialogState.node !== dialog) return;
        var path = normalizedPath(input.value);
        if (path && (!pathIsRelative(path) || paths.indexOf(path) < 0)) {
          validation.textContent = "请选择识别列表中的本地相对路径；列表外文件请重新识别。"; validation.hidden = false; input.setAttribute("aria-invalid", "true"); input.focus(); return;
        }
        saveMatch(row, path, { dialog: dialog, files: files, input: input, cancel: cancel, save: save });
      });
      document.body.appendChild(dialog); if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", ""); files.focus();
    }
    async function saveMatch(row, path, controls) {
      var epoch = generation, requestPayload = payload(); requestPayload.assignments = [{ chapter_key: row.chapter_key, relative_path: path }];
      busy = "save"; controls.files.disabled = controls.input.disabled = controls.cancel.disabled = controls.save.disabled = true; render();
      try {
        var result = await request("save", requestPayload);
        if (!active(epoch)) return;
        acceptSaved(result, path ? "章节文件匹配已保存。" : "已恢复自动匹配。");
      } catch (error) { if (active(epoch)) status(errorText(error), true); }
      finally {
        if (active(epoch)) {
          busy = ""; controls.files.disabled = controls.input.disabled = controls.cancel.disabled = controls.save.disabled = false; render();
          if (!dialogState && settingsButtons[row.chapter_key]) settingsButtons[row.chapter_key].focus();
        }
      }
    }
    function render() {
      if (disposed) return;
      root.setAttribute("aria-busy", busy ? "true" : "false");
      currentResource.hidden = selected < 0; currentResource.textContent = selected >= 0 ? resourceLabel(resources[selected], selected) : ""; currentResource.title = currentResource.textContent;
      chooseButton.hidden = selected >= 0; chooseButton.disabled = !!busy;
      scanButton.hidden = selected < 0; scanButton.disabled = !!busy || selected < 0 || !ref; scanButton.textContent = busy === "scan" ? "正在识别…" : "重新识别文件";
      batchButton.hidden = selected < 0; batchButton.disabled = !!busy || !snapshot || !snapshot.files.length || !snapshot.chapters.some(function (row) { return row.binding_allowed !== false && row.chapter_key; }) || !global.NimdaChapterBatchDialog;
      clear(list); settingsButtons = {};
      var rows = snapshot ? snapshot.chapters : originalChapters;
      var hasContinuous = !!(detail.chapter_numbering && detail.chapter_numbering.has_continuous) || rows.some(function (row) { return row.has_continuous_number === true; });
      root.className = "chapter-player" + (hasContinuous ? " has-continuous-numbering" : "");
      clear(heading);
      var columns = [["集数", "chapter-player-head-episode"]];
      columns.concat([["章节", "chapter-player-head-title"], ["播出日期", "chapter-player-head-date"], ["时长", "chapter-player-head-duration"], ["设置", "chapter-player-head-actions"]]).forEach(function (column) { heading.appendChild(el("span", column[1], column[0])); });
      if (!rows.length) { list.appendChild(el("li", "chapter-player-empty", "暂无章节信息。")); return; }
      rows.forEach(function (row, index) {
        var number = global.NimdaChapterNumbers.number(row, index), li = el("li", "chapter-player-row"), episode = el("div", "chapter-player-episode");
        var canPlay = !!snapshot && playable(row), playbackReason;
        var playbackControl = button("▶", function () { return openChapter(row); }, !!busy || !canPlay, "chapter-player-play");
        playbackControl.setAttribute("aria-label", "播放第 " + number + " 集"); playbackControl.title = "使用系统默认播放器播放";
        if (!canPlay) {
          var unavailable = !snapshot ? (selected < 0 ? ["未绑定", "请在资源页签选择播放资源"] : ["未识别", "请重新识别文件"]) :
            ({ ambiguous: ["待选", "有多个候选视频，请在设置中确认"], missing: ["缺失", "绑定的视频文件已缺失，请重新识别或修改匹配"] })[row.status] || ["无资源", "未找到该章节的对应视频，可在设置中手动匹配"];
          playbackControl.title = unavailable[0] + "：" + unavailable[1];
          playbackReason = el("span", "chapter-player-play-reason", playbackControl.title); playbackReason.id = id + "-play-reason-" + index; playbackReason.hidden = true;
          playbackControl.setAttribute("aria-describedby", playbackReason.id);
        }
        var numberLabel = el("span", "chapter-player-number", global.NimdaChapterNumbers.label(row, index));
        numberLabel.title = global.NimdaChapterNumbers.title(row, index);
        append(episode, numberLabel, playbackControl, playbackReason);
        var chapterTitle = global.NimdaChapterTitles.display(row, detail.chapter_title_preference);
        var titleNode = el("span", "chapter-player-title", chapterTitle.text); titleNode.title = chapterTitle.tooltip;
        var displayDuration = durationDisplay(row, !!snapshot);
        var date = el("span", "chapter-player-date", dateLabel(row.air_date || row.date || row.airdate) || "—"), duration = el("span", "chapter-player-duration", displayDuration.label);
        date.title = "播出日期"; duration.title = displayDuration.title; duration.setAttribute("data-duration-source", displayDuration.source);
        var stateLabel = snapshot ? ({ automatic: "自动匹配", confirmed: "已确认", ambiguous: "多个候选", unmatched: "未匹配", missing: "文件缺失" })[row.status] || "未匹配" : "";
        var setting = button("设置", function () { editMatch(row); }, !!busy || !snapshot || row.binding_allowed === false, "chapter-player-setting"); setting.setAttribute("aria-label", "设置第 " + number + " 集匹配");
        setting.title = row.binding_allowed === false ? "章节缺少唯一标识或集数重复，请先修正章节数据" : stateLabel || "请先选择播放资源";
        settingsButtons[row.chapter_key] = setting; li.appendChild(episode);
        append(li, titleNode, date, duration, setting); list.appendChild(li);
      });
    }
    render();
    return { dispose: function () { if (disposed) return; disposed = true; generation++; busy = ""; closeDialog(false); root.remove(); } };
  }

  global.NimdaChapterPlayer = { mount: mount, durationLabel: durationLabel };
})(window);
