(function (global) {
  "use strict";
  var arrays = function (value) { return Array.isArray(value) ? value : []; };
  var text = function (value) { return String(value == null ? "" : value); };
  var copy = function (value) { return JSON.parse(JSON.stringify(value)); };
  var labels = { bangumi: "Bangumi", tmdb: "TMDB", wikipedia: "中文维基百科", douban: "豆瓣" };
  var fieldLabels = { name: "名称", name_cn: "中文名", aliases: "别名", summary: "简介", career: "职业", gender: "性别", birth_date: "出生日期", infobox: "详细资料", avatar: "图片", taxonomy: "分类", kind: "细分类别" };
  var textFields = ["name", "name_cn", "summary"], listFields = ["aliases", "career"], jsonFields = ["infobox", "birth_date"];
  function node(tag, className, value) { var element = document.createElement(tag); if (className) element.className = className; if (value != null) element.textContent = text(value); return element; }
  function clear(parent) { while (parent.firstChild) parent.removeChild(parent.firstChild); }
  function add(parent) { Array.prototype.slice.call(arguments, 1).forEach(function (child) { if (child) parent.appendChild(child); }); return parent; }
  function button(label, handler) { var element = node("button", "library-button", label); element.type = "button"; element.addEventListener("click", function () { if (!element.disabled) handler(); }); return element; }
  function name(entity, preference) { return global.NimdaPeoplePanel ? global.NimdaPeoplePanel.entityDisplay(entity || {}, preference).text : text(entity && (entity.name_cn || entity.name)); }
  function localizedType(field) {
    if (!/^localized\.(name|summary|aliases)\.(zh-Hans|zh-Hant|ko|ko-Latn|en|ja|und-Latn|und)$/.test(text(field.field))) return "";
    var expected = { name: "text", summary: "textarea", aliases: "list" }[field.field.split(".")[1]];
    return !field.edit_type || field.edit_type === expected ? expected : "";
  }
  function sourceFieldType(field) {
    var localized = localizedType(field);
    if (localized) return localized;
    // Source infobox entries are independent, signed fields. Their key is an
    // opaque server identifier; never turn it into an object/property path.
    return text(field.field).indexOf("infobox.") === 0 && ["text", "textarea", "list", "json"].indexOf(field.edit_type) >= 0 ? field.edit_type : "";
  }
  function searchNames(entity) {
    var values = [entity.name, entity.name_cn].concat(arrays(entity.aliases));
    if (global.NimdaPeoplePanel) ["name", "aliases"].forEach(function (kind) { global.NimdaPeoplePanel.localizedEntries(entity, kind, "chinese").forEach(function (row) { values = values.concat(row.value); }); });
    return values.join("\n").toLocaleLowerCase();
  }
  function pretty(value) { return value == null || value === "" ? "（空）" : typeof value === "object" ? JSON.stringify(value, null, 2) : text(value); }
  function typeLabel(entity) { return global.NimdaPeopleTaxonomy ? global.NimdaPeopleTaxonomy.label(entity || {}) : entity && (entity.taxonomy_label || entity.classification_label) || (entity && entity.type === "character" ? "角色" : "资料卡"); }
  function targetFor(entry) {
    if (entry.action === "create") return entry.creation && entry.creation.allowed === true && !entry.creation.blocked ? entry.creation : null;
    if (entry.action !== "reuse") return null;
    return arrays(entry.targets).find(function (target) { return text(target.id) === text(entry.target_id) && !target.blocked; }) || null;
  }
  function keys(entry, target) {
    var allowed = arrays(target && target.diff).filter(function (field) { return field.selectable !== false; }).map(function (field) { return field.field; });
    return arrays(entry.selected_fields).filter(function (field, index, all) { return allowed.indexOf(field) >= 0 && all.indexOf(field) === index; });
  }
  function creationFields(entry) {
    var creation = entry.creation || {}, recommended = arrays(creation.selected_fields);
    return arrays(creation.diff).filter(function (field) {
      return field.selectable !== false && !field.must_override && (field.required || recommended.indexOf(field.field) >= 0);
    }).map(function (field) { return field.field; });
  }
  function reuseFields(target) {
    var recommended = arrays(target && target.selected_fields);
    return arrays(target && target.diff).filter(function (field) {
      return field.selectable !== false && !field.must_override && (recommended.indexOf(field.field) >= 0 || field.default_selected === true);
    }).map(function (field) { return field.field; });
  }
  function meaningful(value) {
    if (Array.isArray(value)) return value.some(meaningful);
    if (value && typeof value === "object") return Object.keys(value).some(function (key) { return meaningful(value[key]); });
    return value != null && (typeof value !== "string" || !!value.trim());
  }
  function mergeAction(field) {
    if (field.merge_action) return field.merge_action;
    var before = meaningful(field.before), after = meaningful(field.after);
    return !before ? after ? "fill" : "empty" : !after ? "keep" : field.changed === false || JSON.stringify(field.before) === JSON.stringify(field.after) ? "same" : "conflict";
  }
  function conflict(field) {
    return field.selectable !== false && (field.requires_confirmation === true || field.requires_confirmation == null && mergeAction(field) === "conflict");
  }
  function proposedValue(field) {
    return ["fill", "merge"].indexOf(mergeAction(field)) >= 0 && Object.prototype.hasOwnProperty.call(field, "merged_value") ? field.merged_value : field.after;
  }
  function fieldLabel(target, key) {
    var field = arrays(target && target.diff).find(function (row) { return row.field === key; });
    return field && field.label || fieldLabels[key] || key;
  }
  function entryLabel(entry) { return name(entry.candidate, "chinese") || text(entry.external_id); }
  function entryIssue(entry) {
    if (entry.action !== "create" && entry.action !== "reuse") return "";
    if (entry._loading) return "仍在读取所选资料卡的差异，请完成后再确认。";
    if (entry._targetError) return entry._targetError;
    var target = targetFor(entry), errors = Object.keys(entry._fieldErrors || {});
    if (errors.length) return "手填资料格式有误（" + fieldLabel(target, errors[0]) + "）：" + entry._fieldErrors[errors[0]];
    if (!target) return "请选择有效的已有资料卡或新增方式。";
    var selected = keys(entry, target), diff = arrays(target.diff);
    var manual = diff.find(function (field) { return field.must_override && selected.indexOf(field.field) >= 0 && !Object.prototype.hasOwnProperty.call(entry.overrides || {}, field.field); });
    if (manual) return "“" + fieldLabel(target, manual.field) + "”没有可直接采用的来源值，请手动填写后确认，或取消勾选以保留原值。";
    if (entry.action === "create") {
      var required = arrays(target.required_fields).concat(diff.filter(function (field) { return field.required; }).map(function (field) { return field.field; }));
      var missing = (required.length ? required : ["name"]).find(function (field) { return selected.indexOf(field) < 0; });
      if (missing) return "新增资料卡需要确认名称等必填资料：" + fieldLabel(target, missing) + "。";
    }
    return "";
  }
  function missingDependencies(entry, preview) {
    var missing = [];
    arrays(entry.relations).forEach(function (relation) {
      if (arrays(entry.selected_relations).indexOf(text(relation.id)) < 0 && relation.work_relation_status !== "missing") return;
      arrays(relation.required_entity_keys).forEach(function (key) {
        var dependency = arrays(preview && preview.entries).find(function (row) { return text(row.external_id) === text(key); });
        var title = dependency ? entryLabel(dependency) : text(key);
        if ((!dependency || !(dependency.identity_owner_id || targetFor(dependency))) && missing.indexOf(title) < 0) missing.push(title);
      });
    });
    return missing;
  }
  function needsConfirmation(entry, preview) {
    if (entry._loading || entry._targetError || Object.keys(entry._fieldErrors || {}).length) return true;
    if (entry.action !== "create" && entry.action !== "reuse") return !entry._userSkipped;
    var target = targetFor(entry);
    if (!target || entry.action === "reuse" && arrays(target.diff).some(conflict)) return true;
    if (arrays(entry.relations).some(function (relation) { return relation.requires_confirmation === true || relation.work_relation_status === "conflict"; })) return true;
    return !!entryIssue(entry) || missingDependencies(entry, preview).length > 0;
  }
  function requiresConfirmation(preview) {
    if (!preview) return false;
    if (preview.mode === "people-import" && arrays(preview.entries).some(function (entry) { return needsConfirmation(entry, preview); })) return true;
    return requiresConfirmation(preview.people_preview) || arrays(preview.sources).some(requiresConfirmation);
  }
  function creationRelations(entry, preview) {
    var entries = new Map(arrays(preview.entries).map(function (row) { return [text(row.external_id), row]; }));
    return arrays(entry.relations).filter(function (relation) {
      return relation.selectable !== false && relation.selected === true && arrays(relation.required_entity_keys).every(function (key) {
        var dependent = entries.get(text(key));
        return !!dependent && (dependent.identity_owner_id || targetFor(dependent));
      });
    }).map(function (relation) { return text(relation.id); });
  }
  function collect(preview) {
    var selectedEntries = arrays(preview.entries).reduce(function (result, entry) {
      if (entry.action !== "create" && entry.action !== "reuse") return result;
      var issue = entryIssue(entry); if (issue) throw new Error(entryLabel(entry) + "：" + issue);
      var target = targetFor(entry), fields = keys(entry, target);
      var selected = { external_id: text(entry.external_id), action: entry.action, fields: fields, overrides: {}, relations: [] };
      if (entry.action === "reuse") selected.target_id = text(target.id);
      var relations = arrays(entry.relations).filter(function (relation) { return relation.selectable !== false; }).map(function (relation) { return text(relation.id); });
      selected.relations = arrays(entry.selected_relations).map(text).filter(function (id, index, all) { return relations.indexOf(id) >= 0 && all.indexOf(id) === index; });
      fields.forEach(function (field) { if (Object.prototype.hasOwnProperty.call(entry.overrides || {}, field)) selected.overrides[field] = copy(entry.overrides[field]); });
      result.push(selected); return result;
    }, []);
    var confirmed = new Set(selectedEntries.map(function (entry) { return entry.external_id; }));
    var entries = new Map(arrays(preview.entries).map(function (entry) { return [text(entry.external_id), entry]; }));
    selectedEntries.forEach(function (selection) {
      arrays(entries.get(selection.external_id).relations).filter(function (relation) { return selection.relations.indexOf(text(relation.id)) >= 0; }).forEach(function (relation) {
        arrays(relation.required_entity_keys).forEach(function (identity) {
          var dependency = entries.get(text(identity));
          if (!confirmed.has(text(identity)) && !(dependency && dependency.identity_owner_id)) throw new Error(entryLabel(entries.get(selection.external_id)) + "：请先确认关联所需的资料卡：" + (dependency ? entryLabel(dependency) : text(identity)));
        });
      });
    });
    return selectedEntries;
  }
  function state(preview) {
    var entries = arrays(preview.entries), selected = entries.filter(function (entry) { return entry.action === "create" || entry.action === "reuse"; });
    var invalid = false; try { collect(preview); } catch (_) { invalid = true; }
    return { chosen: selected.length, pending: selected.some(function (entry) { return !!entry._loading; }), invalid: invalid };
  }
  async function readTarget(preview, entry, identity, options) {
    var payload = { snapshot_id: preview.snapshot_id, preview_token: preview.selection_token || preview.preview_token, external_id: entry.external_id, target_id: identity };
    var result;
    if (options.requestTarget) result = await options.requestTarget(payload);
    else {
      var response = await global.NimdaCommon.fetchJson("/api/catalog-library/people/selection-target", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), operationTitle: "核对所选资料卡与来源差异" });
      result = response && response.data !== undefined ? response.data : response;
      if (response && response.res && !response.res.ok) throw new Error(result && result.error || "资料卡差异读取失败");
    }
    if (!result || result.ok === false || !result.target || text(result.target.id) !== text(identity)) throw new Error(result && (result.error || result.message) || "返回的资料卡与所选目标不一致");
    return result.target;
  }
  function render(parent, preview, preference, options) {
    options = options || {};
    var panel = node("section", "library-people-preview library-people-selection"), provider = labels[preview.provider] || "来源";
    if (preview.source_role) provider += " · " + (preview.source_role === "primary" ? "主来源" : "辅助来源");
    panel.setAttribute("aria-label", provider + " · 人物与组织确认"); panel.appendChild(node("h3", "library-section-title", provider + " · 人物与组织确认"));
    panel.appendChild(node("p", "library-meta", "别名合并去重，空字段自动补齐，详细资料按项合并；简繁中文分别保留，仅冲突项需要核对。统一保存前不会修改资料。"));
    var warningCount = new Set(arrays(preview.warnings).map(function (warning) { return typeof warning === "string" ? warning : warning && warning.message; }).filter(Boolean)).size;
    if (warningCount) panel.appendChild(node("p", "library-meta", "读取提示（" + warningCount + "）· 具体条目与原因见处理详情。"));
    if (global.NimdaPeopleRetry) global.NimdaPeopleRetry.render(panel, preview, options);
    var status = node("p", "library-meta"); status.setAttribute("aria-live", "polite"); panel.appendChild(status);
    var manualGroup = node("div", "library-people-manual-group"), manualTitle = node("h4", "library-section-title"), manualCards = node("div", "library-people-group-cards");
    var autoGroup = node("details", "library-people-auto-group"), autoTitle = node("summary"), autoCards = node("div", "library-people-group-cards"), cards = [];
    add(manualGroup, manualTitle, manualCards); add(autoGroup, autoTitle, autoCards); add(panel, manualGroup, autoGroup);
    autoGroup.open = preview._autoGroupOpen === true;
    autoGroup.addEventListener("toggle", function () { preview._autoGroupOpen = autoGroup.open; });
    function groupCards() {
      var manualCount = 0, active = document.activeElement, restoreFocus = false;
      var selectionStart = active && active.selectionStart, selectionEnd = active && active.selectionEnd;
      cards.forEach(function (item) {
        var manual = needsConfirmation(item.entry, preview), destination = manual ? manualCards : autoCards;
        if (manual) manualCount++;
        if (item.card.parentNode !== destination) {
          if (manual && item.entry._detailsOpen == null) item.card.open = true;
          var ancestor = active; while (ancestor && ancestor !== item.card) ancestor = ancestor.parentNode;
          if (ancestor) { restoreFocus = true; if (!manual) { autoGroup.open = true; preview._autoGroupOpen = true; } }
          if (item.card.parentNode) item.card.parentNode.removeChild(item.card); destination.appendChild(item.card);
        }
      });
      cards.forEach(function (item) {
        var target = targetFor(item.entry), conflicts = arrays(target && target.diff).filter(conflict).length;
        var issue = entryIssue(item.entry), dependencies = missingDependencies(item.entry, preview);
        item.state.textContent = issue || (needsConfirmation(item.entry, preview) ?
          !target ? "待选择已有资料卡或新增" : dependencies.length ? "待确认关联资料卡：" + dependencies.join("、") :
          conflicts ? conflicts + " 项差异待确认" : "作品关联待核对" : item.entry.action === "skip" ? "已跳过" : "自动补齐／保持原值");
      });
      manualTitle.textContent = "需要确认（" + manualCount + "）"; manualGroup.hidden = !manualCount;
      autoTitle.textContent = "自动补齐／保持原值（" + (cards.length - manualCount) + "）"; autoGroup.hidden = cards.length === manualCount;
      if (restoreFocus && active && typeof active.focus === "function") {
        active.focus({ preventScroll: true });
        if (typeof selectionStart === "number" && typeof active.setSelectionRange === "function") active.setSelectionRange(selectionStart, selectionEnd);
      }
    }
    function changed() {
      groupCards();
      var value = state(preview), issue = "", entries = arrays(preview.entries);
      if (value.invalid && !value.pending) { try { collect(preview); } catch (error) { issue = error.message; } }
      var creating = entries.filter(function (entry) { return entry.action === "create"; }).length;
      var reused = entries.filter(function (entry) { return entry.action === "reuse"; });
      var updates = reused.reduce(function (count, entry) { return count + keys(entry, targetFor(entry)).length; }, 0);
      var restorations = reused.reduce(function (count, entry) { return count + arrays(entry.relations).filter(function (relation) { return relation.restore_missing === true && arrays(entry.selected_relations).indexOf(text(relation.id)) >= 0; }).length; }, 0);
      status.textContent = "将新增 " + creating + " 项 · 复用 " + reused.length + " 项 · 已选更新 " + updates + " 个字段" +
        (restorations ? " · 待补回 " + restorations + " 项作品关联" : "") +
        (value.chosen < entries.length ? " · 跳过／待核对 " + (entries.length - value.chosen) + " 项" : "") +
        (value.pending ? " · 正在核对资料卡" : issue ? " · " + issue : "");
      if (options.onChange) options.onChange(value);
    }
    arrays(preview.entries).forEach(function (entry) {
      if (entry._loading) { entry._loading = false; entry._targetError = "页面已重新显示，请重新选择资料卡以核对差异。"; }
      if (entry.action !== "create" && entry.action !== "reuse") entry.action = "skip";
      if (!Array.isArray(entry.targets)) entry.targets = [];
      if (!Array.isArray(entry.selected_fields)) entry.selected_fields = entry.action === "create" ? creationFields(entry) : entry.action === "reuse" ? reuseFields(targetFor(entry)) : [];
      if (!Array.isArray(entry.selected_relations)) entry.selected_relations = entry.action === "create" || entry.action === "reuse" ? creationRelations(entry, preview) : [];
      if (!entry.overrides || typeof entry.overrides !== "object") entry.overrides = {};
      if (!entry._fieldErrors) entry._fieldErrors = {};
      var source = entry.candidate || {}, title = name(source, preference) || entry.external_id;
      var card = node("details", "library-person-selection"), header = node("summary", "library-person-selection-head"), identity = node("span", "library-person-selection-identity"), controls = node("div", "library-person-selection-controls");
      var body = node("div", "library-person-selection-body"), metadata = node("div", "library-person-selection-metadata"), cardState = node("span", "library-meta library-person-selection-state");
      card.open = entry._detailsOpen == null ? needsConfirmation(entry, preview) : entry._detailsOpen === true;
      card.addEventListener("toggle", function () { entry._detailsOpen = card.open; });
      var sourceName = node("strong", "", title); sourceName.title = global.NimdaPeoplePanel ? global.NimdaPeoplePanel.entityDisplay(source, preference).tooltip : title;
      add(identity, sourceName, node("span", "library-meta", typeLabel(source)));
      var roles = arrays(source.roles).concat(arrays(source.character_names).map(function (value) { return "饰演 " + value; }));
      if (roles.length) metadata.appendChild(node("p", "library-meta", roles.join(" · ")));
      if (entry.identity_owner_id) metadata.appendChild(node("p", "library-meta", "此来源已有明确绑定，可复用原资料卡。"));
      else if (entry.match_status === "role_evidence" && entry.default_reason) metadata.appendChild(node("p", "library-meta", entry.default_reason));
      else if (entry.targets.some(function (target) { return target.matched || target.exact; })) metadata.appendChild(node("p", "library-meta", "发现已有资料候选，请核对身份。"));
      else if (entry.default_reason) metadata.appendChild(node("p", "library-meta", entry.default_reason));
      var sourceUrl = text(entry.source_url || source.source_url);
      if (/^https:\/\/(?:bangumi\.tv|bgm\.tv|www\.themoviedb\.org|zh\.wikipedia\.org|(?:www|movie)\.douban\.com)\//.test(sourceUrl)) {
        var link = node("a", "library-source-link", "查看来源"); link.href = sourceUrl; link.target = "_blank"; link.rel = "noopener noreferrer"; metadata.appendChild(link);
      }
      var query = node("input"), select = node("select"), fields = node("div", "library-person-selection-fields"), relationBox = node("div", "library-person-selection-relations"), notice = node("p", "library-meta"), targetSerial = 0;
      var details = node("div", "library-person-selection-details");
      add(details, fields, relationBox);
      query.type = "search"; query.placeholder = "在全库查找已有资料卡"; query.setAttribute("aria-label", "查找已有资料卡：" + title); query.disabled = select.disabled = !!options.busy;
      select.setAttribute("aria-label", "资料卡处理方式：" + title);
      function connected() { return panel.isConnected !== false && (!options.current || options.current()); }
      function selectionKey() { return entry.action === "create" ? "__create__" : entry.action === "reuse" ? text(entry.target_id) : ""; }
      function optionsList() {
        clear(select);
        var option = node("option", "", "跳过，不修改"); option.value = ""; select.appendChild(option);
        if (entry.creation) { option = node("option", "", "新增资料卡" + (entry.creation.allowed !== true || entry.creation.blocked ? "（不可用）" : "")); option.value = "__create__"; option.disabled = entry.creation.allowed !== true || !!entry.creation.blocked; select.appendChild(option); }
        var needle = query.value.trim().toLocaleLowerCase(), seen = new Set(), candidates = entry.targets.slice();
        if (needle) candidates = candidates.concat(arrays(preview.targets).filter(function (target) { return (!entry.entity_type || target.type === entry.entity_type) && searchNames(target).indexOf(needle) >= 0; }).slice(0, 50));
        candidates.forEach(function (target) {
          if (!target.id || seen.has(target.id)) return; seen.add(target.id);
          var suffix = target.id === entry.identity_owner_id ? " · 来源已绑定" : entry.match_status === "role_evidence" && target.id === entry.recommended_target_id ? " · 同作品演员对应" : target.matched ? " · 同名候选" : "";
          if (target.planned) suffix += " · 本次" + (labels[target.planned_provider] || "前序来源") + "待新增";
          option = node("option", "", name(target, preference) + " · " + typeLabel(target) + suffix + (target.blocked ? "（不可选）" : ""));
          option.value = text(target.id); option.disabled = !!target.blocked; if (target.reason) option.title = target.reason; select.appendChild(option);
        });
        select.value = selectionKey();
      }
      function setField(field, enabled) { entry.selected_fields = entry.selected_fields.filter(function (key) { return key !== field; }); if (enabled) entry.selected_fields.push(field); }
      function drawFields() {
        clear(fields); clear(relationBox); notice.textContent = entry._targetError || (entry._loading ? "正在读取已有资料卡与来源的字段差异…" : "");
        details.hidden = entry.action === "skip" || !!entry._loading || !!entry._targetError;
        if (entry.action === "skip" || entry._loading || entry._targetError) return;
        var target = targetFor(entry); if (!target) return;
        var creating = entry.action === "create", required = arrays(target.required_fields).concat(arrays(target.diff).filter(function (row) { return row.required; }).map(function (row) { return row.field; }));
        var conflicts = arrays(target.diff).filter(conflict), fills = arrays(target.diff).filter(function (row) { return mergeAction(row) === "fill" && !row.must_override && row.selectable !== false; });
        var restorations = creating ? [] : arrays(entry.relations).filter(function (relation) { return relation.restore_missing === true; });
        notice.textContent = target.reason || (creating ? "保存时新增资料卡；已默认采用来源中有效的资料，可展开调整。" :
          (fills.length ? fills.length + " 项缺失资料可补齐；" : "") + (conflicts.length ? conflicts.length + " 项双边差异待确认：" + conflicts.map(function (row) { return row.label || fieldLabels[row.field] || row.field; }).join("、") + "。未勾选项保持原值。" : "无需确认差异；来源为空时保留当前资料，可展开调整。"));
        if (!creating && target.planned) notice.textContent = "匹配本次" + (labels[target.planned_provider] || "前序来源") + "待新增资料卡，先建立再补充，不会重复建卡。" + notice.textContent;
        if (restorations.length) notice.textContent = "待补回作品关联（" + restorations.length + " 项）：资料卡已存在，但尚未关联本作品。已默认勾选，可取消；确认保存后才生效。" + (conflicts.length ? notice.textContent : "");
        var automatic = node("details", "library-person-auto-fields"), automaticTitle = node("summary"), automaticRows = node("div", "library-person-selection-fields"), automaticCount = 0;
        automatic.open = entry._autoFieldsOpen === true;
        automatic.addEventListener("toggle", function () { entry._autoFieldsOpen = automatic.open; });
        add(automatic, automaticTitle, automaticRows);
        if (creating && !required.length) required = ["name"];
        if (creating) required.forEach(function (key) { if (entry.selected_fields.indexOf(key) < 0 && arrays(target.diff).some(function (field) { return field.field === key && field.selectable !== false; })) entry.selected_fields.push(key); });
        arrays(target.diff).forEach(function (difference) {
          var key = difference.field, label = difference.label || fieldLabels[key] || key, locked = !!options.busy || difference.selectable === false, must = creating && required.indexOf(key) >= 0;
          var row = node("section", "library-person-selection-field"), toggle = node("input"), checkLabel = node("label", "library-person-selection-check"), values = node("div", "library-person-selection-values");
          toggle.type = "checkbox"; toggle.checked = difference.selectable !== false && entry.selected_fields.indexOf(key) >= 0; toggle.disabled = locked || must; toggle.setAttribute("aria-label", "采用" + label + "：" + title);
          add(checkLabel, toggle, node("span", "", label + (must ? "（必填）" : ""))); row.appendChild(checkLabel);
          if (!creating) checkLabel.appendChild(node("span", "library-meta library-person-merge-state", ({ fill: "补齐缺失", merge: "合并去重", keep: "保留当前", same: "内容相同", conflict: "勾选采用新值", empty: "暂无资料" })[mergeAction(difference)]));
          var before = node("div"), after = node("div"), afterText = null; add(before, node("span", "library-meta", creating ? "新资料卡" : "当前资料")); add(after, node("span", "library-meta", "拟采用"));
          function imageValue(parent, value) { var asset = value && value.asset_id; if (typeof asset === "string" && /^[a-f0-9]{64}\.(?:jpg|png|webp|gif)$/.test(asset) && global.NimdaPeoplePanel) parent.appendChild(global.NimdaPeoplePanel.portrait({ name: title, avatar_url: "/api/catalog-library/people/assets/" + asset }, preference)); else parent.appendChild(node("pre", "library-diff-value", pretty(value))); }
          if (key === "avatar") { imageValue(before, difference.before); imageValue(after, difference.after); }
          else { before.appendChild(node("pre", "library-diff-value", pretty(difference.before))); afterText = node("pre", "library-diff-value", pretty(Object.prototype.hasOwnProperty.call(entry.overrides, key) ? entry.overrides[key] : proposedValue(difference))); after.appendChild(afterText); }
          add(values, before, after); row.appendChild(values);
          var classification = null;
          if (key === "taxonomy" && difference.editable !== false && !locked && global.NimdaPeopleTaxonomy) {
            classification = global.NimdaPeopleTaxonomy.createEditor(entry.overrides[key] || difference.after, function (value) {
              entry.overrides[key] = value; if (afterText) afterText.textContent = pretty(value); setField(key, true); toggle.checked = true; changed();
            }, difference.options);
            row.appendChild(classification.element);
          }
          var dynamicType = sourceFieldType(difference), isText = textFields.indexOf(key) >= 0 || dynamicType === "text" || dynamicType === "textarea", isList = listFields.indexOf(key) >= 0 || dynamicType === "list", isJson = jsonFields.indexOf(key) >= 0 || dynamicType === "json";
          var editable = difference.editable !== false && (isText || isList || isJson || key === "gender" || arrays(difference.options).length);
          if (editable && !locked) {
            var hasOverride = Object.prototype.hasOwnProperty.call(entry.overrides, key), edit = button("手动修改", function () { editorBox.hidden = !editorBox.hidden; edit.setAttribute("aria-expanded", String(!editorBox.hidden)); }), editorBox = node("div", "library-person-selection-editor"), input, error = node("p", "library-person-selection-error");
            var choices = arrays(difference.options), original = hasOverride ? entry.overrides[key] : proposedValue(difference);
            if (key === "gender" && !choices.length) choices = ["", "男性", "女性", "非二元", "其他"].map(function (value) { return { value: value, label: value || "未填写" }; });
            if (choices.length) { input = node("select"); choices.forEach(function (choice) { var choiceValue = typeof choice === "object" ? choice.value : choice; var option = node("option", "", typeof choice === "object" ? choice.label : choice); option.value = text(choiceValue); input.appendChild(option); }); input.value = text(original); }
            else { input = node(isText && key !== "summary" && dynamicType !== "textarea" ? "input" : "textarea"); input.value = isList ? arrays(original).join("\n") : isJson ? JSON.stringify(original == null ? key === "birth_date" ? {} : [] : original, null, 2) : text(original); }
            input.setAttribute("aria-label", "手填" + label + "：" + title); input.disabled = !!options.busy;
            input.addEventListener("input", function () {
              try {
                var value = choices.length || isText ? input.value : isList ? input.value.split(/\r?\n/).map(function (part) { return part.trim(); }).filter(Boolean) : JSON.parse(input.value);
                if ((key === "name" || must && dynamicType === "text") && !text(value).trim()) throw new Error("名称不能为空");
                entry.overrides[key] = value; if (afterText) afterText.textContent = pretty(value); delete entry._fieldErrors[key]; error.textContent = ""; input.removeAttribute("aria-invalid"); setField(key, true); toggle.checked = true;
              } catch (failure) { entry._fieldErrors[key] = failure.message || "格式错误"; error.textContent = "请修正：" + entry._fieldErrors[key]; input.setAttribute("aria-invalid", "true"); }
              changed();
            });
            var reset = button("还原来源值", function () { delete entry.overrides[key]; delete entry._fieldErrors[key]; drawFields(); changed(); }); reset.disabled = !!options.busy;
            error.setAttribute("role", "alert"); error.textContent = entry._fieldErrors[key] || ""; editorBox.hidden = !hasOverride && !error.textContent; edit.setAttribute("aria-expanded", String(!editorBox.hidden));
            add(editorBox, input, reset, error); add(row, edit, editorBox);
          }
          toggle.addEventListener("change", function () {
            if (toggle.disabled) return;
            setField(key, toggle.checked);
            // Checking a classification is itself explicit confirmation of the
            // displayed editor values; a second select-change must not be needed.
            if (toggle.checked && classification) { entry.overrides[key] = copy(classification.read()); if (afterText) afterText.textContent = pretty(entry.overrides[key]); }
            if (!toggle.checked) { delete entry.overrides[key]; delete entry._fieldErrors[key]; drawFields(); }
            changed();
          });
          if (difference.must_override) row.appendChild(node("p", "library-meta", classification ? "勾选后采用当前分类；可通过下拉框调整。" : "此项需要手动填写后才能采用。"));
          if (difference.reason) row.appendChild(node("p", "library-meta", difference.reason));
          var manual = conflict(difference) || !!entry._fieldErrors[key] || Object.prototype.hasOwnProperty.call(entry.overrides, key) ||
            difference.must_override && entry.selected_fields.indexOf(key) >= 0;
          if (manual) fields.appendChild(row);
          else { automaticRows.appendChild(row); automaticCount++; }
        });
        if (automaticCount) { automaticTitle.textContent = "自动补齐／保持原值字段（" + automaticCount + "）"; fields.appendChild(automatic); }
        if (arrays(entry.relations).length) relationBox.appendChild(node("h4", "", "作品与人物关系"));
        arrays(entry.relations).forEach(function (relation) {
          var label = node("label", "library-person-selection-check"), check = node("input"); check.type = "checkbox"; check.checked = entry.selected_relations.indexOf(text(relation.id)) >= 0; check.disabled = !!options.busy || relation.selectable === false;
          var caption = relation.label || [relation.role, relation.character_name_cn || relation.character_name].filter(Boolean).join(" · ") || relation.kind || "关联当前作品";
          var stage = global.NimdaPeoplePanel && global.NimdaPeoplePanel.qualifierLabel(relation.qualifier || relation.ref && relation.ref.qualifier);
          if (stage && caption.indexOf(stage) < 0) caption += " · " + stage;
          check.setAttribute("aria-label", "关联" + caption + "：" + title); check.addEventListener("change", function () { if (check.disabled) return; entry.selected_relations = entry.selected_relations.filter(function (value) { return value !== text(relation.id); }); if (check.checked) entry.selected_relations.push(text(relation.id)); changed(); });
          add(label, check, node("span", "", caption)); relationBox.appendChild(label);
          if (relation.work_relation_status) label.appendChild(node("span", "library-meta library-person-relation-state", relation.restore_missing ? "待补回作品关联" : ({ existing: "本作品已有关联", missing: "本作品尚未关联", conflict: "关联冲突，请核对" })[relation.work_relation_status]));
          if (relation.reason) relationBox.appendChild(node("p", "library-meta", relation.reason));
          var dependencies = arrays(relation.required_entity_keys).map(function (key) { var dependent = arrays(preview.entries).find(function (item) { return text(item.external_id) === text(key); }); return dependent ? name(dependent.candidate, preference) : key; });
          if (dependencies.length) relationBox.appendChild(node("p", "library-meta", "关联资料卡：" + dependencies.join("、")));
        });
      }
      select.addEventListener("change", async function () {
        if (select.disabled) return;
        var selected = select.value, sequence = ++targetSerial;
        entry.action = selected === "__create__" ? "create" : selected ? "reuse" : "skip"; entry.target_id = entry.action === "reuse" ? selected : "";
        entry._userSkipped = !selected;
        entry.selected_fields = []; entry.selected_relations = []; entry.overrides = {}; entry._fieldErrors = {}; entry._targetError = ""; entry._loading = false;
        delete entry._autoFieldsOpen;
        if (entry.action === "create") { entry.selected_fields = creationFields(entry); entry.selected_relations = creationRelations(entry, preview); }
        var local = targetFor(entry);
        if (entry.action === "reuse" && local) entry.selected_fields = reuseFields(local);
        if (entry.action === "reuse" && (!local || !Array.isArray(local.diff))) {
          entry._loading = true; drawFields(); groupCards(); changed();
          try { var target = await readTarget(preview, entry, selected, options); if (!connected() || sequence !== targetSerial || entry.target_id !== selected) return; entry.targets = entry.targets.filter(function (row) { return text(row.id) !== selected; }); entry.targets.push(target); if (target.blocked) entry._targetError = target.reason || "所选资料卡不能关联此来源"; else entry.selected_fields = reuseFields(target); }
          catch (error) { if (connected() && sequence === targetSerial) entry._targetError = error.message || "差异读取失败"; }
          finally { if (connected() && sequence === targetSerial) { entry._loading = false; optionsList(); drawFields(); groupCards(); changed(); } }
        } else { drawFields(); groupCards(); changed(); }
      });
      query.addEventListener("input", function () { optionsList(); });
      add(controls, select, query); add(header, identity, cardState); add(body, metadata, controls, notice, details); add(card, header, body); cards.push({ entry: entry, card: card, state: cardState }); optionsList(); drawFields();
    });
    if (!arrays(preview.entries).length) panel.appendChild(node("p", "library-empty", "此来源没有可确认的人物或组织资料。"));
    groupCards(); parent.appendChild(panel); changed(); return panel;
  }
  global.NimdaPeopleSelection = { render: render, collect: collect, state: state, requiresConfirmation: requiresConfirmation, fieldRequiresConfirmation: conflict, proposedValue: proposedValue };
})(window);
