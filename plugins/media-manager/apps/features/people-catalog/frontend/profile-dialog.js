(function (global) {
  "use strict";
  var active = null, nextId = 0;
  var labels = { bangumi: "Bangumi", tmdb: "TMDB", wikipedia: "中文维基百科", douban: "豆瓣", local: "原有本地资料" };
  function node(tag, cls, value) { var n = document.createElement(tag); if (cls) n.className = cls; if (value != null) n.textContent = String(value); return n; }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function button(label, action) { var n = node("button", "library-button", label); n.type = "button"; n.addEventListener("click", function () { if (!n.disabled) action(); }); return n; }
  function field(parent, label, control) { var n = node("label", "person-library-field"); n.appendChild(node("span", "", label)); n.appendChild(control); parent.appendChild(n); return control; }
  function array(value) { return Array.isArray(value) ? value : []; }
  function valueText(value) { if (value && typeof value === "object" && Object.keys(value).length === 3 && value.category && value.reality && value.subtype) return global.NimdaPeopleTaxonomy.label({ taxonomy: value }); return value == null || value === "" ? "（空）" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value); }
  function notice(message, error) { if (global.NimdaOperationCenter) global.NimdaOperationCenter.notice(message, !!error, { path: "/api/catalog-library/people-library" }); }
  function operationTitle(route, body) {
    if (route.indexOf("characters/merge/") === 0) return ({ candidates: "检查相似角色候选", preview: "预览角色合并", apply: "保存确认的角色合并" })[route.split("/").pop()] || "处理角色合并";
    var actions = { "people-library/browse": "浏览本地人物库", "people-library/detail": "读取本地人物资料", "people-library/edit-preview": "预览人物资料修改", "people-library/edit-apply": "保存人物资料修改", "people-library/clear-preview": "预览人物资料清空", "people-library/clear-apply": "清空所选人物资料", "people-library/source-preview": "读取来源资料预览", "people-library/source-apply": "采用所选来源资料", "people-library/image-preview": "预览人物图片", "people-library/image-apply": "保存人物图片", "people-library/image-candidates": "读取 TMDB 剧照候选" };
    return (body && labels[body.provider] ? labels[body.provider] + " · " : "") + (actions[route] || "处理人物资料");
  }
  async function request(route, body) {
    var response = await global.NimdaCommon.fetchJson("/api/catalog-library/" + route, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}), operationTitle: operationTitle(route, body) });
    var data = response && response.data !== undefined ? response.data : response;
    if (!data || data.ok === false || response && response.res && !response.res.ok) throw new Error(data && (data.error || data.message) || "人物资料处理失败");
    return data;
  }
  function close(force) {
    if (!active || active.saving && !force) return;
    var old = active; active = null; old.serial += 1;
    if (old.dialog.open && old.dialog.close) old.dialog.close(); old.dialog.remove();
    if (old.trigger && old.trigger.isConnected !== false) old.trigger.focus();
    if (old.changed) {
      if (old.options.onChanged) old.options.onChanged();
      document.dispatchEvent(new CustomEvent("nimda-people-changed", { detail: { person_id: old.id } }));
    }
  }
  function alive(s, serial) { return active === s && (serial == null || serial === s.serial); }
  async function run(s, action, saving) {
    if (!alive(s) || s.busy) return;
    var sequence = ++s.serial; s.busy = true; s.saving = !!saving;
    Array.from(s.dialog.querySelectorAll("button,input,select,textarea")).forEach(function (n) { n.disabled = true; });
    s.close.disabled = !!saving; s.dialog.setAttribute("aria-busy", "true");
    try { await action(sequence); }
    catch (e) { if (alive(s, sequence)) { notice(e.message || String(e), true); if (!s.detail) clear(s.body); var retry = node("p", "person-library-error", e.message || String(e)); retry.setAttribute("role", "alert"); s.body.appendChild(retry); if (!s.detail) s.body.appendChild(button("重试读取人物资料", function () { run(s, function (seq) { return reload(s, seq); }); })); } }
    finally { if (alive(s, sequence)) { s.busy = false; s.saving = false; s.dialog.setAttribute("aria-busy", "false"); Array.from(s.dialog.querySelectorAll("button,input,select,textarea")).forEach(function (n) { n.disabled = n.dataset.locked === "true"; }); } }
  }
  function reset(s) { clear(s.body); }
  function safeSource(url) { return /^https:\/\/(?:bangumi\.tv|bgm\.tv|api\.bgm\.tv|www\.themoviedb\.org|zh\.wikipedia\.org|(?:www|movie)\.douban\.com)\//.test(String(url || "")); }
  function profile(s) {
    reset(s); var person = s.detail.person, helpers = global.NimdaPeoplePanel;
    var hero = node("div", "person-library-profile-hero"); hero.appendChild(helpers.portrait(person, s.preference)); hero.appendChild(helpers.entityProfile(person, s.preference)); s.body.appendChild(hero);
    var birth = person.birth_date || {}, parts = [birth.year, birth.month || birth.mon, birth.day], birthday = parts.some(Boolean) ? parts.map(function (part) { return part || "?"; }).join("-") : "";
    var classification = global.NimdaPeopleTaxonomy.project(person);
    if (classification.category === "person" && (person.gender || birthday)) s.body.appendChild(node("p", "library-meta", [person.gender, birthday].filter(Boolean).join(" · ")));
    var actions = node("div", "person-library-actions");
    actions.appendChild(button("编辑资料", function () { edit(s); })); actions.appendChild(button("获取来源资料", function () { sources(s); }));
    actions.appendChild(button("图片来源", function () { images(s); }));
    global.NimdaPeopleImages.evidence(s.body, person.avatar_image_source);
    if (variantRows(person).length) actions.appendChild(button("名称与简介版本", function () { versions(s); }));
    actions.appendChild(button("清空资料…", function () { run(s, async function (seq) { var data = await request("people-library/clear-preview", base(s)); if (alive(s, seq)) showDiff(s, data, "people-library/clear-apply", true); }); })); s.body.appendChild(actions);
    if (person.type === "character" && classification.category === "person" && classification.reality === "fictional") mergeHints(s);
    var relations = array(s.detail.relations); if (relations.length) { var box = node("details", "person-library-relations"); box.appendChild(node("summary", "", "关联作品（" + relations.length + "）")); relations.forEach(function (row) { var relation = row.relation || {}, actor = row.actor && helpers.entityDisplay(row.actor, s.preference).text; box.appendChild(node("p", "", [row.name, row.role, row.character_name, actor, helpers.qualifierLabel && helpers.qualifierLabel(relation.qualifier)].filter(Boolean).join(" · "))); }); s.body.appendChild(box); }
    if (array(s.detail.sources).length) { var links = node("section", "person-library-sources"); links.appendChild(node("h3", "", "来源")); s.detail.sources.forEach(function (source) { var text = (labels[source.provider] || source.provider) + " · " + source.external_id; if (safeSource(source.source_url)) { var a = node("a", "library-source-link", text); a.href = source.source_url; a.target = "_blank"; a.rel = "noopener noreferrer"; links.appendChild(a); } else links.appendChild(node("span", "", text)); }); s.body.appendChild(links); }
    s.body.appendChild(node("p", "library-meta", "这是全库共享的人物资料，编辑后会用于所有关联作品。"));
  }
  function base(s) { return { person_id: s.id, version: s.detail.version }; }
  function variantRows(person) {
    var variants = person.profile_variants || {}, rows = [];
    ["name", "summary"].forEach(function (kind) {
      Object.keys(variants[kind] || {}).forEach(function (language) {
        if (!global.NimdaPeoplePanel.localizedField("localized." + kind + "." + language)) return;
        var values = variants[kind][language];
        var providers = Object.keys(values || {}).filter(function (provider) { return values[provider] && typeof values[provider].value === "string" && values[provider].value.trim(); });
        if (providers.length) rows.push({ kind: kind, language: language, values: values, providers: providers });
      });
    });
    return rows;
  }
  function variantLabel(key, value, keys, values) {
    var provider = value && value.provider || key.split(":")[0], label = labels[provider] || provider;
    var duplicates = keys.filter(function (other) { return (values[other].provider || other.split(":")[0]) === provider; }).length > 1;
    return label + (duplicates ? " · " + String(value.external_id || key).slice(-16) : "");
  }
  function versions(s) {
    reset(s);
    var person = s.detail.person, preferences = JSON.parse(JSON.stringify(person.profile_preferences || {}));
    var form = node("form", "person-library-versions"), original = JSON.stringify(preferences);
    form.appendChild(node("h3", "", "名称与简介版本"));
    variantRows(person).forEach(function (row) {
      var group = node("section", "person-library-version"), title = (row.kind === "name" ? "名称" : "简介") + " · " + global.NimdaPeoplePanel.languageLabel(row.language);
      var select = node("select"), preview = node("p", row.kind === "summary" ? "library-person-summary person-library-version-preview" : "person-library-version-preview");
      select.setAttribute("aria-label", title + "来源");
      [["auto", "自动（按来源优先）"], ["locked", "手动固定当前值"]].concat(row.providers.map(function (provider) { return ["source:" + provider, variantLabel(provider, row.values[provider], row.providers, row.values)]; })).forEach(function (entry) { var option = node("option", "", entry[1]); option.value = entry[0]; select.appendChild(option); });
      var preference = preferences[row.kind] && preferences[row.kind][row.language] || {};
      select.value = preference.locked ? "locked" : preference.source && row.providers.indexOf(preference.source) >= 0 ? "source:" + preference.source : "auto";
      var sourceLink = node("a", "library-source-link", "查看来源"); sourceLink.target = "_blank"; sourceLink.rel = "noopener noreferrer";
      function showValue() {
        var source = select.value.indexOf("source:") === 0 ? row.values[select.value.slice(7)] : null;
        var current = global.NimdaPeoplePanel.fieldValue(person, "localized." + row.kind + "." + row.language);
        preview.textContent = source ? source.value : current || "（当前为空）";
        sourceLink.hidden = !source || !safeSource(source.source_url);
        sourceLink.href = sourceLink.hidden ? "" : source.source_url;
      }
      select.addEventListener("change", function () {
        if (select.value === "auto") { if (preferences[row.kind]) { delete preferences[row.kind][row.language]; if (!Object.keys(preferences[row.kind]).length) delete preferences[row.kind]; } }
        else { var kind = preferences[row.kind] || (preferences[row.kind] = {}); kind[row.language] = select.value === "locked" ? { locked: true } : { source: select.value.slice(7) }; }
        showValue();
      });
      field(group, title, select); group.appendChild(preview); group.appendChild(sourceLink); form.appendChild(group); showValue();
    });
    var submit = node("button", "library-button", "预览显示设置"); submit.type = "submit"; form.appendChild(submit);
    form.appendChild(button("返回资料", function () { profile(s); }));
    form.addEventListener("submit", function (event) { event.preventDefault(); if (JSON.stringify(preferences) === original) { notice("没有修改显示设置"); return; } run(s, async function (seq) { var preview = await request("people-library/edit-preview", Object.assign(base(s), { changes: { profile_preferences: preferences } })); if (alive(s, seq)) showDiff(s, preview, "people-library/edit-apply", false); }); });
    s.body.appendChild(form);
  }
  function mergeHints(s) {
    var section = node("section", "person-library-merge-hints"), list = node("div"), status = node("p", "library-meta", "正在检查同名与别名角色；不会自动合并…");
    section.appendChild(node("h3", "", "可能相同的角色")); section.appendChild(status); section.appendChild(list); s.body.appendChild(section);
    var identity = s.id, requestSerial = 0;
    function current() { return alive(s) && s.id === identity && section.isConnected; }
    function showRows(rows, suggested) {
      clear(list); rows = array(rows).filter(function (row) { return row.id !== identity; });
      status.textContent = rows.length ? (suggested ? "发现 " + rows.length + " 个同名／别名候选。请核对身份，再选择合并目标。" : "查找结果；同名不代表同一角色。") : "没有发现明确的同名候选；也可以手动查找。";
      rows.forEach(function (candidate) {
        var row = node("article", "person-library-source-result"), display = global.NimdaPeoplePanel.entityDisplay(candidate, s.preference);
        var name = node("strong", "", display.text); name.title = display.tooltip; row.appendChild(name);
        if (array(candidate.matched_names).length) row.appendChild(node("p", "library-meta", "相同名称：" + candidate.matched_names.join(" / ")));
        if (candidate.reason) row.appendChild(node("p", "library-meta", candidate.reason));
        var choose = button("预览合并到此角色", function () { run(s, async function (seq) { var preview = await request("characters/merge/preview", { person_id: identity, target_id: candidate.id }); if (alive(s, seq)) showMerge(s, preview); }); });
        choose.disabled = !!candidate.blocked; choose.dataset.locked = String(choose.disabled); row.appendChild(choose); list.appendChild(row);
      });
    }
    request("characters/merge/candidates", { person_id: identity }).then(function (data) { if (current() && requestSerial === 0) showRows(data.candidates, true); }).catch(function (error) { if (current() && requestSerial === 0) status.textContent = "候选检查未完成：" + error.message; });
    var form = node("form", "person-library-source-form"), search = node("input"); search.type = "search"; search.placeholder = "查找其他角色的名称或别名"; search.setAttribute("aria-label", "查找合并目标角色"); form.appendChild(search); var submit = node("button", "library-button", "查找角色"); submit.type = "submit"; form.appendChild(submit); section.appendChild(form);
    form.addEventListener("submit", function (event) { event.preventDefault(); if (!search.value.trim() || !current()) return; var serial = ++requestSerial; submit.disabled = true; request("people-library/browse", { type: "character", category: "person", reality: "fictional", query: search.value.trim(), page: 1, page_size: 30 }).then(function (data) { if (current() && serial === requestSerial) showRows(data.items, false); }).catch(function (error) { if (current() && serial ===requestSerial) status.textContent = error.message; }).finally(function () { if (current() && serial === requestSerial) submit.disabled = false; }); });
  }
  function showMerge(s, preview) {
    if (!preview || preview.person_id !== s.id || !preview.target_id || preview.target_id === s.id || !preview.preview_token) throw new Error("角色合并预览与当前资料不一致，请重新检查。");
    reset(s); var selected = new Set(), box = node("div", "person-library-diff");
    var target = preview.target || {}, donor = preview.donor || s.detail.person;
    box.appendChild(node("h3", "", "合并角色档案"));
    box.appendChild(node("p", "", global.NimdaPeoplePanel.entityDisplay(donor, s.preference).text + " → " + global.NimdaPeoplePanel.entityDisplay(target, s.preference).text));
    box.appendChild(node("p", "library-meta", "保留右侧目标角色。来源和作品关联会转移；下面未勾选的资料保留目标原值。原档案保留合并标记，不删除媒体。"));
    var affected = array(preview.affected_works); box.appendChild(node("p", "library-meta", "将更新 " + (preview.affected_work_count == null ? affected.length : preview.affected_work_count) + " 部作品的角色引用。"));
    affected.forEach(function (work) { box.appendChild(node("p", "library-meta", work.name || work.title || "关联作品")); });
    array(preview.warnings).forEach(function (warning) { box.appendChild(node("p", "library-meta", typeof warning === "string" ? warning : warning.message)); });
    array(preview.diff).forEach(function (row) { var line = node("section", "person-library-diff-row"), check = node("input"); check.type = "checkbox"; check.disabled = row.selectable === false; check.dataset.locked = String(check.disabled); check.addEventListener("change", function () { if (check.checked) selected.add(row.field); else selected.delete(row.field); }); field(line, row.label || row.field, check); var values = node("div", "person-library-diff-values"); [row.before, row.after].forEach(function (value, index) { var part = node("div"); part.appendChild(node("strong", "", index ? "选择后" : "目标当前")); if (row.field === "avatar") avatarValue(part, value); else part.appendChild(node("pre", "", valueText(value))); values.appendChild(part); }); line.appendChild(values); box.appendChild(line); });
    var acknowledged = node("input"); acknowledged.type = "checkbox"; field(box, "我已核对，确认这两个档案属于同一角色", acknowledged);
    var confirm = button("确认合并这两个角色", function () { if (!acknowledged.checked) return; run(s, async function (seq) { await request("characters/merge/apply", { person_id: preview.person_id, target_id: preview.target_id, preview_token: preview.preview_token, selected_fields: Array.from(selected) }); if (!alive(s, seq)) return; s.changed = true; s.id = preview.target_id; notice("角色已按确认合并，作品引用已更新。"); await reload(s, seq); }, true); });
    confirm.disabled = true; confirm.dataset.locked = "true"; acknowledged.addEventListener("change", function () { confirm.disabled = !acknowledged.checked; confirm.dataset.locked = String(confirm.disabled); }); box.appendChild(confirm); box.appendChild(button("取消合并", function () { profile(s); })); s.body.appendChild(box);
  }
  async function reload(s, seq) { var data = await request("people-library/detail", { person_id: s.id }); if (alive(s, seq)) { s.detail = data; var display = global.NimdaPeoplePanel.entityDisplay(data.person, s.preference); s.heading.textContent = display.text; s.heading.title = display.tooltip; profile(s); } }
  function edit(s) {
    reset(s); var form = node("form", "person-library-edit"), readers = {}, originals = {}, languageGroups = {};
    var fieldNodes = {}, category = global.NimdaPeopleTaxonomy.project(s.detail.person).category;
    function showPersonalFields(value) { category = value.category; ["gender", "birth_date"].forEach(function (key) { if (fieldNodes[key]) fieldNodes[key].hidden = category !== "person"; }); }
    array(s.detail.edit_fields).filter(function (spec) { return spec.editable; }).forEach(function (spec) {
      var value = Object.prototype.hasOwnProperty.call(spec, "value") ? spec.value : global.NimdaPeoplePanel.fieldValue(s.detail.person, spec.field), input;
      originals[spec.field] = value;
      var holder = form;
      if (global.NimdaPeoplePanel.localizedField(spec.field)) {
        var language = spec.field.split(".")[2];
        if (!languageGroups[language]) { var group = node("details", "person-library-language-fields"); group.open = false; group.appendChild(node("summary", "", global.NimdaPeoplePanel.languageLabel(language))); languageGroups[language] = group; form.appendChild(group); }
        holder = languageGroups[language];
        if (spec.required || (Array.isArray(value) ? value.length : typeof value === "string" && value.trim())) holder.open = true;
      }
      if (spec.type === "taxonomy") { var editor = global.NimdaPeopleTaxonomy.createEditor(value || global.NimdaPeopleTaxonomy.project(s.detail.person), showPersonalFields, spec.options); input = editor.element; readers[spec.field] = editor.read; }
      else if (spec.type === "select") { input = node("select"); array(spec.options).forEach(function (option) { var n = node("option", "", option.label); n.value = option.value; input.appendChild(n); }); input.value = value || ""; readers[spec.field] = function () { return input.value; }; }
      else if (spec.type === "date-parts") { input = node("div", "person-library-date"); var parts = {}; ["year", "month", "day"].forEach(function (part, index) { var n = node("input"); n.type = "number"; n.min = "1"; n.max = String([9999, 12, 31][index]); n.placeholder = ["年", "月", "日"][index]; n.setAttribute("aria-label", "出生" + n.placeholder); n.value = value && value[part] || ""; input.appendChild(n); parts[part] = n; }); readers[spec.field] = function () { var result = {}; Object.keys(parts).forEach(function (part) { if (parts[part].value) result[part] = Number(parts[part].value); }); return result; }; }
      else { input = node(spec.type === "text" ? "input" : "textarea"); input.value = spec.type === "json" ? JSON.stringify(value || [], null, 2) : spec.type === "list" ? array(value).join("\n") : value || ""; input.required = !!spec.required; if (spec.type === "list") input.placeholder = "每行一项"; readers[spec.field] = function () { return spec.type === "json" ? JSON.parse(input.value || "[]") : spec.type === "list" ? input.value.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean) : input.value; }; }
      if (spec.type === "taxonomy") { var group = node("fieldset", "person-library-classification-field"), legend = node("legend", "", spec.label); group.appendChild(legend); group.appendChild(input); form.appendChild(group); }
      else { field(holder, spec.label, input); fieldNodes[spec.field] = input.parentElement || holder.children[holder.children.length - 1]; }
    });
    showPersonalFields({ category: category });
    var submit = node("button", "library-button is-primary", "预览修改"); submit.type = "submit"; form.appendChild(submit); form.appendChild(button("返回资料", function () { profile(s); }));
    form.addEventListener("submit", function (event) { event.preventDefault(); run(s, async function (seq) { var changes = {}; Object.keys(readers).forEach(function (key) { if (category !== "person" && (key === "gender" || key === "birth_date")) return; var value = readers[key](); if (JSON.stringify(value) !== JSON.stringify(originals[key] == null ? (Array.isArray(value) ? [] : typeof value === "object" ? {} : "") : originals[key])) changes[key] = value; }); if (!Object.keys(changes).length) throw new Error("没有修改任何资料。"); var data = await request("people-library/edit-preview", Object.assign(base(s), { changes: changes })); if (alive(s, seq)) showDiff(s, data, "people-library/edit-apply", false); }); }); s.body.appendChild(form);
  }
  function avatarValue(parent, value) { var asset = value && value.asset_id; if (typeof asset === "string" && /^[a-f0-9]{64}\.(jpg|png|webp|gif)$/.test(asset)) { parent.appendChild(global.NimdaPeoplePanel.portrait({ name: "头像预览", avatar_url: "/api/catalog-library/people/assets/" + asset }, "chinese")); } else parent.appendChild(node("pre", "", valueText(value))); }
  function images(s) {
    reset(s);
    global.NimdaPeopleImages.render(s.body, { detail: s.detail, onBack: function () { profile(s); }, onCandidates: function (readPayload, show) {
      run(s, async function (seq) {
        var payload = await readPayload(); if (!alive(s, seq)) return;
        var result = await request("people-library/image-candidates", Object.assign(base(s), payload));
        if (alive(s, seq)) show(result, payload);
      });
    }, onPreview: function (readPayload) {
      run(s, async function (seq) {
        var payload = await readPayload(); if (!alive(s, seq)) return;
        var preview = await request("people-library/image-preview", Object.assign(base(s), payload));
        if (alive(s, seq)) showDiff(s, preview, "people-library/image-apply", false);
      });
    } });
  }
  function diffValue(parent, row, value, index) {
    if (row.field === "avatar") { avatarValue(parent, value); return; }
    if (row.field === "profile_preferences") {
      if (array(row.display_diff).length) {
        row.display_diff.forEach(function (change) { parent.appendChild(node("strong", "library-meta", change.label || change.field)); parent.appendChild(node("pre", "", valueText(index ? change.after : change.before))); });
      } else {
        var lines = [];
        ["name", "summary"].forEach(function (kind) { Object.keys(value && value[kind] || {}).forEach(function (language) { var pref = value[kind][language], key = String(pref.source || ""); lines.push((kind === "name" ? "名称" : "简介") + "（" + global.NimdaPeoplePanel.languageLabel(language) + "）：" + (pref.locked ? "固定当前值" : (labels[key.split(":")[0]] || key) + (key.indexOf(":") >= 0 ? " · " + key.split(":").slice(1).join(":").slice(-16) : ""))); }); });
        parent.appendChild(node("pre", "", lines.length ? lines.join("\n") : "自动按来源优先显示"));
      }
      return;
    }
    parent.appendChild(node("pre", "", valueText(value)));
  }
  function showDiff(s, preview, route, clearing) {
    reset(s); var selected = new Set(), rows = array(preview.diff), imagePreview = route === "people-library/image-apply", box = node("div", "person-library-diff" + (imagePreview ? " person-library-image-preview" : ""));
    if (imagePreview) { box.appendChild(node("h3", "", "确认人物图片")); global.NimdaPeopleImages.evidence(box, preview.image_source); }
    var automatic = node("details", "library-person-auto-fields"), automaticTitle = node("summary"), automaticRows = node("div");
    automatic.open = false; automatic.appendChild(automaticTitle); automatic.appendChild(automaticRows);
    if (clearing) box.appendChild(node("p", "library-meta", "只清空勾选的人物资料。保留 ID、姓名、来源身份及作品 / CV 关联；不删除任何作品或图片文件。"));
    array(preview.warnings).forEach(function (warning) { box.appendChild(node("p", "library-meta", typeof warning === "string" ? warning : warning.message)); });
    var confirm = button(clearing ? "确认清空所选资料" : "确认采用所选资料", function () { run(s, async function (seq) { var payload = { person_id: s.id, preview_token: preview.preview_token, selected_fields: Array.from(selected) }; await request(route, payload); if (!alive(s, seq)) return; s.changed = true; notice(clearing ? "人物资料已清空，其他库未修改" : "人物资料已保存"); await reload(s, seq); }, true); });
    function update() {
      var saveVersions = route === "people-library/source-apply" && preview.profile_versions_changed === true;
      confirm.disabled = !selected.size && !saveVersions; confirm.dataset.locked = String(confirm.disabled);
      confirm.textContent = !selected.size && saveVersions ? "保存来源版本" : clearing ? "确认清空所选资料" : imagePreview ? "确认采用此图片" : "确认采用所选资料";
    }
    rows.forEach(function (row) { var line = node("section", "person-library-diff-row"), toggle = node("input"); toggle.type = "checkbox"; toggle.disabled = row.selectable === false; toggle.dataset.locked = String(toggle.disabled); toggle.checked = !clearing && row.selected === true && !toggle.disabled; if (toggle.checked) selected.add(row.field); toggle.addEventListener("change", function () { if (toggle.checked) selected.add(row.field); else selected.delete(row.field); update(); }); field(line, row.label || row.field, toggle); var values = node("div", "person-library-diff-values"); [row.before, global.NimdaPeopleSelection.proposedValue(row)].forEach(function (value, index) { var part = node("div"); part.appendChild(node("strong", "", index ? clearing ? "清空后" : "采用后" : "当前")); diffValue(part, row, value, index); values.appendChild(part); }); line.appendChild(values); if (row.reason) line.appendChild(node("p", "library-meta", row.reason));
      var automaticField = route === "people-library/source-apply" && !global.NimdaPeopleSelection.fieldRequiresConfirmation(row);
      (automaticField ? automaticRows : box).appendChild(line);
    });
    if (automaticRows.children.length) { automaticTitle.textContent = "自动补齐／保持原值字段（" + automaticRows.children.length + "）"; box.appendChild(automatic); }
    box.appendChild(confirm); box.appendChild(button("取消预览", function () { profile(s); })); s.body.appendChild(box); update();
  }
  function sources(s) {
    var workRole = s.detail.person.type === "character" && array(s.detail.person.role_origins).length;
    if (workRole) {
      reset(s); s.body.appendChild(node("h3", "", "角色来源"));
      s.body.appendChild(node("p", "library-meta", "此角色来自作品的演职员表。打开关联作品刷新 TMDB／维基百科资料后，可通过“建立／更新角色档案”预览同步；演员照片不会作为角色图片。"));
      var seen = new Set(); array(s.detail.relations).forEach(function (row) {
        var key = JSON.stringify(row.ref); if (!row.ref || seen.has(key)) return; seen.add(key);
        s.body.appendChild(button("打开作品：" + row.name, function () { close(); if (global.NimdaCatalogLibrary) global.NimdaCatalogLibrary.openWork(row.ref); }));
      });
      if (!array(s.detail.sources).some(function (source) { return source.provider === "bangumi"; })) { s.body.appendChild(button("返回资料", function () { profile(s); })); return; }
    }
    if (!workRole) reset(s); var form = node("form", "person-library-source-form"), provider = node("select"), external = node("input");
    var entityCategory = global.NimdaPeopleTaxonomy.project(s.detail.person).category;
    var providerChoices = s.detail.person.type === "character" || entityCategory === "organization" || entityCategory === "object" ? ["bangumi"] : ["bangumi", "tmdb", "wikipedia"];
    providerChoices.forEach(function (key) { var option = node("option", "", labels[key]); option.value = key; provider.appendChild(option); });
    var current = array(s.detail.sources).find(function (row) { return providerChoices.indexOf(row.provider) >= 0; }); provider.value = current ? current.provider : "bangumi";
    function choose() { var bound = array(s.detail.sources).filter(function (row) { return row.provider === provider.value; }); external.value = bound.length === 1 ? bound[0].external_id : ""; external.placeholder = provider.value === "wikipedia" ? "人物条目标题、页面 ID 或链接" : "人物条目数字 ID"; }
    provider.addEventListener("change", choose); choose(); field(form, "来源", provider); field(form, "人物条目", external);
    var submit = node("button", "library-button", "获取并预览"); submit.type = "submit"; form.appendChild(submit); form.appendChild(button("返回资料", function () { profile(s); })); s.body.appendChild(form);
    form.addEventListener("submit", function (event) { event.preventDefault(); run(s, async function (seq) {
      if (providerChoices.indexOf(provider.value) < 0) return;
      var payload = Object.assign(base(s), { provider: provider.value }); if (external.value.trim()) payload.external_id = external.value.trim();
      var preview = await request("people-library/source-preview", payload); if (alive(s, seq)) showDiff(s, preview, "people-library/source-apply", false);
    }); });
  }
  function open(options) {
    if (active && active.saving) return; close(); options = options || {}; if (!options.personId) return;
    var dialog = node("dialog", "library-person-dialog person-library-dialog"), header = node("header", "person-library-dialog-header"), heading = node("h2", "", "人物资料"), body = node("div", "person-library-dialog-body"); heading.id = "public-person-heading-" + (++nextId); dialog.setAttribute("aria-labelledby", heading.id);
    var s = { dialog: dialog, body: body, heading: heading, id: options.personId, options: options, preference: options.preference || "chinese", trigger: options.trigger || document.activeElement, serial: 0, busy: false, saving: false, changed: false }; active = s;
    s.close = button("关闭", function () { close(); }); header.appendChild(heading); header.appendChild(s.close); dialog.appendChild(header); body.appendChild(node("p", "library-meta", "正在读取本地人物资料…")); dialog.appendChild(body);
    dialog.addEventListener("cancel", function (event) { event.preventDefault(); close(); }); dialog.addEventListener("close", function () { if (active === s) close(); }); document.body.appendChild(dialog); if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", ""); s.close.focus(); run(s, function (seq) { return reload(s, seq); });
  }
  global.NimdaPersonProfile = { open: open, close: close, request: request, safeSource: safeSource };
})(window);
