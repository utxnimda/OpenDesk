(function (global) {
  "use strict";
  function title(value) { return typeof value === "string" ? value.trim() : ""; }
  function display(row, preference, fallback) {
    row = row || {};
    var original = title(row.name) || title(row.title), chinese = title(row.name_cn);
    var preferred = preference === "chinese" ? chinese : original;
    var alternative = preference === "chinese" ? original : chinese;
    fallback = title(fallback) || "未命名章节";
    return { text: preferred || alternative || fallback, tooltip: alternative || preferred || fallback };
  }
  global.NimdaChapterTitles = { display: display };
})(window);
