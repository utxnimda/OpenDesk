(function (global) {
  "use strict";
  var categories = [["person", "人物"], ["organization", "组织"], ["object", "其他实体"], ["unknown", "待分类"]];
  var realities = [["real", "现实"], ["fictional", "虚构"], ["unknown", "待确认"]];
  var subtypes = { person: [["individual", "个人"], ["character", "角色"], ["unknown", "待确认"]], organization: [["corporation", "公司"], ["studio", "工作室"], ["association", "团体／协会"], ["organization", "组织"], ["unknown", "待确认"]], object: [["mechanic", "机体"], ["ship", "舰船"], ["object", "其他实体"], ["unknown", "待确认"]], unknown: [["unknown", "待确认"]] };
  var legacy = { "person:individual": ["person", "real", "individual"], "person:corporation": ["organization", "real", "corporation"], "person:association": ["organization", "real", "association"], "character:character": ["person", "fictional", "character"], "character:organization": ["organization", "fictional", "organization"], "character:mechanic": ["object", "fictional", "mechanic"], "character:ship": ["object", "fictional", "ship"] };
  function project(entity) { entity = entity || {}; if (entity.taxonomy) return { category: entity.taxonomy.category, reality: entity.taxonomy.reality, subtype: entity.taxonomy.subtype }; var values = legacy[entity.type + ":" + entity.kind] || ["unknown", "unknown", "unknown"]; return { category: values[0], reality: values[1], subtype: values[2] }; }
  function name(rows, value) { var row = rows.find(function (item) { return item[0] === value; }); return row ? row[1] : "待确认"; }
  function label(entity) { if (entity.taxonomy_label) return entity.taxonomy_label; var value = project(entity); if (value.category === "unknown") return "待分类"; if (value.category === "person") return ({ real: "现实人物", fictional: "虚拟角色", unknown: "人物 · 现实性待确认" })[value.reality] || "人物 · 现实性待确认"; return [name(categories, value.category), name(subtypes[value.category] || [], value.subtype), name(realities, value.reality)].join(" · "); }
  function options(select, rows, selected) { while (select.firstChild) select.removeChild(select.firstChild); rows.forEach(function (row) { var option = document.createElement("option"); option.value = row[0]; option.textContent = row[1]; select.appendChild(option); }); select.value = rows.some(function (row) { return row[0] === selected; }) ? selected : "unknown"; }
  function createEditor(value, onChange, schema) {
    value = value || project({}); var box = document.createElement("div"), controls = {}; box.className = "person-library-taxonomy-editor";
    function rows(key, fallback) { return schema && Array.isArray(schema[key]) ? schema[key].map(function (row) { return [row.value, row.label]; }) : fallback; }
    var categoryRows = rows("categories", categories), realityRows = rows("realities", realities);
    function subtypeRows(category) { return schema && schema.subtypes && Array.isArray(schema.subtypes[category]) ? schema.subtypes[category].map(function (row) { return [row.value, row.label]; }) : subtypes[category] || subtypes.unknown; }
    [["category", "实体类别"], ["reality", "现实性"], ["subtype", "细分类别"]].forEach(function (row) { var labelNode = document.createElement("label"), text = document.createElement("span"), control = document.createElement("select"); text.textContent = row[1]; control.setAttribute("aria-label", row[1]); labelNode.appendChild(text); labelNode.appendChild(control); box.appendChild(labelNode); controls[row[0]] = control; });
    options(controls.category, categoryRows, value.category); options(controls.reality, realityRows, value.reality); options(controls.subtype, subtypeRows(value.category), value.subtype);
    function read() { return { category: controls.category.value, reality: controls.reality.value, subtype: controls.subtype.value }; }
    Object.keys(controls).forEach(function (key) { controls[key].addEventListener("change", function () { if (key === "category") options(controls.subtype, subtypeRows(controls.category.value), controls.subtype.value); if (onChange) onChange(read()); }); });
    return { element: box, read: read };
  }
  global.NimdaPeopleTaxonomy = { project: project, label: label, createEditor: createEditor, categories: categories, realities: realities, subtypes: subtypes };
})(window);
