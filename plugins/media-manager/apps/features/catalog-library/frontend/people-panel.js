(function (global) {
  "use strict";
  var array = function (value) { return Array.isArray(value) ? value : []; };
  var text = function (value) { return String(value == null ? "" : value); };
  var fieldLabel = function (key) { return ({ name: "名称", name_cn: "中文名", summary: "简介", aliases: "别名", infobox: "详细资料", career: "职业", kind: "类型", gender: "性别", birth_date: "生日", voice_actors: "声优关联", avatar: "图片" })[key] || key; };
  var currentDialog = null, nextId = 0;
  function node(tag, cls, value) {
    var element = document.createElement(tag); if (cls) element.className = cls;
    if (value != null) element.textContent = text(value); return element;
  }
  function add(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function clear(parent) { while (parent.children.length) parent.children[0].remove(); }
  function button(label, handler, disabled, primary) {
    var element = node("button", "library-button" + (primary ? " is-primary" : ""), label);
    element.type = "button"; element.disabled = !!disabled;
    element.addEventListener("click", function () { if (element.disabled || typeof handler !== "function") return; Promise.resolve().then(handler).catch(function () { /* Shared operation center reports errors. */ }); });
    return element;
  }
  function valueText(value) {
    if (value == null) return "";
    if (Array.isArray(value)) return value.map(function (row) { return row && typeof row === "object" ? text(row.v || row.value || row.name) : text(row); }).filter(Boolean).join(" / ");
    return typeof value === "object" ? JSON.stringify(value) : text(value);
  }
  var languageLabels = { "zh-Hans": "简体中文", "zh-Hant": "繁体中文", ko: "韩语", "ko-Latn": "韩语罗马字", en: "英语", ja: "日语", "und-Latn": "拉丁字母（语言未定）", und: "语言未定" };
  var own = function (value, key) { return !!value && Object.prototype.hasOwnProperty.call(value, key); };
  function localizedField(key) { return /^localized\.(name|summary|aliases)\.(zh-Hans|zh-Hant|ko|ko-Latn|en|ja|und-Latn|und)$/.test(text(key)); }
  function fieldValue(entity, key) {
    if (!entity || typeof entity !== "object") return undefined;
    if (!localizedField(key)) return key.indexOf(".") < 0 && own(entity, key) ? entity[key] : undefined;
    var path = key.split("."); return own(entity.localized, path[1]) && own(entity.localized[path[1]], path[2]) ? entity.localized[path[1]][path[2]] : undefined;
  }
  function languageHint(entity, kind) {
    var sources = array(entity.source_refs).concat(entity.source && typeof entity.source === "object" ? [entity.source] : []), fields = entity.field_sources || {}, hint = fields[kind] && fields[kind].language;
    if (own(languageLabels, hint)) return hint;
    var hints = Array.from(new Set(sources.map(function (source) { return source.language; }).filter(function (value) { return own(languageLabels, value); })));
    return hints.length === 1 ? hints[0] : kind === "name" && sources.length && sources.every(function (source) { return source.provider === "bangumi"; }) ? "ja" : "";
  }
  function legacyLanguage(value, hint) {
    var hangul = (value.match(/[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]/g) || []).length;
    var kana = (value.match(/[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]/g) || []).length;
    var han = (value.match(/[\u3400-\u9fff\uf900-\ufaff]/g) || []).length;
    // A quoted native name must not reclassify the surrounding biography.
    var chineseHint = ["zh-Hans", "zh-Hant"].indexOf(hint) >= 0;
    if (han > hangul + kana && (hangul || kana && chineseHint) && ["ja", "ko"].indexOf(hint) < 0) return chineseHint ? hint : "und";
    if (hangul) return "ko";
    if (kana) return "ja";
    if (/[\u3400-\u9fff\uf900-\ufaff]/.test(value)) return ["zh-Hans", "zh-Hant", "ja", "ko"].indexOf(hint) >= 0 ? hint : "und";
    if (/[A-Za-z\u00c0-\u024f]/.test(value)) return ["en", "ko-Latn", "und-Latn"].indexOf(hint) >= 0 ? hint : "und-Latn";
    return "und";
  }
  function localizedValues(entity, kind) {
    entity = entity || {}; var result = {}, incoming = entity.localized && entity.localized[kind];
    Object.keys(languageLabels).forEach(function (language) {
      if (!own(incoming, language)) return;
      if (kind === "aliases" && Array.isArray(incoming[language])) result[language] = incoming[language].filter(function (value) { return typeof value === "string" && value.trim(); });
      else if (kind !== "aliases" && typeof incoming[language] === "string") result[language] = incoming[language].trim();
    });
    function fallback(language, value) { if (!own(result, language)) result[language] = value; }
    function classified(value) { return Object.keys(result).some(function (language) { return result[language] === value; }); }
    if (kind === "name") {
      var chineseHint = languageHint(entity, "name_cn") || "zh-Hans";
      if (text(entity.name).trim() && !classified(text(entity.name).trim())) fallback(legacyLanguage(text(entity.name), entity.name === entity.name_cn ? chineseHint : languageHint(entity, "name")), text(entity.name).trim());
      if (text(entity.name_cn).trim() && !classified(text(entity.name_cn).trim())) fallback(legacyLanguage(text(entity.name_cn), chineseHint), text(entity.name_cn).trim());
    } else if (kind === "summary") {
      if (text(entity.summary).trim() && !classified(text(entity.summary).trim())) fallback(legacyLanguage(text(entity.summary), languageHint(entity, "summary")), text(entity.summary).trim());
    } else if (kind === "aliases") {
      var old = {}, names = localizedValues(entity, "name"); array(entity.aliases).filter(function (value) { return typeof value === "string" && value.trim(); }).forEach(function (value) { if (Object.keys(result).some(function (language) { return array(result[language]).indexOf(value) >= 0; })) return; var known = Object.keys(names).filter(function (language) { return names[language] === value; }), language = known.length === 1 ? known[0] : legacyLanguage(value); var entries = old[language] || (old[language] = []); if (entries.indexOf(value.trim()) < 0) entries.push(value.trim()); });
      Object.keys(old).forEach(function (language) { fallback(language, old[language]); });
    }
    return result;
  }
  function localizedEntries(entity, kind, preference) {
    var values = localizedValues(entity, kind), original = ["ko", "ja", "en", "ko-Latn", "und-Latn", "und"], chinese = ["zh-Hans", "zh-Hant"];
    var names = kind === "name" ? values : localizedValues(entity, "name"), rawName = text(entity && entity.name).trim(), reliable = ["ko", "ja", "en", "zh-Hans", "zh-Hant"];
    var known = reliable.filter(function (language) { return rawName && names[language] === rawName; });
    var language = known.length === 1 ? known[0] : legacyLanguage(rawName, languageHint(entity || {}, "name"));
    original = original.concat(chinese);
    if (reliable.indexOf(language) >= 0 && names[language]) original = [language].concat(original.filter(function (value) { return value !== language; }));
    var order = preference === "chinese" ? chinese.concat(original.filter(function (language) { return chinese.indexOf(language) < 0; })) : original;
    // An unverified Chinese variant can still be the saved Chinese display
    // name. Prefer its existing slot without relabelling it as simplified.
    if (preference === "chinese" && kind === "name" && !chinese.some(function (key) { return !!values[key]; })) {
      var chineseName = text(entity && entity.name_cn).trim();
      var retained = /[\u3400-\u9fff\uf900-\ufaff]/.test(chineseName) && order.find(function (key) { return values[key] === chineseName; });
      if (retained) order = [retained].concat(order.filter(function (key) { return key !== retained; }));
    }
    return order.filter(function (language) { return kind === "aliases" ? array(values[language]).length : !!values[language]; }).map(function (language) { return { language: language, label: languageLabels[language], value: values[language] }; });
  }
  function entityDisplay(entity, preference, fallback) {
    fallback = fallback || "人物资料缺失";
    if (!entity || !entity.localized) return global.NimdaChapterTitles.display(entity, preference, fallback);
    var entries = localizedEntries(entity, "name", preference), alternative = localizedEntries(entity, "name", preference === "chinese" ? "original" : "chinese");
    var primary = entries[0], secondary = alternative[0];
    return { text: primary ? primary.value : fallback, tooltip: secondary ? secondary.value : primary ? primary.value : fallback };
  }
  function summaryDisplay(entity, preference) { var entries = localizedEntries(entity, "summary", preference); return { text: entries.length ? entries[0].value : "", language: entries.length ? entries[0].language : "" }; }
  function qualifierLabel(qualifier) {
    if (!qualifier || qualifier.kind !== "age" || qualifier.explicit !== true) return "";
    return ({ child: "童年", teen: "少年", young: "年少时期", adult: "成年", elder: "老年" })[qualifier.value] || "";
  }
  function entityName(entity, preference) { return entityDisplay(entity, preference).text; }
  function sourceKey(source) {
    source = source || {}; var provider = source.provider || "bangumi", range = source.episode_range || {};
    return JSON.stringify([provider, text(source.binding_id), text(source.subject_id || source.external_id), text(source.scope), source.season_number == null ? "" : source.season_number, range.start == null ? "" : range.start, range.end == null ? "" : range.end]);
  }
  function sourcesForPeople(bindings) {
    var seen = new Set(); return array(bindings).filter(function (source) {
      if (!source || source.enabled === false || ["bangumi", "tmdb", "wikipedia"].indexOf(source.provider || "bangumi") < 0 || !text(source.subject_id || source.external_id)) return false;
      var key = sourceKey(source); if (seen.has(key)) return false; seen.add(key); return true;
    });
  }
  function sourceLabel(source) {
    var label = ({ tmdb: "TMDB", wikipedia: "中文维基百科" })[source.provider] || "Bangumi";
    label += " · " + (source.name_cn || source.name || source.title || "条目 " + text(source.subject_id || source.external_id));
    if (source.provider === "tmdb" && source.season_number != null) label += " · 第 " + source.season_number + " 季";
    if (source.episode_range) label += " · 第 " + source.episode_range.start + "–" + source.episode_range.end + " 集";
    else if (source.scope && source.provider !== "tmdb") label += " · " + source.scope;
    return label;
  }
  function uniqueEntities(entities) {
    var seen = new Set(); return array(entities).filter(function (entity) {
      if (!entity || typeof entity !== "object") return false;
      var key = entity.id || entityName(entity); if (seen.has(key)) return false; seen.add(key); return true;
    });
  }
  function portrait(entity, preference) {
    var box = node("div", "library-person-portrait"), fallback = node("span", "library-person-photo-fallback", "暂无图片");
    var url = text(entity.avatar_url);
    box.appendChild(fallback);
    // Only opaque filenames served by our local people asset route are allowed.
    if (/^\/api\/catalog-library\/people\/assets\/[a-f0-9]{64}\.(?:jpg|png|webp|gif)$/.test(url)) {
      var image = node("img"); image.src = url; image.alt = entityName(entity, preference); image.loading = "lazy"; image.decoding = "async";
      fallback.hidden = true;
      image.addEventListener("error", function () { image.hidden = true; fallback.hidden = false; });
      box.appendChild(image);
    }
    return box;
  }
  function closeDialog(restoreFocus) {
    if (!currentDialog) return;
    var previous = currentDialog; currentDialog = null;
    if (previous.cleanup) previous.cleanup();
    if (previous.node.open && typeof previous.node.close === "function") previous.node.close();
    previous.node.remove();
    if (restoreFocus && previous.trigger && previous.trigger.isConnected !== false) previous.trigger.focus();
  }
  function entityProfile(entity, preference) {
    entity = entity || {};
    var content = node("div", "library-person-profile"), display = entityDisplay(entity, preference);
    if (entity.type) content.appendChild(node("p", "library-meta", global.NimdaPeopleTaxonomy ? global.NimdaPeopleTaxonomy.label(entity) : entity.taxonomy_label || "未分类"));
    if (entity.missing) content.appendChild(node("p", "library-meta", "本地人物记录缺失，请更新人物信息。"));
    if (entity.localized) {
      var names = node("dl", "library-person-facts library-person-localized-names");
      localizedEntries(entity, "name", preference).forEach(function (row) { add(names, node("dt", "", "名称（" + row.label + "）"), node("dd", "", row.value)); });
      localizedEntries(entity, "aliases", preference).forEach(function (row) { add(names, node("dt", "", "别名（" + row.label + "）"), node("dd", "", row.value.join(" / "))); });
      if (names.children.length) content.appendChild(names);
    } else {
      if (display.tooltip !== display.text) content.appendChild(node("p", "library-meta", display.tooltip));
      if (array(entity.aliases).length) content.appendChild(node("p", "library-meta", "别名：" + entity.aliases.join(" / ")));
    }
    if (valueText(entity.career)) content.appendChild(node("p", "library-meta", "职业：" + valueText(entity.career)));
    if (entity.localized) localizedEntries(entity, "summary", preference).forEach(function (row) { var section = node("section", "library-person-localized-summary"); add(section, node("h3", "library-meta", "简介（" + row.label + "）"), node("p", "library-person-summary", row.value)); content.appendChild(section); });
    else if (entity.summary) content.appendChild(node("p", "library-person-summary", entity.summary));
    var facts = node("dl", "library-person-facts");
    array(entity.infobox).forEach(function (fact) {
      if (fact && fact.key && valueText(fact.value)) add(facts, node("dt", "", fact.key), node("dd", "", valueText(fact.value)));
    });
    if (facts.children.length) content.appendChild(facts);
    if (!content.children.length) content.appendChild(node("p", "library-meta", "暂无更多资料。"));
    return content;
  }
  function focusTargets(parent) {
    var targets = [];
    function visit(element) {
      if (element.hidden) return;
      var tag = text(element.tagName || element.tag).toLowerCase();
      if (!element.disabled && (["button", "input", "select", "textarea"].indexOf(tag) >= 0 || tag === "a" && element.href)) targets.push(element);
      Array.from(element.children || []).forEach(visit);
    }
    visit(parent); return targets;
  }
  function showEntity(entity, trigger, preference) {
    closeDialog(false);
    if (entity && entity.id && !entity.missing && global.NimdaPersonProfile && typeof global.NimdaPersonProfile.open === "function") {
      return global.NimdaPersonProfile.open({ personId: entity.id, preference: preference, trigger: trigger });
    }
    var display = entityDisplay(entity, preference), dialog = node("dialog", "library-person-dialog"), heading = node("h2", "library-person-dialog-name", display.text);
    heading.title = display.tooltip;
    heading.id = "library-person-heading-" + (++nextId);
    dialog.setAttribute("aria-labelledby", heading.id); dialog.setAttribute("aria-modal", "true"); dialog.setAttribute("role", "dialog");
    function closeThisDialog() { if (currentDialog && currentDialog.node === dialog && !currentDialog.saving) closeDialog(true); }
    var close = button("关闭", closeThisDialog), header = node("div", "library-person-dialog-header");
    add(header, portrait(entity, preference), heading, close); add(dialog, header, entityProfile(entity, preference));
    var holder = { node: dialog, trigger: trigger, preference: preference, saving: false }; currentDialog = holder;
    dialog.addEventListener("cancel", function (event) { event.preventDefault(); closeThisDialog(); });
    dialog.addEventListener("close", closeThisDialog);
    dialog.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); if (event.stopPropagation) event.stopPropagation(); closeThisDialog(); }
      if (event.key === "Tab") {
        var targets = focusTargets(dialog), index = targets.indexOf(document.activeElement);
        if (targets.length && (index < 0 || event.shiftKey && index === 0 || !event.shiftKey && index === targets.length - 1)) {
          event.preventDefault(); targets[event.shiftKey ? targets.length - 1 : 0].focus();
        }
      }
    });
    document.body.appendChild(dialog);
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    close.focus();
  }
  function personLink(entity, preference) {
    var display = entityDisplay(entity, preference), link = node("button", "library-person-name", display.text); link.type = "button";
    link.title = display.tooltip; link.setAttribute("aria-haspopup", "dialog");
    link.addEventListener("click", function () { if (link.isConnected !== false) showEntity(entity, link, preference); }); return link;
  }
  function groupPeople(entities, fallback) {
    var groups = new Map();
    array(entities).forEach(function (entity) {
      if (!entity || typeof entity !== "object") return;
      // Responsibilities belong to this work, not to the person's global career.
      var role = text(entity.role || entity.relation).trim() || fallback;
      if (!groups.has(role)) groups.set(role, []); groups.get(role).push(entity);
    });
    return Array.from(groups, function (entry) { return { role: entry[0], people: uniqueEntities(entry[1]) }; });
  }
  function renderEntities(parent, people, preference) {
    var characters = array(people.characters), persons = array(people.persons), cast = array(people.cast);
    if (!characters.length && !persons.length && !cast.length) { parent.appendChild(node("p", "library-empty", "暂无人物信息")); return; }
    if (cast.length) {
      var actorSection = node("section", "library-cast-group library-actor-group"), actorHeading = node("h3", "library-section-title", "演员"), actorsList = node("div", "library-cast-list");
      actorHeading.id = "library-actor-group-" + (++nextId); actorSection.setAttribute("aria-labelledby", actorHeading.id);
      var seenCast = new Set();
      cast.forEach(function (entry) {
        if (!entry || typeof entry !== "object") return;
        var person = entry.person && typeof entry.person === "object" ? entry.person : entry;
        var role = text(entry.character_name).trim(), roleCn = text(entry.character_name_cn).trim();
        var roleNames = { name: role, name_cn: roleCn }, overrides = entry.character_name_overrides;
        // Only the explicit projection contract distinguishes inherited names
        // from work-local edits; older responses must retain their local text.
        if (entry.character && !entry.character.missing && entry.character.localized && Array.isArray(overrides) && overrides.every(function (key) { return key === "character_name" || key === "character_name_cn"; })) {
          var originals = localizedEntries(entry.character, "name", "original"), chineseNames = localizedEntries(entry.character, "name", "chinese").filter(function (row) { return row.language === "zh-Hans" || row.language === "zh-Hant"; });
          roleNames.name = overrides.indexOf("character_name") >= 0 ? role : originals.length ? originals[0].value : "";
          roleNames.name_cn = overrides.indexOf("character_name_cn") >= 0 ? roleCn : chineseNames.length ? chineseNames[0].value : "";
        }
        var stage = qualifierLabel(entry.qualifier);
        var key = JSON.stringify([text(person.id || entityName(person)), text(entry.character && entry.character.id || entry.character_id), role, roleCn, stage]);
        if (seenCast.has(key)) return; seenCast.add(key);
        var row = node("article", "library-cast-row library-actor-row"), info = node("div", "library-cast-character");
        info.appendChild(personLink(person, preference));
        if (roleNames.name || roleNames.name_cn) {
          var roleDisplay = entityDisplay(roleNames, preference, "角色名缺失");
          var linkedCharacter = entry.character && entry.character.id && !entry.character.missing;
          var roleLabel = linkedCharacter ? personLink(entry.character, preference) : node("span", "library-actor-role", "饰演 " + roleDisplay.text);
          if (linkedCharacter) roleLabel.textContent = roleDisplay.text;
          roleLabel.title = roleDisplay.tooltip;
          if (linkedCharacter) { var roleLine = node("span", "library-actor-role", "饰演 "); roleLine.appendChild(roleLabel); info.appendChild(roleLine); }
          else info.appendChild(roleLabel);
          if (stage) info.appendChild(node("span", "library-meta library-portrayal-stage", stage));
        }
        add(row, portrait(person, preference), info); actorsList.appendChild(row);
      });
      add(actorSection, actorHeading, actorsList); parent.appendChild(actorSection);
    }
    if (characters.length) {
      var groups = groupPeople(characters, "其他角色"), roleOrder = ["主角", "配角", "客串"];
      groups.sort(function (a, b) { var left = roleOrder.indexOf(a.role), right = roleOrder.indexOf(b.role); return (left < 0 ? 3 : left) - (right < 0 ? 3 : right); });
      groups.forEach(function (group) {
        var section = node("section", "library-cast-group"), heading = node("h3", "library-section-title", group.role), list = node("div", "library-cast-list");
        heading.id = "library-cast-group-" + (++nextId); section.setAttribute("aria-labelledby", heading.id);
        group.people.forEach(function (character) {
          var row = node("article", "library-cast-row"), characterBox = node("div", "library-cast-character"), actors = node("div", "library-cast-actors");
          actors.appendChild(node("span", "library-meta", "CV："));
          var cast = uniqueEntities(character.actors);
          if (!cast.length) actors.appendChild(node("span", "library-meta", "未关联"));
          cast.forEach(function (person, index) { if (index) actors.appendChild(node("span", "library-person-separator", "、")); actors.appendChild(personLink(person, preference)); });
          add(characterBox, personLink(character, preference), actors); add(row, portrait(character, preference), characterBox); list.appendChild(row);
        }); add(section, heading, list); parent.appendChild(section);
      });
    }
    if (persons.length) {
      parent.appendChild(node("h3", "library-section-title", "制作人员"));
      var staff = node("div", "library-staff-list");
      groupPeople(persons, "其他职员").forEach(function (group) {
        var row = node("div", "library-staff-row"), names = node("div", "library-staff-names");
        names.id = "library-staff-names-" + (++nextId);
        var entries = [];
        group.people.forEach(function (person, index) {
          var entry = node("span", "library-staff-entry"); if (index) entry.appendChild(node("span", "library-person-separator", "、"));
          entry.appendChild(personLink(person, preference)); entry.hidden = index >= 6; entries.push(entry); names.appendChild(entry);
        });
        if (entries.length > 6) {
          var expanded = false, toggle = button("展开全部（" + entries.length + "）", function () {
            expanded = !expanded; entries.forEach(function (entry, index) { entry.hidden = !expanded && index >= 6; });
            toggle.textContent = expanded ? "收起" : "展开全部（" + entries.length + "）"; toggle.setAttribute("aria-expanded", String(expanded));
          });
          toggle.className = "library-person-expand"; toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", names.id); names.appendChild(toggle);
        }
        add(row, node("div", "library-staff-role", group.role + "："), names); staff.appendChild(row);
      }); parent.appendChild(staff);
    }
  }
  function previewEntities(people) {
    people = people || {};
    var entities = new Map();
    function remember(entity) { if (entity && entity.id) entities.set(text(entity.id), entity); }
    array(people.characters).forEach(function (character) { remember(character); array(character && character.actors).forEach(remember); });
    array(people.persons).forEach(remember);
    array(people.cast).forEach(function (entry) { remember(entry && entry.person || entry); remember(entry && entry.character); });
    return entities;
  }
  function previewDisplay(change, entities, preference) {
    // Change summaries may only carry the original name; the projected preview
    // has both language values, including unchanged and manually retained names.
    var entity = entities.get(text(change.id)), names = { name: change.name, name_cn: change.name_cn, localized: change.localized };
    if (!entity) array(change.diff).forEach(function (difference) {
      if (difference.field === "name" || difference.field === "name_cn") names[difference.field] = difference.after;
      else if (localizedField(difference.field) && difference.field.indexOf("localized.name.") === 0) { var language = difference.field.split(".")[2]; names.localized = names.localized || {}; names.localized.name = Object.assign({}, names.localized.name); names.localized.name[language] = difference.after; }
    });
    return entityDisplay(entity || names, preference, text(change.id).trim() || "人物资料缺失");
  }
  function collectSelections(preview) {
    var selections = {}, imported = [];
    function visit(value) {
      if (!value || typeof value !== "object" || value.provider === "douban") return;
      if (value.mode === "people-import" && value.snapshot_id) {
        selections[text(value.snapshot_id)] = global.NimdaPeopleSelection.collect(value);
        imported.push(value);
      }
      if (value.people_preview) visit(value.people_preview);
      array(value.sources).forEach(visit);
    }
    visit(preview);
    imported.forEach(function (source) {
      array(source.entries).filter(function (entry) { return entry.action === "reuse"; }).forEach(function (entry) {
        var target = array(entry.targets).find(function (row) { return text(row.id) === text(entry.target_id); });
        if (!target || !target.planned) return;
        var upstream = imported.find(function (row) { return text(row.snapshot_id) === text(target.planned_snapshot_id); });
        var chosen = upstream && array(upstream.entries).find(function (row) { return text(row.external_id) === text(target.planned_external_id); });
        var identity = chosen && (chosen.action === "create" ? chosen.creation && chosen.creation.id : chosen.action === "reuse" ? chosen.target_id : "");
        if (text(identity) !== text(target.id)) throw new Error("前序来源待新增资料卡已取消或改选：" + entityName(target, "chinese") + "。请恢复前序选择，或重新选择辅助来源的补充目标。");
      });
    });
    return selections;
  }
  function renderPreview(parent, preview, preference, options) {
    preview = preview || {};
    if (preview.mode === "people-import") return global.NimdaPeopleSelection.render(parent, preview, preference, options);
    // Combined source refreshes retain separate signed proposals. Display each
    // proposal's bilingual cast rather than guessing a merged people snapshot.
    if (!preview.people && array(preview.sources).length) {
      var sourcesPanel = node("section", "library-people-preview-sources");
      var issue = node("p", "library-person-selection-error"); issue.setAttribute("role", "status"); issue.hidden = true;
      function changed(value) {
        try { collectSelections(preview); issue.textContent = ""; issue.hidden = true; }
        catch (error) { issue.textContent = error.message; issue.hidden = false; }
        if (options && options.onChange) options.onChange(value);
      }
      array(preview.sources).filter(function (source) { return source.provider !== "douban"; }).forEach(function (source) {
        var sourcePanel = renderPreview(sourcesPanel, source, preference, Object.assign({}, options, { onChange: changed }));
        sourcePanel.children[0].textContent = sourceLabel(source) + (source.source_role ? " · " + (source.source_role === "primary" ? "主来源" : "辅助来源") : "") + " · 人物更新预览";
      });
      sourcesPanel.appendChild(issue); changed();
      parent.appendChild(sourcesPanel); return sourcesPanel;
    }
    var panel = node("section", "library-people-preview"), changes = array(preview.changes), entities = previewEntities(preview.people);
    var title = preview.mode === "birthday_repair" ? "生日资料修复预览" : "人物更新预览";
    panel.setAttribute("aria-label", title);
    panel.appendChild(node("h3", "library-section-title", title));
    if (preview.mode === "birthday_repair" && Number.isInteger(preview.checked_count) && preview.checked_count >= 0) {
      var repairCount = changes.filter(function (change) { return change.action === "update"; }).length;
      panel.appendChild(node("p", "library-meta", "已检查 " + preview.checked_count + " 项本地人物资料，待修复 " + repairCount + " 项。"));
    }
    var warnings = array(preview.warnings).map(function (warning) { return typeof warning === "string" ? warning : warning && warning.message; }).filter(Boolean);
    if (warnings.length) {
      panel.appendChild(node("p", "library-meta library-people-warning-summary", "读取提示（" + new Set(warnings).size + "）· 有效资料仍可保存，具体内容见处理详情。"));
    }
    var changesList = node("ul", "library-people-changes");
    changes.forEach(function (change) {
      var action = ({ create: "新增", add: "新增", update: "更新", unchanged: "保留", keep: "保留" })[change.action] || change.action || "更新";
      var fields = array(change.changed_fields).map(fieldLabel).join("、"), line = node("li");
      var display = previewDisplay(change, entities, preference), summary = node("span", "", action + " · " + display.text + (fields ? " · " + fields : ""));
      summary.title = display.tooltip; line.appendChild(summary);
      if (array(change.preserved_fields).length) line.appendChild(node("span", "library-meta", "（保留手工修改：" + change.preserved_fields.map(fieldLabel).join("、") + "）"));
      array(change.diff).forEach(function (difference) {
        var field = node("details", "library-person-field-diff"); field.appendChild(node("summary", "", difference.label || fieldLabel(difference.field)));
        add(field, node("div", "library-meta", "当前"), node("pre", "library-diff-value", difference.before == null ? "未填写" : typeof difference.before === "object" ? JSON.stringify(difference.before, null, 2) : difference.before),
          node("div", "library-meta", "更新后"), node("pre", "library-diff-value", difference.after == null ? "未填写" : typeof difference.after === "object" ? JSON.stringify(difference.after, null, 2) : difference.after));
        line.appendChild(field);
      });
      changesList.appendChild(line);
    });
    if (changes.length) {
      var differences = node("details", "library-person-differences"); add(differences, node("summary", "", changes.length + " 项人物记录 · 查看差异"), changesList); panel.appendChild(differences);
    }
    if (preview.people) renderEntities(panel, preview.people, preference);
    parent.appendChild(panel); return panel;
  }
  function render(parent, options) {
    closeDialog(false); options = options || {};
    var state = options.state || {}, detail = options.detail || {}, people = detail.people || {}, busy = options.busy || state.loading, preference = detail.chapter_title_preference;
    var bindings = array(options.bindings), sources = sourcesForPeople(bindings), selectedKey = state.sourceKey || "";
    var toolbar = node("div", "library-people-toolbar");
    if (!sources.length) {
      toolbar.appendChild(button("关联作品来源", options.onChooseSources, busy));
    }
    else {
      if (sources.length > 1) {
        var select = node("select"); select.setAttribute("aria-label", "人物来源作品"); select.disabled = busy;
        var blank = node("option", "", "选择已关联来源"); blank.value = ""; select.appendChild(blank);
        sources.forEach(function (source) { var choice = node("option", "", sourceLabel(source)); choice.value = sourceKey(source); select.appendChild(choice); });
        select.value = selectedKey; select.addEventListener("change", function () { options.onSource(select.value); }); toolbar.appendChild(select);
      }
      else toolbar.appendChild(node("span", "library-meta library-people-source", sourceLabel(sources[0])));
      toolbar.appendChild(button(state.loading ? "正在获取人物…" : "更新人物信息", options.onPreview, busy || sources.length > 1 && !sources.some(function (source) { return sourceKey(source) === selectedKey; }), true));
    }
    if (typeof options.onRolesPreview === "function" && array(people.cast).length) toolbar.appendChild(button("建立／更新角色档案", options.onRolesPreview, busy));
    parent.appendChild(toolbar);
    if (typeof options.onWikipediaPreview === "function") {
      var wikiForm = node("details", "library-wikipedia-people-form"), wikiRows = node("div"), wikiActions = node("div", "library-people-toolbar");
      wikiForm.appendChild(node("summary", "", "通过维基人物条目补充"));
      var draftRows = state.wikipediaRows || (state.wikipediaRows = [{ page: "", role: "", character_name: "" }]);
      function drawWikiRows() {
        clear(wikiRows);
        draftRows.forEach(function (draft, position) {
          var line = node("div", "library-wikipedia-people-row");
          [["page", "维基人物链接或条目名"], ["role", "职责（可空）"], ["character_name", "饰演角色（可空）"]].forEach(function (spec) {
            var label = node("label", "library-field"), input = node("input"); input.value = text(draft[spec[0]]); input.disabled = !!busy; input.setAttribute("aria-label", spec[1]);
            input.addEventListener("input", function () { draft[spec[0]] = input.value; }); add(label, node("span", "", spec[1]), input); line.appendChild(label);
          });
          line.appendChild(button("移除", function () { draftRows.splice(position, 1); drawWikiRows(); }, busy || draftRows.length <= 1)); wikiRows.appendChild(line);
        });
      }
      wikiActions.appendChild(button("添加条目", function () { draftRows.push({ page: "", role: "", character_name: "" }); drawWikiRows(); }, busy));
      wikiActions.appendChild(button("预览人物资料", function () { return options.onWikipediaPreview(draftRows.filter(function (row) { return row.page.trim(); }).map(function (row) { return { page: row.page.trim(), role: row.role.trim(), character_name: row.character_name.trim() }; })); }, busy, true));
      drawWikiRows(); add(wikiForm, wikiRows, wikiActions); parent.appendChild(wikiForm);
    }
    if (state.error) toolbar.appendChild(button("重试获取", options.onPreview, busy || (!sources.length || sources.length > 1 && !sources.some(function (source) { return sourceKey(source) === selectedKey; }))));
    if (state.preview) {
      var preview = state.preview, selectionApply = null;
      function selectionDisabled() { var selection = preview.mode === "people-import" ? global.NimdaPeopleSelection.state(preview) : null; return !!busy || !!selection && (!selection.chosen || selection.pending || selection.invalid); }
      var panel = renderPreview(parent, preview, preference, { busy: busy, onRetry: options.onRetry, onCancelRetry: options.onCancelRetry, retryingSnapshotId: options.retryingSnapshotId, onChange: function () { if (selectionApply) selectionApply.disabled = selectionDisabled(); } });
      var actions = node("div", "library-people-toolbar");
      var repairing = preview.mode === "birthday_repair", noRepairs = repairing && !array(preview.changes).some(function (change) { return change.action === "update"; });
      if (noRepairs) panel.appendChild(node("p", "library-meta", "没有需要修复的生日资料。"));
      selectionApply = button(repairing ? "确认修复生日资料" : "确认保存人物与关联", function () { return options.onApply(preview); }, selectionDisabled() || noRepairs, true); actions.appendChild(selectionApply);
      actions.appendChild(button("取消预览", options.onCancel, busy));
      panel.appendChild(actions);
    }
    if (state.preview && (array(people.characters).length || array(people.persons).length || array(people.cast).length)) parent.appendChild(node("h3", "library-section-title", "当前本地人物"));
    if (!state.preview || array(people.characters).length || array(people.persons).length || array(people.cast).length) renderEntities(parent, people, preference);
  }
  global.NimdaPeoplePanel = { render: render, renderPreview: renderPreview, collectSelections: collectSelections, sourceKey: sourceKey, sourcesForPeople: sourcesForPeople,
    entityProfile: entityProfile, portrait: portrait, entityDisplay: entityDisplay, summaryDisplay: summaryDisplay, qualifierLabel: qualifierLabel, localizedEntries: localizedEntries, localizedField: localizedField, languageLabel: function (language) { return languageLabels[language] || language; }, fieldValue: fieldValue, dispose: function () { closeDialog(false); } };
})(window);
