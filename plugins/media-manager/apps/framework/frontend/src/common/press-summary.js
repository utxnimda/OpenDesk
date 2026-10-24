(function () {
  "use strict";

  // Read-only presentation shared by the collection table and work cards.
  // Labels/order come from the same configured enums; paths are not required.
  function text(value) { return value == null ? "" : String(value).trim(); }
  function pair(item) {
    item = item && typeof item === "object" ? item : {};
    var group = text(item.press_group);
    return { fm: text(item.press_format), gp: group === "----" ? "" : group };
  }
  function display(labels, key, value) {
    if (!value) return "—";
    var values = labels && labels[key];
    return values && Object.prototype.hasOwnProperty.call(values, value) && text(values[value]) || value;
  }
  function columns(items, configured) {
    var result = [], seen = Object.create(null);
    function add(value) {
      value = text(value && typeof value === "object" ? value.value : value);
      if (value && !seen[value]) { seen[value] = true; result.push(value); }
    }
    (Array.isArray(configured) ? configured : []).forEach(add);
    (Array.isArray(items) ? items : []).forEach(function (item) { add(pair(item).fm); });
    return result;
  }
  function compareGroups(a, b, label) {
    function sortKey(value) {
      if (!value) return "";
      var key = text(label("press_group", value));
      if (key === "—") key = value;
      return key === "—" ? "" : key.toLowerCase();
    }
    var left = sortKey(a.gpTrim), right = sortKey(b.gpTrim);
    if (!left !== !right) return left ? -1 : 1;
    return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }) || a._sourceOrd - b._sourceOrd;
  }
  function entries(items, format, label) {
    format = text(format);
    if (!format) return [];
    label = label || function (_key, value) { return value; };
    return (Array.isArray(items) ? items : []).map(function (item, index) {
      return { itObj: item, gpTrim: pair(item).gp, fm: pair(item).fm, _sourceOrd: index };
    }).filter(function (item) { return item.fm === format; }).sort(function (a, b) { return compareGroups(a, b, label); });
  }
  function aggregate(items, options) {
    options = options || {};
    var label = options.display || function (key, value) { return display(options.labels, key, value); };
    var result = [];
    columns(items, options.formatOrder).forEach(function (format) {
      entries(items, format, label).forEach(function (entry) {
        var formatLabel = label("press_format", format), groupLabel = entry.gpTrim ? label("press_group", entry.gpTrim) : "";
        if (formatLabel === "—") formatLabel = format;
        if (groupLabel === "—") groupLabel = entry.gpTrim;
        result.push({ fmSlugP: format, itCellAg: entry.itObj, gpTrimP: entry.gpTrim,
          dispFm: formatLabel, dispGp: groupLabel, plainLine: formatLabel + " / " + (entry.gpTrim ? groupLabel : "—"),
          splitOn: !!(options.splitVisible && options.splitVisible[format]), _sourceOrd: entry._sourceOrd });
      });
    });
    return result;
  }
  function compact(items, options) {
    var result = [], seen = new Map();
    aggregate(items, options).forEach(function (entry) {
      var key = JSON.stringify([entry.fmSlugP, entry.gpTrimP]), existing = seen.get(key);
      if (existing) { existing.count++; return; }
      var row = { format: entry.fmSlugP, group: entry.gpTrimP, format_label: entry.dispFm,
        group_label: entry.gpTrimP ? entry.dispGp : "—", label: entry.plainLine, count: 1 };
      seen.set(key, row); result.push(row);
    });
    return result;
  }
  function groupDescriptions(registry) {
    registry = registry || {};
    var groups = new Map(), folded = new Map(), cached = new Map();
    function register(row, kind) {
      if (!row || typeof row !== "object" || !text(row.code)) return;
      var code = text(row.code);
      if (groups.has(code)) return;
      groups.set(code, { row: row, kind: text(row.kind) || kind });
      var key = code.toUpperCase();
      // Case folding helps read legacy spelling, but never combines cards or
      // resolves two distinct registry identities to one guessed owner.
      if (!folded.has(key)) folded.set(key, code);
      else if (folded.get(key) !== code) folded.set(key, null);
    }
    [["release_groups", "release"], ["translation_groups", "translation"],
      ["combinations", "combination"], ["catalog_only_groups", "catalog-only"], ["options", ""]].forEach(function (section) {
      (Array.isArray(registry[section[0]]) ? registry[section[0]] : []).forEach(function (row) { register(row, section[1]); });
    });
    function find(code) {
      return groups.get(code) || groups.get(folded.get(code.toUpperCase()));
    }
    function values(items) {
      return (Array.isArray(items) ? items : []).filter(function (value) { return typeof value === "string" && text(value); }).map(text);
    }
    function describe(code) {
      if (cached.has(code)) return cached.get(code);
      var names = [], seen = new Set(), releases = new Map(), translations = new Map();
      function append(member, fullNames, kind) {
        var roleNames = fullNames.length ? fullNames.join(" / ") : member;
        if (kind === "release") releases.set(member, roleNames);
        if (kind === "translation") translations.set(member, roleNames);
        if (seen.has(member)) return;
        seen.add(member);
        var known = fullNames.filter(function (name) { return name.toUpperCase() !== member.toUpperCase(); });
        if (known.length) names.push(known.join(" / "));
        else if (member.toUpperCase() !== code.toUpperCase()) names.push(member);
      }
      function visit(member, inheritedKind, ancestors) {
        var match = find(member), row = match && match.row, identity = row ? text(row.code) : member;
        if (!row || ancestors.has(identity) || ancestors.size >= 32) {
          append(identity, [], inheritedKind); return;
        }
        var kind = match.kind || inheritedKind, releases = values(row.release_groups), translations = values(row.translation_groups);
        var members = values(row.members);
        if (kind === "combination" || members.some(function (value) { return value !== identity; })) {
          var next = new Set(ancestors); next.add(identity);
          var selected = new Set(members.concat(releases, translations));
          if (!selected.size) { append(identity, values(row.names), kind); return; }
          selected.forEach(function (value) {
            visit(value, releases.indexOf(value) >= 0 ? "release" : translations.indexOf(value) >= 0 ? "translation" : "", next);
          });
        } else {
          // Classifier families describe file layout, not group identity.
          // In particular VCBM must resolve its own members, never just VCB.
          append(identity, values(row.names), kind);
        }
      }
      if (code) visit(code, "", new Set());
      var result = { names: names.join(" + "), release: Array.from(releases.values()).join(" + "),
        translation: Array.from(translations.values()).join(" + ") };
      cached.set(code, result);
      return result;
    }
    return describe;
  }
  function cardEntries(items, options) {
    options = options || {};
    var describe = groupDescriptions(options.groupRegistry);
    return compact(items, options).map(function (row) {
      var abbreviation = row.group || "—", label = row.format + "/" + abbreviation, names = describe(row.group).names;
      return Object.assign({}, row, { abbreviation: abbreviation, card_label: label, detail: label + (names ? "·" + names : "") });
    });
  }
  function cardFormats(items, options) {
    options = options || {};
    var describe = groupDescriptions(options.groupRegistry), formats = new Map(), result = [];
    aggregate(items, options).forEach(function (entry) {
      var format = entry.fmSlugP, grouped = formats.get(format);
      if (!grouped) {
        grouped = { row: { format: format, label: format, tooltip_lines: [], sections: [] }, seen: new Set() };
        formats.set(format, grouped); result.push(grouped.row);
      }
      var code = entry.gpTrimP, group = code || "----", details = describe(code);
      var line = group + (details.names ? "·" + details.names : "");
      if (!grouped.seen.has(code)) {
        grouped.seen.add(code); grouped.row.tooltip_lines.push(line);
      }
      // Hover lists group identities once; the click view retains every source
      // resource, including records with the same group and different paths.
      grouped.row.sections.push({ title: format + "/" + group, fields: [
        { label: "压制组", value: details.release || "未填写" },
        { label: "字幕组", value: details.translation || "未填写" },
        { label: "资源目录", value: text(entry.itCellAg && entry.itCellAg.press_path) || "未填写" },
      ] });
    });
    return result;
  }
  window.NimdaPressSummary = { pair: pair, columns: columns, compareGroups: compareGroups,
    entries: entries, aggregate: aggregate, compact: compact, cardEntries: cardEntries, cardFormats: cardFormats };
})();
