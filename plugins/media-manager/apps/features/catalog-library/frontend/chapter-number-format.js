(function (global) {
  "use strict";
  function normalized(value) {
    if (typeof value !== "string" && typeof value !== "number") return "";
    var raw = String(value);
    if (raw.length > 18 || !/^\d{1,6}(?:\.\d{1,4})?$/.test(raw) || Number(raw) > 100000) return "";
    return String(Number(raw));
  }
  function number(row, index) {
    var keys = ["local_number", "number", "ep", "sort"];
    for (var i = 0; i < keys.length; i++) {
      var value = normalized(row[keys[i]]);
      if (value && (keys[i] !== "ep" || value !== "0")) return Number(value);
    }
    return index + 1;
  }
  function continuation(row, local) {
    var value = normalized(row.continuous_number);
    return row.has_continuous_number === true && value && value !== "0" && Number(value) !== local ? value : "";
  }
  function label(row, index) {
    var local = number(row, index), parts = String(local).split("."), continued = continuation(row, local);
    parts[0] = parts[0].padStart(2, "0");
    return parts.join(".") + (continued ? "(#" + continued + ")" : "");
  }
  function title(row, index) {
    var local = number(row, index), continued = continuation(row, local);
    return "本作第 " + local + " 集" + (continued ? "；续接第 " + continued + " 集" : "");
  }
  global.NimdaChapterNumbers = { number: number, label: label, title: title };
})(window);
