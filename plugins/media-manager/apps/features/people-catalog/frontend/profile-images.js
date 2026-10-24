(function (global) {
  "use strict";
  var labels = { bangumi: "Bangumi", wikipedia: "中文维基百科", commons: "维基共享资源", local: "本地图片", "tmdb-still": "TMDB 剧照" };
  var maxBytes = 2 * 1024 * 1024;
  function node(tag, cls, value) { var item = document.createElement(tag); if (cls) item.className = cls; if (value != null) item.textContent = String(value); return item; }
  function button(text, action) { var item = node("button", "library-button", text); item.type = "button"; item.addEventListener("click", function () { if (!item.disabled) action(); }); return item; }
  function field(parent, label, control) { var item = node("label", "person-library-field"); item.appendChild(node("span", "", label)); item.appendChild(control); parent.appendChild(item); return item; }
  function list(value) { return Array.isArray(value) ? value : []; }
  function safePage(url) { return /^https:\/\/(?:bangumi\.tv|bgm\.tv|zh\.wikipedia\.org|commons\.wikimedia\.org|www\.themoviedb\.org|creativecommons\.org)\//.test(String(url || "")); }
  function evidence(parent, value) {
    if (!value || !value.provider) return;
    var box = node("div", "person-library-image-evidence"), parts = [labels[value.provider] || value.provider, value.title || value.file_title || value.file_name || value.external_id, value.author, value.license].filter(Boolean);
    box.appendChild(node("p", "library-meta", "图片来源：" + parts.join(" · ")));
    [["查看来源", value.page_url], ["许可说明", value.license_url]].forEach(function (entry) { if (safePage(entry[1])) { var link = node("a", "library-button", entry[0]); link.href = entry[1]; link.target = "_blank"; link.rel = "noopener noreferrer"; box.appendChild(link); } });
    if (value.description) box.appendChild(node("p", "library-meta", value.description));
    parent.appendChild(box);
  }
  function localPayload(file) {
    if (!file) return Promise.reject(new Error("请选择一张本地图片。"));
    if (!Number.isFinite(file.size) || file.size <= 0 || file.size > maxBytes) return Promise.reject(new Error("本地图片须大于 0 且不超过 2 MiB。"));
    if (!/^image\/(jpeg|png|gif|webp)$/.test(file.type)) return Promise.reject(new Error("只支持 JPEG、PNG、GIF、WebP 图片。"));
    return new Promise(function (resolve, reject) {
      var reader = new global.FileReader();
      reader.onerror = function () { reject(new Error("读取本地图片失败，请重新选择。")); };
      reader.onabort = function () { reject(new Error("读取本地图片已取消。")); };
      reader.onload = function () {
        var result = String(reader.result || ""), marker = result.indexOf(";base64,");
        if (marker < 0) { reject(new Error("本地图片编码无效，请重新选择。")); return; }
        resolve({ provider: "local", file_name: file.name, mime_type: file.type, image_base64: result.slice(marker + 8) });
      };
      reader.readAsDataURL(file);
    });
  }
  function render(parent, options) {
    var detail = options.detail, person = detail.person || {}, sources = list(detail.sources);
    var form = node("form", "person-library-image-form"), provider = node("select"), external = node("input"), upload = node("input"), note = node("p", "library-meta"), gallery = node("section", "person-library-image-gallery");
    parent.appendChild(node("h3", "", "图片来源"));
    ["bangumi", "wikipedia", "commons", "tmdb-still", "local"].forEach(function (key) { var item = node("option", "", labels[key]); item.value = key; provider.appendChild(item); });
    provider.setAttribute("aria-label", "图片来源");
    var bound = sources.find(function (item) { return item.provider === "bangumi" || item.provider === "wikipedia"; });
    provider.value = bound ? bound.provider : "wikipedia";
    external.type = "text"; external.setAttribute("aria-label", "图片来源条目");
    upload.type = "file"; upload.accept = "image/jpeg,image/png,image/gif,image/webp"; upload.setAttribute("aria-label", "本地图片");
    field(form, "图源", provider); var remoteField = field(form, "来源条目", external), localField = field(form, "本地图片（最大 2 MiB）", upload);
    var tmdbFields = node("div", "person-library-image-tmdb-fields"), mediaType = node("select"), season = node("input"), episode = node("input");
    [["tv", "电视剧"], ["movie", "电影"]].forEach(function (entry) { var option = node("option", "", entry[1]); option.value = entry[0]; mediaType.appendChild(option); }); mediaType.value = "tv";
    [season, episode].forEach(function (input, index) { input.type = "number"; input.min = index ? "1" : "0"; input.step = "1"; input.placeholder = "不指定"; input.setAttribute("aria-label", index ? "剧照集数" : "剧照季数"); });
    field(tmdbFields, "作品类型", mediaType); field(tmdbFields, "季数（可选）", season); field(tmdbFields, "集数（可选）", episode); form.appendChild(tmdbFields);
    function setTmdbFields() { [season, episode].forEach(function (input) { var locked = provider.value !== "tmdb-still" || mediaType.value === "movie"; input.disabled = locked; input.dataset.locked = String(locked); }); }
    mediaType.addEventListener("change", function () { gallery.textContent = ""; setTmdbFields(); });
    [external, season, episode].forEach(function (input) { input.addEventListener("input", function () { gallery.textContent = ""; }); });
    function candidateParams(page) {
      if (!external.value.trim()) throw new Error("请输入 TMDB 作品 ID 或条目链接。");
      var payload = { provider: "tmdb", external_id: external.value.trim(), media_type: mediaType.value, page: page };
      if (mediaType.value === "tv") [season, episode].forEach(function (input, index) { if (input.value !== "") { var number = Number(input.value); if (!Number.isSafeInteger(number) || number < (index ? 1 : 0)) throw new Error("季数和集数必须是有效整数。"); payload[index ? "episode_number" : "season_number"] = number; } });
      return payload;
    }
    function showCandidates(data, params) {
      gallery.textContent = "";
      gallery.appendChild(node("p", "library-meta", "剧照候选 · 第 " + data.page + " 页 · " + data.total + " 张"));
      if (data.limited) gallery.appendChild(node("p", "library-meta", "仅列出前 30 张；可填写季数和集数查看某集的剧照。"));
      list(data.warnings).forEach(function (warning) { gallery.appendChild(node("p", "library-meta", typeof warning === "string" ? warning : warning.message)); });
      var grid = node("div", "person-library-image-grid");
      list(data.candidates).forEach(function (candidate, index) {
        if (!/^\/api\/catalog-library\/people\/assets\/[a-f0-9]{64}\.(jpg|png|webp|gif)$/.test(String(candidate.thumbnail_url || "")) || !candidate.candidate_token) return;
        var item = node("div", "person-library-image-candidate"), image = node("img"); image.src = candidate.thumbnail_url; image.alt = "剧照候选 " + (index + 1); image.loading = "lazy"; item.appendChild(image);
        if (candidate.width && candidate.height) item.appendChild(node("p", "library-meta", candidate.width + " × " + candidate.height));
        var chooseImage = button("选择此剧照", function () { options.onPreview(function () { return { provider: "tmdb-still", candidate_token: candidate.candidate_token }; }); }); chooseImage.setAttribute("aria-label", "选择第 " + (index + 1) + " 张剧照"); item.appendChild(chooseImage); grid.appendChild(item);
      });
      gallery.appendChild(grid);
      if (!grid.children.length) gallery.appendChild(node("p", "library-meta", "本页没有可用剧照；可尝试指定章节，或使用其他图源。"));
      var pagination = node("div", "person-library-actions");
      function pageButton(text, page, disabled) { var item = button(text, function () { var next = Object.assign({}, params, { page: page }); options.onCandidates(function () { return next; }, function (result) { showCandidates(result, next); }); }); item.disabled = disabled; item.dataset.locked = String(disabled); pagination.appendChild(item); }
      pageButton("上一页剧照", data.page - 1, data.page <= 1); pageButton("下一页剧照", data.page + 1, !data.has_more); gallery.appendChild(pagination);
    }
    function choose() {
      var local = provider.value === "local", tmdb = provider.value === "tmdb-still", matches = sources.filter(function (item) { return item.provider === provider.value; });
      localField.hidden = !local; remoteField.hidden = local; external.required = !local; upload.required = local;
      // Disabled controls do not participate in native form validation.
      upload.disabled = !local; upload.dataset.locked = String(!local); external.disabled = local; external.dataset.locked = String(local);
      tmdbFields.hidden = !tmdb; mediaType.disabled = !tmdb; mediaType.dataset.locked = String(!tmdb); setTmdbFields(); gallery.textContent = "";
      external.value = matches.length === 1 ? matches[0].external_id : "";
      external.placeholder = tmdb ? "TMDB 作品 ID 或 /tv/、/movie/ 条目链接" : provider.value === "bangumi" ? (person.type === "character" ? "角色 ID 或 /character/ 条目链接" : "人物 ID 或 /person/ 条目链接") : provider.value === "commons" ? "File:图片文件名 或共享资源文件页链接" : "维基条目标题、页面 ID 或链接";
      if (submit) submit.textContent = tmdb ? "读取剧照候选" : "获取并预览图片";
      note.textContent = tmdb ? "作品剧照不代表已识别到当前角色，请手动核对。留空季数和集数读取整部作品；指定章节时填写季数及集数。" : person.type === "character" ? "请核对图片中的角色；演员头像不会自动作为角色图片。预览不修改资料。" : "预览并确认后保存图片，不改变人物来源关联。";
    }
    provider.addEventListener("change", choose);
    var submit = node("button", "library-button is-primary", "获取并预览图片"); submit.type = "submit"; form.appendChild(submit); form.appendChild(button("返回资料", options.onBack));
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var choice = provider.value, identifier = external.value.trim(), file = upload.files && upload.files[0];
      if (choice === "tmdb-still") { options.onCandidates(function () { return candidateParams(1); }, function (data, params) { showCandidates(data, params); }); return; }
      options.onPreview(function () {
        if (choice === "local") return localPayload(file);
        if (["bangumi", "wikipedia", "commons"].indexOf(choice) < 0) throw new Error("请选择支持的图片来源。");
        if (!identifier) throw new Error("请输入图片来源条目。");
        return { provider: choice, external_id: identifier };
      });
    });
    choose(); parent.appendChild(form); parent.appendChild(note); parent.appendChild(gallery);
  }
  global.NimdaPeopleImages = { render: render, evidence: evidence, safePage: safePage };
})(window);
