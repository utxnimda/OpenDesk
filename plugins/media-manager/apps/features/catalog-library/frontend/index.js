(function (global) {
  "use strict";

  var API = "/api/catalog-library/";
  var SIDEBAR_PREFERENCE = "nimda.catalog-library.sidebar-collapsed";
  var QUICK_FILTERS = [
    { field: "country", facet: "countries", label: "国家" },
    { field: "release_type", facet: "release_types", label: "媒体类型" },
    { field: "press_group", facet: "press_groups", label: "压制组" },
    { field: "press_format", facet: "press_formats", label: "压制格式" }
  ];
  var array = function (value) { return Array.isArray(value) ? value : []; };
  var text = function (value) { return String(value == null ? "" : value); };
  var copy = function (value) { return JSON.parse(JSON.stringify(value)); };
  var errorText = function (error) { return error && error.message ? error.message : text(error); };
  function attribute(record, kind, fallback) {
    var row = array(record && record.attributes).find(function (item) { return item && item.type === kind; });
    return row ? row.data : fallback;
  }
  function template() {
    return { attributes: [{ type: "name", data: "" }, { type: "country", data: "japan" },
      { type: "date", data: { start: "", end: "" } },
      { type: "collection-type", data: { domain: "animation", release_type: "tv", collectioned: [], markers: [] } }] };
  }
  function localCover(value) {
    // Only the dedicated local asset route is allowed. Never contact a provider
    // from an image element, including protocol-relative or encoded URLs.
    var raw = text(value);
    return /^\/api\/catalog-library\/assets\/[0-9a-f]{64}\.(?:jpg|png|gif|webp)$/.test(raw) ? raw : "";
  }
  function selector(item) {
    if (item && item.id) return item.ref ? { id: item.id, ref: copy(item.ref) } : { id: item.id };
    if (item && item.ref) return { ref: copy(item.ref) };
    throw new Error("作品缺少本地引用，请刷新作品库。");
  }
  function dateLabel(value) { return text(value).replace(/^([0-9X]{4})([0-9X]{2})([0-9X]{2})$/i, "$1-$2-$3"); }
  function fieldLabel(key) {
    return ({ name: "作品名", title: "作品名", name_cn: "中文名", aliases: "别名", summary: "简介", cover: "封面", cover_url: "封面",
      date: "日期", "date.start": "开始日期", "date.end": "结束日期", chapters: "章节", episodes: "章节", tags: "来源标签", relations: "作品关系", "metadata.ratings.douban": "豆瓣评分" })[key] || key;
  }
  function displayValue(value) { return value == null || value === "" ? "未填写" : typeof value === "object" ? JSON.stringify(value, null, 2) : text(value); }
  function doubanScore(rating) { return rating && typeof rating.score === "number" && Number.isFinite(rating.score) && rating.score > 0 && rating.score <= 10 && (rating.scale == null || rating.scale === 10) ? rating.score : null; }
  function effectiveProviderDifference(difference, selected, choices) {
    var picked = choices && choices[difference.field];
    if (picked) difference = Object.assign({}, difference, array(difference.choices).find(function (candidate) { return candidate.binding_id === picked; }) || {});
    var after = difference.after, changed = difference.changed !== false, selectable = difference.selectable !== false && difference.allowed !== false;
    if (difference.field === "metadata.aliases") {
      if (selected.indexOf("name") >= 0) {
        var byName = difference.after_by_name_binding || {}, nameBinding = choices && choices.name || difference.default_name_binding_id;
        if (Array.isArray(byName[nameBinding])) after = byName[nameBinding];
        else if (Array.isArray(difference.after_when_name_selected)) after = difference.after_when_name_selected;
      }
      changed = JSON.stringify(difference.before) !== JSON.stringify(after);
      selectable = difference.allowed !== false && Array.isArray(after) && after.length > 0;
    }
    return { after: after, changed: changed, selectable: selectable, after_url: difference.after_url };
  }
  function defaultProviderFields(preview) {
    var preferred = preview && preview.selected_fields;
    return array(preview && preview.diff).filter(function (row) {
      return row.selectable !== false && row.changed !== false &&
        (Array.isArray(preferred) ? preferred.indexOf(row.field) >= 0 : row.default_selected === true);
    }).map(function (row) { return row.field; });
  }
  function episodeRange(startValue, endValue) {
    var startText = text(startValue).trim(), endText = text(endValue).trim();
    if (!startText && !endText) return null;
    if (!startText || !endText) throw new Error("限定正片集数时，请同时填写起始集和结束集；全部留空表示完整条目。");
    var start = Number(startText), end = Number(endText), decimal = /^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
    if (!decimal.test(startText) || !decimal.test(endText) || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < 0) throw new Error("起止集数必须为不小于 0 的有限数值。");
    if (start > end) throw new Error("起始集不能大于结束集。");
    return { start: start, end: end };
  }
  function providerState() {
    return { provider: "bangumi", season: "1", query: "", loading: false, results: [], searchWarnings: [], unavailable: false, preview: null, selected: [], fieldChoices: {}, error: "", validation: false, retry: null, guidance: "", rangeStart: "", rangeEnd: "", rangeOpen: false, mode: "full", replaceBindingId: "", bindingRole: "", removeBindingId: "", selectionOpen: false };
  }
  function doubanSearchUrl(query) {
    var category = "1002";
    var value = text(query).trim(), match = /^https:\/\/www\.douban\.com\/search\?([^#]*)$/.exec(value);
    if (match) {
      try {
        var params = Object.create(null);
        match[1].split("&").forEach(function (part) { var index = part.indexOf("="); if (index >= 0) params[decodeURIComponent(part.slice(0, index))] = decodeURIComponent(part.slice(index + 1).replace(/\+/g, " ")); });
        if (params.cat === category && typeof params.q === "string") value = params.q;
      } catch (_) { /* Invalid input is still only an encoded search query. */ }
    }
    var encoded;
    try { encoded = encodeURIComponent(value); } catch (_) { encoded = encodeURIComponent(value.replace(/[\uD800-\uDFFF]/g, "\uFFFD")); }
    return "https://www.douban.com/search?cat=" + category + "&q=" + encoded;
  }
  function providerLabel(value) { return ({ bangumi: "Bangumi", tmdb: "TMDB", wikipedia: "中文维基百科", douban: "豆瓣评分", multiple: "多来源合并" })[value] || text(value); }
  function supportedProvider(provider) { return ["bangumi", "tmdb", "wikipedia", "douban"].indexOf(provider) >= 0; }
  function supportedSource(source) { return source && supportedProvider(source.provider) && (source.subject_id || source.external_id); }
  function supportsChapters(provider) { return provider === "bangumi" || provider === "tmdb"; }
  function sourceLink(source, fallbackProvider) {
    var provider = source.provider || fallbackProvider, id = text(source.external_id || source.subject_id || source.id), supplied = text(source.source_url || source.url);
    // Source links are navigation only, never remote images or arbitrary schemes.
    if (provider === "bangumi" && /^\d+$/.test(id)) return "https://bangumi.tv/subject/" + id;
    if (provider === "tmdb" && /^\d+$/.test(id)) return "https://www.themoviedb.org/tv/" + id + (Number.isInteger(Number(source.season_number)) && Number(source.season_number) >= 0 && source.season_number != null ? "/season/" + Number(source.season_number) : "");
    if (provider === "douban") {
      if (/^[1-9]\d*$/.test(id)) return "https://movie.douban.com/subject/" + id + "/";
      if (/^https:\/\/movie\.douban\.com\/subject\/[1-9]\d*\/$/.test(supplied)) return supplied;
    }
    if (provider === "wikipedia") {
      if (/^https:\/\/zh\.wikipedia\.org\/wiki\/[^\s<>]+$/.test(supplied)) return supplied;
      if (/^\d+$/.test(id)) return "https://zh.wikipedia.org/?curid=" + id;
    }
    return "";
  }
  function peopleState() { return { loading: false, preview: null, sourceKey: "", subjectId: "", error: "" }; }
  function numberingMode(row) { return row && row.numbering_mode === "continuous" ? "continuous" : "local"; }
  function sourceRows(detail) {
    var item = detail && (detail.item || detail.work || detail) || {}, record = detail && detail.record || item.record || {};
    return array(detail && detail.sources || item.sources || record.source_refs || record.sources);
  }
  function sourcePolicy(detail) {
    var item = detail && (detail.item || detail.work || detail) || {}, record = detail && detail.record || item.record || {};
    var collection = attribute(record, "collection-type", {}), domain = item.domain || collection.domain, country = item.country || attribute(record, "country", "");
    var korean = domain !== "animation" && ["korea", "south-korea", "south_korea", "kr", "韩国"].indexOf(text(country).toLowerCase()) >= 0;
    var defaults = { primary_provider: korean ? "tmdb" : "bangumi", supplement_providers: korean ? ["wikipedia"] : [] };
    if (!detail || !detail.source_policy) return defaults;
    var policy = Object.assign({}, detail.source_policy);
    policy.primary_provider = supportedProvider(policy.primary_provider) && policy.primary_provider !== "douban" ? policy.primary_provider : defaults.primary_provider;
    policy.supplement_providers = array(policy.supplement_providers).filter(supportedProvider);
    if (Array.isArray(policy.missing_providers)) policy.missing_providers = policy.missing_providers.filter(supportedProvider);
    return policy;
  }
  function sourceTitle(source) { return source.name_cn || source.name || source.title || source.display_name || "条目 " + text(source.subject_id || source.external_id || source.id); }
  function defaultSourceRole(provider, detail) { return provider !== "douban" && (provider === sourcePolicy(detail).primary_provider || !sourceRows(detail).filter(supportedSource).length) ? "primary" : "supplement"; }
  function peopleBindings(detail) { return global.NimdaPeoplePanel ? global.NimdaPeoplePanel.sourcesForPeople(sourceRows(detail)) : []; }
  function bindingRange(source) {
    var range = source.episode_range;
    if (range == null && !source.scope) return null;
    if (!range || typeof range !== "object" || range.start == null || range.end == null || text(range.start).trim() === "" || text(range.end).trim() === "") {
      throw new Error("此来源的章节范围缺失或无效，请在来源页手动核对起止集数后重新预览；不会自动改为完整条目。");
    }
    var parsed = episodeRange(range.start, range.end);
    if (source.scope) {
      var scope = /^episodes:(\d+(?:\.\d+)?(?:e[+-]?\d+)?)-(\d+(?:\.\d+)?(?:e[+-]?\d+)?)$/i.exec(text(source.scope));
      if (!scope || Number(scope[1]) !== parsed.start || Number(scope[2]) !== parsed.end) throw new Error("此来源的范围标识与起止集数不一致，请手动核对后重新预览。");
    }
    return parsed;
  }
  function tmdbBinding(source) {
    var season = source.season_number, scope = text(source.scope), parsed;
    if (typeof season !== "number" || !Number.isInteger(season) || season < 0 || season > 999) throw new Error("此 TMDB 来源的季数缺失或无效，请手动核对季数后重新预览；不会自动改为第一季。");
    if (scope) {
      parsed = /^season:(\d+)(?:;(episodes:.+))?$/.exec(scope);
      if (!parsed || Number(parsed[1]) !== season) throw new Error("此 TMDB 来源的季数与范围标识不一致，请手动核对后重新预览。");
    }
    var range = bindingRange({ episode_range: source.episode_range, scope: parsed && parsed[2] || "" });
    if (scope && !!(parsed && parsed[2]) !== !!range) throw new Error("此 TMDB 来源的章节范围与范围标识不一致，请手动核对后重新预览。");
    return { season: season, range: range };
  }

  function createController(options) {
    options = options || {};
    var state = { active: false, disposed: false, loaded: false, loading: false, error: "", busy: false, resourceOpening: false, playbackSelecting: false, searchDraft: "",
      filters: { query: "", domain: "", year: "", classification_id: "", country: "", release_type: "", press_group: "", press_format: "", page: 1, page_size: 24, sort: "name" },
      mode: options.mode === "list" ? "list" : "cards", items: [], total: 0, facets: {}, classifications: [], classificationRevision: "", detail: null, detailLoading: false,
      detailError: "", detailSelector: null, detailTab: "overview", identityPreview: null,
      provider: providerState(), people: peopleState(), peopleRetrying: false, retryingSnapshotId: "", playbackNumberingDrafts: {} };
    var browseSerial = 0, detailSerial = 0, providerSerial = 0, peopleSerial = 0, lifecycle = 0;
    var peopleRetryTicket = null, ratingBrowser = null, ratingCancellation = null, ratingCancelWait = null;
    function changed(reason) { if (!state.disposed && options.onChange) options.onChange(state, reason); }
    function notice(value, error) {
      if (state.disposed) return;
      if (options.setStatus) options.setStatus(value, !!error);
    }
    function providerFailure(error, retry) {
      state.provider.error = errorText(error); state.provider.validation = false; state.provider.retry = retry;
      notice(state.provider.error, true);
    }
    async function request(route, payload, signal) {
      var provider = providerLabel(payload && payload.provider || "bangumi");
      var titles = { "provider/search": "搜索 " + provider + " 来源", "provider/preview": "预览 " + provider + " 数据同步",
        "provider/cover-preview": "预览 " + provider + " 封面", "provider/sync-preview": "刷新作品来源数据",
        "display-settings": "读取作品库显示设置", "display-settings/save": "保存作品库显示设置",
        "people/wikipedia-preview": "读取维基人物资料并生成确认预览", "people/retry": "重试失败的人物资料读取",
        "douban/browser/start": "打开豆瓣评分页面", "douban/browser/step": "读取豆瓣评分", "douban/browser/cancel": "结束豆瓣评分读取" };
      var requestOptions = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}) };
      if (signal) requestOptions.signal = signal;
      if (titles[route]) requestOptions.operationTitle = titles[route];
      var response = await options.request(route[0] === "/" ? route : API + route, requestOptions);
      var data = response && response.data !== undefined ? response.data : response;
      if (!data || data.ok === false || response && response.res && !response.res.ok) throw new Error(data && (data.error || data.message) || "作品库请求失败");
      return data;
    }
    async function browse() {
      if (state.disposed || !state.active) return;
      var serial = ++browseSerial, epoch = lifecycle;
      state.loading = true; state.error = ""; changed("loading");
      try {
        var data = await request("browse", copy(state.filters));
        if (serial !== browseSerial || epoch !== lifecycle || state.disposed) return;
        state.items = array(data.items); state.total = Number(data.total) || 0; state.facets = data.facets || {};
        state.filters.page = Math.max(1, Number(data.page) || state.filters.page); state.loaded = true;
        state.identityMissing = Number(data.legacy_count) || 0; state.classificationRevision = data.classification_revision || "";
        state.classifications = array(state.facets.classifications);
      } catch (error) { if (serial === browseSerial && epoch === lifecycle) { state.error = errorText(error); notice(state.error, true); } }
      finally { if (serial === browseSerial && epoch === lifecycle) { state.loading = false; changed("browse"); } }
    }
    function setFilters(values) {
      if (state.disposed) return Promise.resolve();
      Object.keys(values).forEach(function (key) { if (Object.prototype.hasOwnProperty.call(state.filters, key)) state.filters[key] = values[key]; });
      if (Object.prototype.hasOwnProperty.call(values, "query")) state.searchDraft = text(values.query);
      if (!Object.prototype.hasOwnProperty.call(values, "page")) state.filters.page = 1;
      return browse();
    }
    function cancelRatingBrowser() {
      if (ratingCancelWait) ratingCancelWait.finish("cancelled");
      var browser = ratingBrowser; ratingBrowser = null;
      if (browser) {
        var cancellation = Promise.all([ratingCancellation, browser.cancel()]);
        ratingCancellation = cancellation;
        function settled() { if (ratingCancellation === cancellation) ratingCancellation = null; }
        cancellation.then(settled, settled);
      }
      state.provider.browser = null; state.provider.loading = false;
      return ratingCancellation;
    }
    function waitForRatingCancellation(cancellation) {
      return new Promise(function (resolve) {
        var wait = { timer: null, done: false, finish: function (outcome) {
          if (wait.done) return;
          wait.done = true; if (wait.timer !== null) global.clearTimeout(wait.timer);
          if (ratingCancelWait === wait) ratingCancelWait = null;
          resolve(outcome);
        } };
        ratingCancelWait = wait;
        wait.timer = global.setTimeout(function () { wait.finish("timeout"); }, 5000);
        Promise.resolve(cancellation).then(function () { wait.finish("finished"); }, function () { wait.finish("failed"); });
      });
    }
    function resetProvider() { cancelPeopleRetry(false); cancelRatingBrowser(); providerSerial += 1; state.provider = providerState(); peopleSerial += 1; state.people = peopleState(); }
    async function openDetail(item, preserveTab) {
      var selected = selector(item), serial = ++detailSerial, epoch = lifecycle;
      state.detailSelector = selected; state.detail = null; state.detailLoading = true; state.detailError = "";
      state.playbackNumberingDrafts = {};
      if (!preserveTab) state.detailTab = "overview";
      resetProvider(); changed("detail-loading");
      try {
        var data = await request("detail", selected);
        if (serial !== detailSerial || epoch !== lifecycle || state.disposed) return;
        state.detail = data.detail || data;
        state.provider.provider = sourcePolicy(state.detail).primary_provider || "bangumi";
      } catch (error) { if (serial === detailSerial && epoch === lifecycle) { state.detailError = errorText(error); notice(state.detailError, true); } }
      finally { if (serial === detailSerial && epoch === lifecycle) { state.detailLoading = false; changed("detail"); } }
    }
    function retireDetail() {
      detailSerial += 1; state.detailSelector = null; state.detail = null; state.detailLoading = false; state.detailError = ""; state.playbackNumberingDrafts = {}; resetProvider(); changed("close-detail");
    }
    function closeDetail() { if (!state.busy) retireDetail(); }
    function currentRef() {
      var detail = state.detail || {}, item = detail.item || detail.work || detail;
      var ref = detail.ref || item.ref;
      if (!ref) throw new Error("作品缺少当前版本引用，请重新加载详情。");
      return copy(ref);
    }
    function acceptPlaybackSave(detail, result) {
      if (state.disposed || !state.active || state.detail !== detail || !result) return;
      var oldItem = detail.item || detail.work || detail, oldRef = detail.ref || oldItem.ref, oldId = oldItem.id;
      var freshDetail = result.detail, freshItem = freshDetail && (freshDetail.item || freshDetail.work || freshDetail);
      var freshRef = result.ref || freshDetail && freshDetail.ref || freshItem && freshItem.ref;
      function matches(item) {
        return oldId && item.id === oldId || oldRef && item.ref && item.ref.yaml_source_rel === oldRef.yaml_source_rel && item.ref.index_in_file === oldRef.index_in_file;
      }
      // A list read started before this write must not restore stale card refs.
      browseSerial++; state.loading = false;
      if (freshRef) {
        state.items.forEach(function (item) {
          if (!matches(item)) return;
          if (freshItem) Object.assign(item, copy(freshItem));
          item.ref = copy(freshRef);
        });
        if (freshDetail && freshDetail.record) {
          Object.assign(detail, copy(freshDetail)); detail.ref = copy(freshRef);
          var savedItem = detail.item || detail.work || detail; savedItem.ref = copy(freshRef);
          state.detailSelector = selector(savedItem);
          // The player already owns the returned scan; keep it mounted.
          return;
        }
        // A committed mapping without the complete record cannot be edited
        // safely: reload with the fresh ref rather than reusing the old record.
        return openDetail({ id: oldId, ref: copy(freshRef) }, true);
      }
      if (result.db_committed) {
        // No receipt ref is available. Retire every cached positional selector
        // until a new browse can return current references.
        state.items = []; state.loaded = false; retireDetail(); return browse();
      }
    }
    function setPlaybackNumbering(index, mode) {
      if (state.busy || state.disposed || !state.active || !state.detail) return;
      var detail = state.detail, item = detail.item || detail.work || detail, resources = array(detail.resources || item.resources || item.press);
      if (!Number.isInteger(index) || !resources[index] || (mode !== "local" && mode !== "continuous")) return;
      var available = !!(detail.chapter_numbering && detail.chapter_numbering.has_continuous);
      if (!available && (mode === "continuous" || !resources.some(function (row) { return numberingMode(row) === "continuous"; }))) return;
      if (mode === numberingMode(resources[index])) delete state.playbackNumberingDrafts[index];
      else state.playbackNumberingDrafts[index] = mode;
      changed("playback-numbering");
    }
    async function selectPlaybackResource(index, mode) {
      if (state.busy || state.disposed || !state.active || !state.detail) return;
      var detail = state.detail, item = detail.item || detail.work || detail, resources = array(detail.resources || item.resources || item.press);
      if (!Number.isInteger(index) || !resources[index] || !text(resources[index].press_path).trim()) return;
      if (mode !== undefined && mode !== "local" && mode !== "continuous") return;
      if (mode === "continuous" && !(detail.chapter_numbering && detail.chapter_numbering.has_continuous)) return;
      if (resources[index].is_playback_resource && (mode === undefined || mode === numberingMode(resources[index]))) return;
      var selectionEpoch = lifecycle; state.playbackSelecting = true;
      try {
        return await mutation(async function (epoch) {
          try {
            var selection = { ref: currentRef(), resource_index: index };
            if (mode !== undefined) selection.numbering_mode = mode;
            var result = await request("playback/select", selection);
            if (epoch !== lifecycle || state.detail !== detail) return;
            delete state.playbackNumberingDrafts[index];
            notice(result.message || "已设置当前播放资源。");
            await acceptPlaybackSave(detail, result);
            return result;
          } finally { if (epoch === lifecycle) state.playbackSelecting = false; }
        });
      } finally {
        state.playbackSelecting = false;
        // If the user returned while selection was still being committed,
        // fetch cards only now, after the write outcome is known.
        if (selectionEpoch !== lifecycle && state.active && !state.disposed) await browse();
      }
    }
    async function refreshAfterWrite(result, epoch, openCreated) {
      if (epoch !== lifecycle || state.disposed) return;
      var saved = array(result.records)[0];
      if (saved && (state.detailSelector || openCreated)) {
        await openDetail({ id: saved.id || saved.record && saved.record.id, ref: saved.ref }, true);
      } else if (state.detailSelector) {
        // Never retry a legacy positional selector after a write using its old
        // version token; reload the list and let the user explicitly reopen it.
        state.detailSelector = null; state.detail = null; state.detailLoading = false;
      }
      await browse();
    }
    async function mutation(action) {
      if (state.busy || state.peopleRetrying || state.disposed) return;
      var epoch = lifecycle; state.busy = true; changed("mutation");
      try { return await action(epoch); }
      catch (error) { if (epoch === lifecycle) notice(errorText(error), true); throw error; }
      finally { state.busy = false; if (epoch === lifecycle) changed("mutation"); }
    }
    async function saveRecord(record, ref) {
      return mutation(async function (epoch) {
        var edits = [{ ref: ref || null, record: copy(record) }];
        var preview = await request("edits/preview", { edits: edits });
        var blocked = array(preview.issues).filter(function (issue) { return issue.blocking !== false; });
        if (blocked.length) throw new Error(blocked.map(function (issue) { return issue.message || issue.reason; }).join("；"));
        if (epoch !== lifecycle || state.disposed) throw new Error("页面已变化，请重新打开编辑窗口。");
        var result = await request("edits/apply", { edits: preview.edits || edits });
        if (epoch === lifecycle) {
          notice("作品已保存。"); await refreshAfterWrite(result, epoch, false);
        }
        return result;
      });
    }
    async function previewIdentities() {
      return mutation(async function (epoch) {
        var result = await request("identities/preview", {});
        if (epoch === lifecycle) state.identityPreview = result;
        return result;
      });
    }
    async function applyIdentities() {
      if (!state.identityPreview) throw new Error("请先预览需要初始化的标识。");
      var preview = state.identityPreview;
      return mutation(async function (epoch) {
        var issues = array(preview.issues).filter(function (issue) { return issue.blocking !== false; });
        if (issues.length) throw new Error(issues.map(function (issue) { return issue.message; }).join("；"));
        var result = await request("edits/apply", { edits: preview.edits });
        if (epoch === lifecycle) { state.identityPreview = null; notice("本地标识已初始化。"); await refreshAfterWrite(result, epoch, false); }
        return result;
      });
    }
    async function searchProvider(query) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      cancelRatingBrowser();
      var serial = ++providerSerial, epoch = lifecycle;
      state.provider.retry = null; state.provider.validation = false;
      state.provider.query = text(query).trim(); state.provider.mode = "full"; state.provider.guidance = ""; state.provider.loading = true; state.provider.error = ""; state.provider.results = []; state.provider.searchWarnings = []; state.provider.unavailable = false; state.provider.preview = null; state.provider.selected = []; changed("provider");
      try {
        var searchBody = { query: state.provider.query };
        if (state.provider.provider !== "bangumi") searchBody.provider = state.provider.provider;
        var result = await request("provider/search", searchBody);
        if (epoch === lifecycle && serial === providerSerial) {
          state.provider.results = array(result.items || result.results);
          state.provider.searchWarnings = array(result.warnings).concat(result.warning ? [result.warning] : []).map(function (warning) { return typeof warning === "string" ? warning : warning && warning.message; }).filter(Boolean);
          state.provider.unavailable = result.unavailable === true || result.blocked === true;
        }
      } catch (error) { if (epoch === lifecycle && serial === providerSerial) providerFailure(error, function () { return searchProvider(query); }); }
      finally { if (epoch === lifecycle && serial === providerSerial) { state.provider.loading = false; changed("provider"); } }
    }
    function setProviderQuery(value) {
      if (state.busy || state.disposed || state.provider.query === text(value)) return;
      cancelRatingBrowser();
      providerSerial += 1; state.provider.query = text(value); state.provider.results = []; state.provider.searchWarnings = [];
      state.provider.unavailable = false; state.provider.loading = false; state.provider.preview = null; state.provider.selected = []; state.provider.error = ""; state.provider.retry = null;
      changed("provider-query");
    }
    async function previewProvider(subjectId) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      cancelRatingBrowser();
      var ref, serial = ++providerSerial, epoch = lifecycle;
      state.provider.retry = null; state.provider.validation = false;
      state.provider.mode = "full"; state.provider.guidance = ""; state.provider.loading = true; state.provider.error = ""; state.provider.preview = null; state.provider.selected = []; changed("provider");
      var range;
      try { range = supportsChapters(state.provider.provider) ? episodeRange(state.provider.rangeStart, state.provider.rangeEnd) : null; }
      catch (error) {
        state.provider.error = errorText(error); state.provider.validation = true; state.provider.loading = false; changed("provider"); return;
      }
      try {
        ref = currentRef();
        var payload = { ref: ref, subject_id: text(subjectId) };
        if (state.provider.provider !== "bangumi") payload.provider = state.provider.provider;
        if (state.provider.provider === "tmdb") {
          if (!/^\d+$/.test(state.provider.season) || Number(state.provider.season) > 999) throw new Error("请填写 0～999 的有效季数（0 表示特别篇）。");
          payload.season_number = Number(state.provider.season);
        }
        if (range) payload.episode_range = range;
        if (state.provider.replaceBindingId) payload.replace_binding_id = state.provider.replaceBindingId;
        if (state.provider.bindingRole) payload.binding_role = state.provider.bindingRole;
        var result = await request("provider/preview", payload);
        if (epoch === lifecycle && serial === providerSerial) {
          state.provider.preview = result; state.provider.selected = defaultProviderFields(result);
        }
      } catch (error) { if (epoch === lifecycle && serial === providerSerial) providerFailure(error, function () { return previewProvider(subjectId); }); }
      finally { if (epoch === lifecycle && serial === providerSerial) { state.provider.loading = false; changed("provider"); } }
    }
    async function startRatingBrowser(subjectId, binding) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      if (ratingBrowser && state.provider.browser && state.provider.browser.polling) return;
      if (!global.NimdaRatingBrowser) throw new Error("评分读取组件未加载，请重新打开应用。");
      if (binding && (sourceRows(state.detail).indexOf(binding) < 0 || binding.provider !== "douban" || binding.enabled === false)) return;
      if (!binding && (state.provider.provider !== "douban" || !state.provider.results.some(function (row) { return text(row.id) === text(subjectId); }))) return;
      var cancellation = cancelRatingBrowser();
      var provider = state.provider, origin = state.detail, epoch = lifecycle, serial = ++providerSerial;
      state.detailTab = "sources"; provider.provider = "douban"; provider.mode = "full"; provider.preview = null; provider.selected = []; provider.fieldChoices = {}; provider.error = ""; provider.retry = null;
      var payload = { ref: currentRef(), subject_id: text(subjectId), binding_role: "supplement" };
      var replacement = binding ? binding.binding_id : provider.replaceBindingId;
      if (replacement) payload.replace_binding_id = replacement;
      function current() { return state.active && !state.disposed && lifecycle === epoch && state.detail === origin && state.provider === provider && providerSerial === serial && state.detailTab === "sources"; }
      if (cancellation) {
        provider.browser = { status: "loading", busy: true, polling: true, message: "正在结束上次评分读取，完成后打开所选作品页…" };
        provider.loading = true; changed("provider");
        var ended = await waitForRatingCancellation(cancellation);
        if (!current() || ended === "cancelled") return;
        if (ended !== "finished") {
          provider.loading = false;
          provider.browser = { status: "failed", busy: false, polling: false,
            message: "上次评分读取的取消尚未确认，未打开新窗口；请稍后重试，或取消本次等待。" };
          changed("provider"); return;
        }
      }
      ratingBrowser = global.NimdaRatingBrowser.create({ payload: payload, request: request, current: current,
        onChange: function (value) { provider.browser = value; provider.loading = !!value.busy; changed("provider"); },
        onComplete: function (preview) { provider.preview = preview; provider.selected = defaultProviderFields(preview); }
      });
      return ratingBrowser.start();
    }
    async function refreshProvider(binding) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      var bindings = sourceRows(state.detail).filter(supportedSource), selected = binding;
      state.detailTab = "sources"; resetProvider();
      state.provider.provider = sourcePolicy(state.detail).primary_provider || "bangumi";
      if (selected != null && bindings.indexOf(selected) < 0) {
        state.provider.error = "来源关联已变化，请重新选择当前作品的来源。"; state.provider.validation = true; changed("provider"); return;
      }
      if (bindings.length && bindings.every(function (source) { return !!source.binding_id; })) {
        if (selected && selected.enabled === false || !selected && !bindings.some(function (source) { return source.enabled !== false; })) {
          state.provider.guidance = "当前没有启用的来源，请先启用来源后再刷新。"; changed("provider-source-choose"); return;
        }
        var payload = { ref: currentRef() }, serial = ++providerSerial, epoch = lifecycle;
        if (selected) payload.binding_ids = [selected.binding_id];
        state.provider.mode = "sync"; state.provider.loading = true; changed("provider");
        try {
          var result = await request("provider/sync-preview", payload);
          if (serial === providerSerial && epoch === lifecycle) {
            state.provider.preview = result;
            state.provider.selected = defaultProviderFields(result);
          }
        } catch (error) { if (serial === providerSerial && epoch === lifecycle) providerFailure(error, function () { return refreshProvider(binding); }); }
        finally { if (serial === providerSerial && epoch === lifecycle) { state.provider.loading = false; changed("provider"); } }
        return;
      }
      if (selected == null && bindings.length === 1) selected = bindings[0];
      if (selected == null) {
        if (!bindings.length) {
          state.provider.query = titleOf(state.detail.item || state.detail.work || state.detail);
          state.provider.provider = sourcePolicy(state.detail).primary_provider || "bangumi";
          state.provider.bindingRole = "primary";
          state.provider.guidance = "尚未关联来源，请搜索并确认条目。";
          changed("provider-search-start");
        } else {
          state.provider.guidance = "已关联多个来源或章节范围，请选择下方要刷新的来源。";
          changed("provider-source-choose");
        }
        return;
      }
      if (bindings.indexOf(selected) < 0) {
        state.provider.error = "来源关联已变化，请重新选择当前作品的来源。"; state.provider.validation = true; changed("provider"); return;
      }
      try {
        state.provider.provider = selected.provider;
        var tmdb = selected.provider === "tmdb" ? tmdbBinding(selected) : null;
        if (tmdb) state.provider.season = text(tmdb.season);
        var range = tmdb ? tmdb.range : selected.provider === "bangumi" ? bindingRange(selected) : null;
        state.provider.rangeStart = range ? text(range.start) : "";
        state.provider.rangeEnd = range ? text(range.end) : "";
        state.provider.rangeOpen = !!range;
      } catch (error) { state.provider.error = errorText(error); state.provider.validation = true; changed("provider"); return; }
      return previewProvider(selected.subject_id || selected.external_id);
    }
    function beginSourceSelection(binding, providerName, role) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      if (binding && (sourceRows(state.detail).indexOf(binding) < 0 || !binding.binding_id || !supportedSource(binding)) || providerName && !supportedProvider(providerName)) return;
      resetProvider(); state.detailTab = "sources";
      state.provider.selectionOpen = true;
      var policy = sourcePolicy(state.detail);
      state.provider.provider = binding && binding.provider || providerName || !policy.custom && array(policy.missing_providers)[0] || policy.primary_provider || "bangumi";
      state.provider.bindingRole = state.provider.provider === "douban" ? "supplement" : binding && binding.role || role || defaultSourceRole(state.provider.provider, state.detail);
      state.provider.replaceBindingId = binding && binding.binding_id || "";
      state.provider.query = state.provider.provider === "douban" && binding ? sourceLink(binding) : titleOf(state.detail.item || state.detail.work || state.detail);
      if (binding) {
        if (binding.provider === "tmdb" && binding.season_number != null) state.provider.season = text(binding.season_number);
        if (supportsChapters(binding.provider) && binding.episode_range) { state.provider.rangeStart = text(binding.episode_range.start); state.provider.rangeEnd = text(binding.episode_range.end); state.provider.rangeOpen = true; }
      }
      state.provider.guidance = binding ? "替换「" + sourceTitle(binding) + "」· 预览后确认" : "";
      changed("provider-search-start");
    }
    async function updateSourceBinding(binding, action, role) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      if (!binding || !supportedSource(binding) || !binding.binding_id || sourceRows(state.detail).indexOf(binding) < 0) throw new Error("来源关联已变化，请重新加载详情。");
      var payload = { ref: currentRef(), binding_id: binding.binding_id, action: action };
      if (role) payload.role = binding.provider === "douban" ? "supplement" : role;
      resetProvider();
      return mutation(async function (epoch) {
        var result = await request("provider/binding-update", payload);
        if (epoch === lifecycle) { notice(action === "remove" ? "来源关联已移除，已有作品资料保留。" : "来源设置已保存。"); await refreshAfterWrite(result, epoch, false); }
        return result;
      });
    }
    async function previewCover(subjectId, binding) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.provider.loading) return;
      cancelRatingBrowser();
      if (binding && !supportsChapters(binding.provider)) { notice("此来源不提供封面，请选择 Bangumi 或 TMDB。", true); return; }
      var serial = ++providerSerial, epoch = lifecycle, payload = { ref: currentRef() };
      state.provider.retry = null; state.provider.validation = false;
      if (subjectId != null) payload.subject_id = text(subjectId);
      state.detailTab = "sources"; state.provider.mode = "cover"; state.provider.loading = true;
      state.provider.error = ""; state.provider.preview = null; state.provider.selected = []; changed("provider");
      try {
        state.provider.provider = binding && binding.provider || "bangumi";
        if (state.provider.provider === "tmdb") {
          var tmdb = tmdbBinding(binding); payload.provider = "tmdb"; payload.season_number = tmdb.season; state.provider.season = text(tmdb.season);
        }
        var result = await request("provider/cover-preview", payload);
        if (epoch === lifecycle && serial === providerSerial) {
          state.provider.preview = result; state.provider.selected = defaultProviderFields(result);
        }
      } catch (error) { if (epoch === lifecycle && serial === providerSerial) providerFailure(error, function () { return previewCover(subjectId, binding); }); }
      finally { if (epoch === lifecycle && serial === providerSerial) { state.provider.loading = false; changed("provider"); } }
    }
    function setEpisodeRange(start, end) {
      if (state.busy || state.disposed) return;
      var provider = state.provider;
      if (provider.rangeStart === text(start) && provider.rangeEnd === text(end)) return;
      provider.rangeStart = text(start); provider.rangeEnd = text(end);
      // A range is part of the server-signed preview. Changing it must retire
      // both a displayed token and an in-flight preview before another apply.
      providerSerial += 1; provider.preview = null; provider.selected = []; provider.error = ""; provider.validation = false; provider.retry = null; provider.loading = false;
      changed("provider-range");
    }
    function chooseProvider(value) {
      if (state.busy || state.disposed || !supportedProvider(value)) return;
      var query = state.provider.query, replaceBindingId = state.provider.replaceBindingId, bindingRole = state.provider.bindingRole, guidance = state.provider.guidance, selectionOpen = state.provider.selectionOpen;
      if (!replaceBindingId) bindingRole = value === "douban" ? "supplement" : selectionOpen ? defaultSourceRole(value, state.detail) : "";
      cancelRatingBrowser(); providerSerial += 1; state.provider = providerState(); state.provider.provider = value; state.provider.query = query;
      state.provider.replaceBindingId = replaceBindingId; state.provider.bindingRole = bindingRole; state.provider.guidance = guidance;
      state.provider.selectionOpen = selectionOpen;
      changed("provider-choice");
    }
    function setProviderSeason(value) {
      if (state.busy || state.disposed) return;
      state.provider.season = text(value); providerSerial += 1;
      state.provider.preview = null; state.provider.selected = []; state.provider.loading = false; state.provider.retry = null; state.provider.error = "";
      changed("provider-range");
    }
    function selectProviderField(field, selected) {
      if (state.busy || state.peopleRetrying || !state.provider.preview) return;
      if (state.provider.preview.provider === "douban" && field !== "metadata.ratings.douban") return;
      var difference = array(state.provider.preview.diff).find(function (row) { return row.field === field; });
      if (!difference) return;
      var effective = effectiveProviderDifference(difference, state.provider.selected, state.provider.fieldChoices);
      if (!effective.selectable || !effective.changed) return;
      var fields = state.provider.selected.filter(function (key) { return key !== field; });
      if (selected) fields.push(field);
      var aliases = array(state.provider.preview.diff).find(function (row) { return row.field === "metadata.aliases"; });
      if (aliases) {
        var aliasState = effectiveProviderDifference(aliases, fields, state.provider.fieldChoices);
        if (!aliasState.selectable || !aliasState.changed) fields = fields.filter(function (key) { return key !== "metadata.aliases"; });
      }
      state.provider.selected = fields;
    }
    function chooseProviderField(field, bindingId) {
      if (state.busy || state.peopleRetrying || !state.provider.preview) return;
      var difference = array(state.provider.preview.diff).find(function (row) { return row.field === field; });
      if (!difference || !array(difference.choices).some(function (row) { return row.binding_id === bindingId; })) return;
      state.provider.fieldChoices[field] = bindingId;
      var effective = effectiveProviderDifference(difference, state.provider.selected, state.provider.fieldChoices);
      if (!effective.changed || !effective.selectable) state.provider.selected = state.provider.selected.filter(function (key) { return key !== field; });
      if (field === "name") {
        var aliases = array(state.provider.preview.diff).find(function (row) { return row.field === "metadata.aliases"; });
        if (aliases) {
          var aliasState = effectiveProviderDifference(aliases, state.provider.selected, state.provider.fieldChoices);
          if (!aliasState.changed || !aliasState.selectable) state.provider.selected = state.provider.selected.filter(function (key) { return key !== "metadata.aliases"; });
        }
      }
    }
    async function applyProvider(expectedPreview) {
      if (!state.detail || !state.active || state.detailLoading || state.disposed || state.provider.loading || state.peopleRetrying) throw new Error("页面或预览已变化，请重新预览后确认。");
      var preview = state.provider.preview, fields = state.provider.selected.filter(function (field) { return !preview || preview.provider !== "douban" || field === "metadata.ratings.douban"; });
      if (!preview) throw new Error("请先获取来源快照并预览差异。");
      if (preview.can_apply === false || state.provider.mode === "sync" && !preview.preview_token) throw new Error("没有可确认的来源结果，请重试获取来源数据。");
      if (expectedPreview && expectedPreview !== preview) throw new Error("当前预览已更新，不能应用旧的预览。");
      if (state.provider.mode === "cover" && fields.indexOf("metadata.cover") < 0) throw new Error("请先勾选封面，再确认更新。");
      var peopleSelections = preview.provider === "douban" ? {} : global.NimdaPeoplePanel.collectSelections(preview);
      return mutation(async function (epoch) {
        var applyBody = { ref: preview.ref, snapshot_id: preview.snapshot_id, preview_token: preview.preview_token, selected_fields: fields };
        if (Object.keys(peopleSelections).length) applyBody.people_selections = peopleSelections;
        var combined = state.provider.mode === "sync";
        if (combined) {
          delete applyBody.snapshot_id;
          var choices = {};
          fields.forEach(function (field) { if (state.provider.fieldChoices[field]) choices[field] = state.provider.fieldChoices[field]; });
          if (Object.keys(choices).length) applyBody.field_choices = choices;
        }
        else if (preview.provider && preview.provider !== "bangumi") applyBody.provider = preview.provider;
        var result = await request(combined ? "provider/sync-apply" : "provider/apply", applyBody);
        if (epoch === lifecycle) {
          var addedAliases = array(result.automatic_aliases_added).filter(function (alias) { return typeof alias === "string" && alias.trim(); });
          var message = fields.length ? "所选来源字段已写入本地作品。" : addedAliases.length ? "来源关联已保存。" : "来源关联已保存，未修改作品字段。";
          if (addedAliases.length) message += "已自动追加别名：" + addedAliases.join("、") + "；已有别名保留并去重。";
          if (result.people_updated) message += "人物资料与关联已同步。";
          if (array(result.warnings).length) message += "部分资料未完整获取，详情见处理记录。";
          notice(message); await refreshAfterWrite(result, epoch, false);
        }
        return result;
      });
    }
    function cancelPeopleRetry(notifyChange) {
      var ticket = peopleRetryTicket; if (!ticket) return;
      peopleRetryTicket = null; if (ticket.abort) ticket.abort.abort();
      ticket.holder.loading = false; state.peopleRetrying = false; state.retryingSnapshotId = "";
      if (notifyChange !== false) changed("people-retry");
    }
    async function retryPeopleFailures(scope, expectedPreview, child, ids) {
      var holder = scope === "provider" ? state.provider : scope === "people" ? state.people : null;
      if (!holder || !global.NimdaPeopleRetry || !state.active || state.disposed || !state.detail || state.detailLoading || state.busy || state.peopleRetrying || holder.loading || holder.preview !== expectedPreview) return;
      if (global.NimdaPeopleRetry.previews(expectedPreview).indexOf(child) < 0 || !expectedPreview.preview_token) return;
      var failures = global.NimdaPeopleRetry.failures(child), available = failures.map(function (row) { return row.id; });
      if (!failures.length || ids && (!array(ids).length || ids.some(function (id) { return available.indexOf(id) < 0; }))) return;
      var ticket = { holder: holder, preview: expectedPreview, detail: state.detail, epoch: lifecycle, tab: state.detailTab,
        abort: typeof global.AbortController === "function" ? new global.AbortController() : null };
      peopleRetryTicket = ticket; state.peopleRetrying = true; state.retryingSnapshotId = text(child.snapshot_id);
      holder.loading = true; holder.error = ""; child._retryError = ""; changed("people-retry");
      function current() { return peopleRetryTicket === ticket && state.active && !state.disposed && lifecycle === ticket.epoch && state.detail === ticket.detail && state.detailTab === ticket.tab && (scope === "provider" ? state.provider : state.people) === holder && holder.preview === expectedPreview; }
      try {
        var payload = { ref: expectedPreview.ref || currentRef(), preview_token: expectedPreview.preview_token, snapshot_id: child.snapshot_id };
        if (ids) payload.failure_ids = ids.slice();
        var result = await request("people/retry", payload, ticket.abort && ticket.abort.signal);
        if (!current()) return;
        global.NimdaPeopleRetry.applyPatch(expectedPreview, result);
        var remaining = global.NimdaPeopleRetry.previews(expectedPreview).reduce(function (count, preview) { return count + global.NimdaPeopleRetry.failures(preview).length; }, 0);
        notice(remaining ? "重试已完成，仍有 " + remaining + " 项未完成；成功资料和已确认选择已保留。" : "失败项已补齐，请核对新增差异后统一保存。未修改本地资料。");
        return expectedPreview;
      } catch (error) {
        if (current()) { child._retryError = errorText(error) + "；原预览与已选内容已保留，可再次重试。"; notice(child._retryError, true); }
      } finally { if (peopleRetryTicket === ticket) { cancelPeopleRetry(false); changed("people-retry"); } }
    }
    async function previewPeople(selection) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.people.loading) return;
      var detail = state.detail, epoch = lifecycle, serial = ++peopleSerial;
      var sources = peopleBindings(detail), key = text(selection || state.people.sourceKey || state.people.subjectId || "");
      var matches = sources.filter(function (source) { return global.NimdaPeoplePanel.sourceKey(source) === key; });
      if (!matches.length && key) matches = sources.filter(function (source) { return (source.provider || "bangumi") === "bangumi" && text(source.subject_id || source.external_id) === key; });
      var binding = key ? matches.length === 1 ? matches[0] : null : sources.length === 1 ? sources[0] : null;
      if (!binding) {
        notice(sources.length ? "请选择当前作品已关联的具体人物来源。" : "请先在来源页关联支持人物资料的作品来源。", true); return;
      }
      var provider = binding.provider || "bangumi", subjectId = text(binding.subject_id || binding.external_id), payload = { ref: currentRef() };
      if (binding.binding_id) { payload.provider = provider; payload.binding_id = binding.binding_id; }
      else if (provider === "tmdb") {
        payload.provider = provider; payload.external_id = subjectId; payload.season_number = binding.season_number;
        if (binding.scope) payload.scope = binding.scope;
        if (binding.episode_range) payload.episode_range = copy(binding.episode_range);
      } else if (provider === "wikipedia") {
        payload.provider = provider; payload.external_id = subjectId;
      } else {
        payload.subject_id = subjectId;
        if (binding.scope) payload.scope = binding.scope;
        if (binding.episode_range) payload.episode_range = copy(binding.episode_range);
      }
      state.people = { loading: true, preview: null, sourceKey: global.NimdaPeoplePanel.sourceKey(binding), subjectId: provider === "bangumi" ? subjectId : "", error: "" }; changed("people");
      try {
        var result = await request("people/preview", payload);
        if (serial === peopleSerial && epoch === lifecycle && state.detail === detail && !state.disposed) state.people.preview = result;
      } catch (error) {
        if (serial === peopleSerial && epoch === lifecycle && state.detail === detail) { state.people.error = errorText(error); notice(state.people.error, true); }
      } finally {
        if (serial === peopleSerial && epoch === lifecycle && state.detail === detail) { state.people.loading = false; changed("people"); }
      }
    }
    async function previewWikipediaPeople(pages) {
      if (!state.detail || !state.active || state.detailLoading || state.busy || state.disposed || state.people.loading) return;
      if (!array(pages).length) { notice("请先填写至少一个维基人物条目。", true); return; }
      var detail = state.detail, epoch = lifecycle, serial = ++peopleSerial;
      state.people.loading = true; state.people.preview = null; state.people.error = ""; changed("people");
      try {
        var result = await request("people/wikipedia-preview", { ref: currentRef(), pages: copy(pages) });
        if (serial === peopleSerial && epoch === lifecycle && state.detail === detail && !state.disposed) state.people.preview = result;
      } catch (error) {
        if (serial === peopleSerial && epoch === lifecycle && state.detail === detail) { state.people.error = errorText(error); notice(state.people.error, true); }
      } finally {
        if (serial === peopleSerial && epoch === lifecycle && state.detail === detail) { state.people.loading = false; changed("people"); }
      }
    }
    async function applyPeople(expectedPreview) {
      var preview = state.people.preview;
      if (!state.active || state.disposed || !state.detail || state.detailLoading || state.people.loading || state.peopleRetrying || !preview || expectedPreview && preview !== expectedPreview) throw new Error("人物预览已变化，请重新获取后确认。");
      var peopleSelections = global.NimdaPeoplePanel.collectSelections(preview);
      return mutation(async function (epoch) {
        var payload = { ref: preview.ref, preview_token: preview.preview_token };
        if (preview.subject_id != null) payload.subject_id = preview.subject_id;
        if (preview.snapshot_id != null) payload.snapshot_id = preview.snapshot_id;
        if (preview.provider) payload.provider = preview.provider;
        if (preview.binding_id) payload.binding_id = preview.binding_id;
        if (Object.keys(peopleSelections).length) payload.people_selections = peopleSelections;
        var result = await request("people/apply", payload);
        if (epoch === lifecycle) {
          notice((result.people_updated === false ? "人物检查完成，现有资料已保留。" : "人物资料与作品关联已保存。") +
            (array(result.warnings).length ? "部分资料未完整获取，详情见处理记录。" : ""));
          await refreshAfterWrite(result, epoch, false);
        }
        return result;
      });
    }
    async function assignClassification(valueId, action) {
      return mutation(async function (epoch) {
        var preview = await request("classification-edits/preview", { refs: [currentRef()], value_id: valueId, action: action || "assign" });
        var issues = array(preview.issues).filter(function (issue) { return issue.blocking !== false; });
        if (issues.length) throw new Error(issues.map(function (issue) { return issue.message; }).join("；"));
        if (epoch !== lifecycle) throw new Error("页面已变化，请重新打开分类页。");
        var result = await request("edits/apply", { edits: preview.edits });
        if (epoch === lifecycle) { notice("分类已保存。"); await refreshAfterWrite(result, epoch, false); }
        return result;
      });
    }
    async function saveClassification(name, id) {
      return mutation(async function (epoch) {
        if (!text(name).trim()) throw new Error("请输入系列名称。");
        var payload = { revision: state.classificationRevision, type: "series", name: text(name).trim() };
        if (id) payload.id = id;
        var result = await request("classifications", payload);
        if (epoch === lifecycle) { notice(id ? "系列名称已保存。" : "系列已创建，可在下方关联当前作品。"); await browse(); }
        return result;
      });
    }
    async function openResource(workPath, pressPath) {
      if (state.resourceOpening || state.busy || state.disposed) return;
      var epoch = lifecycle, selected = detailSerial;
      state.resourceOpening = true; changed("resource");
      try {
        if (!text(workPath).trim() || !text(pressPath).trim()) throw new Error("资源尚未绑定完整目录。");
        var result = await request("/api/collection-detail/press/open", { path: workPath, press_path: pressPath });
        if (epoch === lifecycle && selected === detailSerial) notice("已请求打开资源目录。");
        return result;
      } catch (error) {
        if (epoch === lifecycle && selected === detailSerial) notice(errorText(error), true);
      } finally { state.resourceOpening = false; if (epoch === lifecycle) changed("resource"); }
    }
    function deactivate() {
      cancelPeopleRetry(false); cancelRatingBrowser();
      state.active = false; lifecycle += 1; browseSerial += 1; detailSerial += 1; providerSerial += 1; peopleSerial += 1;
      if (state.playbackSelecting) { state.detailSelector = null; state.detail = null; state.detailError = ""; state.items = []; state.loaded = false; }
      state.loading = false; state.detailLoading = false; state.provider.loading = false; state.provider.preview = null; state.provider.selected = []; state.identityPreview = null;
      state.people = peopleState();
    }
    return { state: state, browse: browse, setFilters: setFilters, openDetail: openDetail, closeDetail: closeDetail,
      readDisplaySettings: function () { return request("display-settings", {}); },
      previewClear: function () { return request("work-details/clear-preview", { ref: currentRef() }); },
      previewRoles: function () { return request("characters/materialize/preview", { ref: currentRef() }); },
      applyRoles: function (preview, ids) {
        return mutation(async function (epoch) {
          var result = await request("characters/materialize/apply", { ref: preview.ref, preview_token: preview.preview_token, character_ids: ids });
          if (epoch === lifecycle) { notice("所选角色档案已保存；相似角色仅提示候选，不自动合并。"); await refreshAfterWrite(result, epoch, false); }
          return result;
        });
      },
      applyClear: function (preview, groups) {
        return mutation(async function (epoch) {
          var result = await request("work-details/clear-apply", { ref: preview.ref, preview_token: preview.preview_token, groups: groups });
          if (epoch === lifecycle) { notice("所选作品资料已清空；公共人物库和媒体文件未修改。"); await refreshAfterWrite(result, epoch, false); }
          return result;
        });
      },
      saveDisplaySettings: async function (payload) {
        var result = await request("display-settings/save", payload);
        if (state.active && !state.disposed) { notice("作品库显示设置已保存。"); if (state.detailSelector) await openDetail(state.detailSelector, true); }
        return result;
      },
      setSearchDraft: function (value) { if (!state.disposed) state.searchDraft = text(value); },
      currentRef: currentRef, saveRecord: saveRecord, previewIdentities: previewIdentities, applyIdentities: applyIdentities, acceptPlaybackSave: acceptPlaybackSave, selectPlaybackResource: selectPlaybackResource, setPlaybackNumbering: setPlaybackNumbering,
      searchProvider: searchProvider, setProviderQuery: setProviderQuery,
      startRatingBrowser: startRatingBrowser, cancelRatingBrowser: function () { cancelRatingBrowser(); changed("provider"); }, stepRatingBrowser: function (action) { return ratingBrowser ? ratingBrowser.step(action) : Promise.resolve(null); }, previewProvider: previewProvider, refreshProvider: refreshProvider, previewCover: previewCover, setEpisodeRange: setEpisodeRange, selectProviderField: selectProviderField,
      chooseProvider: chooseProvider, setProviderSeason: setProviderSeason, beginSourceSelection: beginSourceSelection, updateSourceBinding: updateSourceBinding, chooseProviderField: chooseProviderField,
      confirmSourceRemoval: function (bindingId) { if (!state.busy && !state.provider.loading) { state.provider.removeBindingId = bindingId || ""; changed("provider"); } },
      cancelSourceSelection: function () { if (!state.busy && !state.provider.loading) { resetProvider(); state.provider.provider = sourcePolicy(state.detail).primary_provider || "bangumi"; changed("provider-source-choose"); } },
      applyProvider: applyProvider, assignClassification: assignClassification, saveClassification: saveClassification, openResource: openResource,
      previewPeople: previewPeople, previewWikipediaPeople: previewWikipediaPeople, applyPeople: applyPeople,
      retryPeopleFailures: retryPeopleFailures, cancelPeopleRetry: cancelPeopleRetry,
      cancelPeoplePreview: function () { if (!state.busy) { cancelPeopleRetry(false); peopleSerial += 1; state.people.preview = null; state.people.loading = false; changed("people"); } },
      choosePeopleSource: function (key) { if (!state.busy && !state.people.loading) { state.people.sourceKey = text(key); state.people.subjectId = ""; state.people.preview = null; state.people.error = ""; peopleSerial += 1; changed("people"); } },
      setMode: function (mode) { state.mode = mode === "list" ? "list" : "cards"; changed("mode"); },
      setDetailTab: function (tab) { if (!state.playbackSelecting) { if (state.detailTab !== tab) { cancelPeopleRetry(false); cancelRatingBrowser(); } state.detailTab = tab; changed("detail-tab"); } },
      activate: function () {
        state.active = true;
        if (state.playbackSelecting) { state.loading = true; changed("loading"); return Promise.resolve(); }
        if (state.detailSelector && !state.detail) openDetail(state.detailSelector, true); return browse();
      },
      deactivate: deactivate, dispose: function () { deactivate(); state.disposed = true; },
      epoch: function () { return lifecycle; } };
  }

  var context = {}, controller = null, view = null, nav = null, ownedView = false, ownedTab = false, ui = {}, returnFocusKey = "";
  function el(tag, cls, value) {
    var node = document.createElement(tag); if (cls) node.className = cls;
    if (value != null) node.textContent = text(value); return node;
  }
  function append(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function run(task) { Promise.resolve().then(task).catch(function () { /* Controller/form presents errors. */ }); }
  function button(label, action, disabled, cls) {
    var node = el("button", "library-button" + (cls ? " " + cls : ""), label); node.type = "button"; node.disabled = !!disabled;
    node.addEventListener("click", function () { run(action); }); return node;
  }
  function labelField(parent, caption, control) {
    var label = el("label", "library-field"); append(label, el("span", "library-label", caption), control); parent.appendChild(label); return control;
  }
  function badge(value) { return el("span", "library-badge", value); }
  function appendSourceLink(parent, source, fallbackProvider) {
    var url = sourceLink(source, fallbackProvider); if (!url) return;
    var link = el("a", "library-source-link", url); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", "打开 " + providerLabel(source.provider || fallbackProvider) + " 作品条目"); parent.appendChild(link);
  }
  function doubanRating(rating) {
    rating = rating && typeof rating === "object" ? rating : {};
    var score = doubanScore(rating), label = "豆瓣评分 " + (score == null ? "暂无评分" : score.toFixed(1) + " / 10");
    if (score != null && Number.isSafeInteger(rating.votes) && rating.votes >= 0) label += " · " + rating.votes + "人评价";
    var box = el("div", "library-douban-rating library-meta"), value = el("span", "", label);
    if (typeof rating.fetched_at === "string" && rating.fetched_at.trim()) box.title = "更新于 " + rating.fetched_at.trim();
    box.appendChild(value);
    var url = sourceLink({ provider: "douban", external_id: rating.external_id, source_url: rating.source_url });
    if (url) {
      var link = el("a", "library-source-link", "豆瓣条目"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", "打开豆瓣电影评分来源条目"); box.appendChild(link);
    }
    return box;
  }
  function option(parent, value, caption) { var node = el("option", "", caption); node.value = text(value); parent.appendChild(node); }
  function cover(item) {
    var box = el("div", "library-cover"), url = localCover(item.cover_url || item.cover && item.cover.url);
    var placeholder = el("span", "library-cover-placeholder");
    append(placeholder, el("span", "library-cover-initial", text(item.name || item.title || "作").trim().slice(0, 1)), el("span", "library-cover-caption", "暂无封面"));
    if (item.domain_label || item.domain) placeholder.appendChild(el("span", "library-cover-kind", item.domain_label || item.domain));
    placeholder.setAttribute("aria-hidden", "true"); box.appendChild(placeholder);
    if (url) {
      var image = el("img"); image.src = url; image.alt = titleOf(item) + "封面"; image.loading = "lazy"; image.decoding = "async"; image.width = 160; image.height = 224;
      image.addEventListener("error", function () { image.remove(); }); box.appendChild(image);
    }
    return box;
  }
  function titleOf(item) { return text(item.name || item.title || attribute(item.record, "name", "未命名作品")); }
  function keyOf(item) { return text(item.id || item.ref && item.ref.yaml_source_rel + "#" + item.ref.index_in_file); }
  function cardPopover(anchor, title, lines) {
    global.NimdaLibraryCardPopover.open(anchor, { title: title, lines: lines });
  }
  function appendCardItem(row, node) {
    if (row.children.length) {
      var separator = el("span", "library-card-dot", "·"); separator.setAttribute("aria-hidden", "true"); row.appendChild(separator);
    }
    row.appendChild(node);
  }
  function cardFacts(item) {
    var facts = el("div", "library-meta library-card-facts"), lines = [];
    function fact(label, value, priority, cls, tooltip) {
      if (!value) return;
      var node = el("span", "library-card-fact" + (cls ? " " + cls : ""), value);
      node.setAttribute("data-fact-priority", priority); node.title = tooltip || label + "：" + value;
      appendCardItem(facts, node); lines.push(node.title);
    }
    fact("国家", item.country_label || item.country, 10);
    fact("年份", item.year, 90);
    var media = item.domain_label || item.domain, releaseType = item.release_type_label || item.release_type;
    fact(releaseType ? "类型" : "媒体", releaseType || media, 30);
    if (releaseType && media) lines.push("媒体：" + media);
    var rating = item.ratings && item.ratings.douban, score = doubanScore(rating);
    if (score != null) fact("评分", "豆瓣 " + score.toFixed(1), 100, "library-card-rating",
      "豆瓣评分 " + score.toFixed(1) + " / 10" + (Number.isSafeInteger(rating.votes) ? " · " + rating.votes + " 人评价" : "") + (rating.fetched_at ? " · 更新于 " + rating.fetched_at : ""));
    if (!facts.children.length) fact("分类", "未分类", 10);
    array(item.classifications).forEach(function (tag) { if (tag.name || tag.label) { lines.push("分类：" + (tag.name || tag.label)); facts.setAttribute("data-extra-facts", "true"); } });
    var more = button("…", function () { cardPopover(more, titleOf(item), lines); }, false, "library-facts-more");
    more.hidden = true; more.title = "查看完整作品信息"; more.setAttribute("aria-label", "查看" + titleOf(item) + "的完整作品信息");
    more.setAttribute("aria-haspopup", "dialog"); more.setAttribute("aria-expanded", "false"); facts.appendChild(more);
    return facts;
  }
  function cardResources(item) {
    var config = context.config || {}, sourceRows = array(item.press_summary || item.resources || item.press), rows = global.NimdaPressSummary.cardFormats(sourceRows, {
      formatOrder: (config.enum_options || {}).press_format, labels: config.enum_labels, groupRegistry: config.group_registry,
    });
    var box = el("div", "library-card-resources"), sections = [];
    if (!rows.length) box.appendChild(el("span", "library-resource-empty", "暂无资源"));
    rows.forEach(function (row) {
      var chip = el("button", "library-press-chip", row.label);
      chip.type = "button"; chip.setAttribute("data-ui-custom", "true");
      chip.setAttribute("data-card-tooltip", row.tooltip_lines.join("\n"));
      chip.setAttribute("aria-label", row.label + "：" + row.tooltip_lines.join("；")); chip.setAttribute("aria-haspopup", "dialog"); chip.setAttribute("aria-expanded", "false");
      chip.addEventListener("click", function () { global.NimdaLibraryCardPopover.open(chip, { title: titleOf(item) + " · " + row.label, sections: row.sections }); });
      sections = sections.concat(row.sections); appendCardItem(box, chip);
    });
    if (rows.length) {
      var more = button("+" + rows.length, function () { global.NimdaLibraryCardPopover.open(more, { title: titleOf(item) + " · 全部资源", sections: sections }); }, false, "library-press-more");
      more.hidden = true; more.setAttribute("data-ui-custom", "true"); more.setAttribute("data-card-tooltip", rows.map(function (row) { return row.label; }).join("\n"));
      more.setAttribute("aria-haspopup", "dialog"); more.setAttribute("aria-expanded", "false");
      more.setAttribute("aria-label", "查看" + titleOf(item) + "的全部压制格式");
      box.appendChild(more);
    }
    return box;
  }
  function renderCard(item) {
    function openWork() {
      returnFocusKey = keyOf(item); controller.openDetail(item);
    }
    var card = el("article", "library-card"), open = button(titleOf(item), openWork, false, "library-card-link");
    var coverLink = button("", openWork, false, "library-card-cover-link");
    coverLink.setAttribute("data-ui-custom", "true"); coverLink.setAttribute("aria-label", "查看" + titleOf(item));
    coverLink.appendChild(cover(item));
    ui.cardButtons[keyOf(item)] = open;
    var info = el("div", "library-card-info"), heading = el("h3");
    open.title = titleOf(item); heading.appendChild(open);
    append(info, heading, cardFacts(item), cardResources(item));
    if (item.summary) info.appendChild(el("p", "library-card-summary", item.summary));
    var tags = el("div", "library-badges"); array(item.classifications).slice(0, 3).forEach(function (tag) { tags.appendChild(badge(tag.name || tag.label)); });
    info.appendChild(tags); append(card, coverLink, info); return card;
  }
  function browseFilterKey(field, value) {
    var result = text(value).normalize("NFKC").trim().toLowerCase();
    return field === "press_group" && /^-+$/.test(result) ? "__none__" : result;
  }
  function quickFilterOptions(state, definition) {
    // The editor and filters share /api/config enums, including per-file enum
    // additions. Facets add counts and retain legacy values for exact filtering.
    var config = context.config || {}, labels = (config.enum_labels || {})[definition.field] || {}, rows = [], known = Object.create(null);
    function add(value, label, count) {
      var key = browseFilterKey(definition.field, value); if (!key) return;
      var existing = known[key];
      if (existing) { if (count != null) existing.count = count; return; }
      var entry = { value: key === "__none__" ? "__none__" : text(value), label: key === "__none__" ? "----（未填写）" : text(label || value), count: count };
      known[key] = entry; rows.push(entry);
    }
    array((config.enum_options || {})[definition.field]).forEach(function (value) { add(value, labels[value]); });
    array(state.facets[definition.facet]).forEach(function (row) { add(row.value, labels[row.value] || row.label, row.count); });
    var selected = state.filters[definition.field];
    if (selected) add(selected, labels[selected]);
    return rows;
  }
  function hasBrowseFilters(state) {
    return ["query", "domain", "year", "classification_id"].concat(QUICK_FILTERS.map(function (definition) { return definition.field; }))
      .some(function (field) { return !!state.filters[field]; });
  }
  function markActiveFilter(control, value) {
    control.setAttribute("data-filter-active", text(value).trim() ? "true" : "false");
  }
  function syncSidebar() {
    var collapsed = !!ui.sidebarCollapsed, label = collapsed ? "展开筛选栏" : "收起筛选栏";
    ui.columns.setAttribute("data-sidebar-collapsed", collapsed ? "true" : "false");
    ui.sidebarContent.hidden = collapsed;
    ui.sidebarToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    ui.sidebarToggle.setAttribute("aria-label", label); ui.sidebarToggle.title = label;
    ui.sidebarToggleIcon.textContent = collapsed ? "‹" : "›";
    var count = ["year", "classification_id"].concat(QUICK_FILTERS.map(function (definition) { return definition.field; }))
      .filter(function (field) { return !!controller.state.filters[field]; }).length;
    ui.sidebarCount.textContent = text(count); ui.sidebarCount.hidden = !count;
    ui.sidebarCount.setAttribute("aria-label", count + " 项筛选条件");
    ui.sidebarToggle.setAttribute("data-filter-active", count ? "true" : "false");
    if (ui.cardLayout) ui.cardLayout.refresh();
  }
  function mountBrowseSidebar(sidebar) {
    ui.sidebarCollapsed = !!(global.NimdaCommon.readPreference && global.NimdaCommon.readPreference(SIDEBAR_PREFERENCE) === "true");
    ui.sidebarContent = el("div", "library-sidebar-content"); ui.sidebarContent.id = "library-sidebar-content";
    var header = el("div", "library-sidebar-header");
    ui.resetFilters = button("清除筛选", function () { return controller.setFilters({ query: "", domain: "", year: "", classification_id: "", country: "", release_type: "", press_group: "", press_format: "" }); }, true, "library-text-button");
    append(header, el("h2", "library-section-title", "筛选作品"), ui.resetFilters); ui.sidebarContent.appendChild(header);
    function section(title) {
      var node = el("section", "library-sidebar-section"); node.appendChild(el("h3", "library-section-title", title));
      ui.sidebarContent.appendChild(node); return node;
    }
    function quickFilters(parent, fields) {
      QUICK_FILTERS.filter(function (definition) { return fields.indexOf(definition.field) >= 0; }).forEach(function (definition) {
        var control = labelField(parent, definition.label, el("select")); control.id = "library-filter-" + definition.field;
        control.setAttribute("aria-label", definition.label); ui.quickFilters[definition.field] = control;
        control.addEventListener("change", function () { var patch = {}; patch[definition.field] = control.value; controller.setFilters(patch); });
      });
    }
    ui.quickFilters = {}; quickFilters(section("作品信息"), ["country", "release_type"]);
    var time = section("时间与分类");
    ui.year = labelField(time, "年份", el("select")); ui.year.id = "library-filter-year"; ui.year.setAttribute("aria-label", "年份");
    ui.year.addEventListener("change", function () { controller.setFilters({ year: ui.year.value }); });
    ui.series = labelField(time, "系列", el("select")); ui.series.id = "library-filter-series"; ui.series.setAttribute("aria-label", "系列");
    ui.series.addEventListener("change", function () { controller.setFilters({ classification_id: ui.series.value }); });
    quickFilters(section("本地资源"), ["press_group", "press_format"]);
    var rail = el("div", "library-sidebar-rail");
    ui.sidebarToggle = button("", function () {
      ui.sidebarCollapsed = !ui.sidebarCollapsed; syncSidebar();
      if (global.NimdaCommon.writePreference) global.NimdaCommon.writePreference(SIDEBAR_PREFERENCE, text(ui.sidebarCollapsed));
    }, false, "library-sidebar-toggle");
    ui.sidebarToggle.setAttribute("aria-controls", ui.sidebarContent.id);
    ui.sidebarToggleIcon = el("span", "library-sidebar-toggle-icon"); ui.sidebarToggleIcon.setAttribute("aria-hidden", "true");
    ui.sidebarCount = el("span", "library-sidebar-filter-count");
    append(ui.sidebarToggle, ui.sidebarToggleIcon, el("span", "library-sidebar-toggle-label", "筛选"), ui.sidebarCount);
    rail.appendChild(ui.sidebarToggle); append(sidebar, ui.sidebarContent, rail); syncSidebar();
  }
  function refreshFacets(state) {
    clear(ui.domains);
    var domains = array(state.facets.domains), all = button("全部", function () { controller.setFilters({ domain: "" }); });
    all.setAttribute("aria-pressed", state.filters.domain ? "false" : "true"); ui.domains.appendChild(all);
    domains.forEach(function (row) {
      var value = text(row.value == null ? row.id : row.value);
      var node = button((row.label || row.name || value) + (row.count == null ? "" : " " + row.count), function () { controller.setFilters({ domain: value }); });
      node.setAttribute("aria-pressed", state.filters.domain === value ? "true" : "false"); ui.domains.appendChild(node);
    });
    clear(ui.year); option(ui.year, "", "所有年份"); array(state.facets.years).forEach(function (row) { var value = typeof row === "object" ? row.value || row.year : row; option(ui.year, value, value); }); ui.year.value = state.filters.year;
    clear(ui.series); option(ui.series, "", "所有系列"); array(state.facets.classifications).filter(function (row) { return (row.type || row.kind) === "series"; }).forEach(function (row) { option(ui.series, row.id, (row.name || row.label) + (row.count == null ? "" : " · " + row.count)); }); ui.series.value = state.filters.classification_id;
    markActiveFilter(ui.search, state.filters.query);
    markActiveFilter(ui.year, state.filters.year);
    markActiveFilter(ui.series, state.filters.classification_id);
    clear(ui.activeFilters);
    var active = [["query", state.filters.query], ["year", state.filters.year && state.filters.year + " 年"], ["classification_id", (state.classifications.find(function (row) { return row.id === state.filters.classification_id; }) || {}).name]];
    QUICK_FILTERS.forEach(function (definition) {
      var control = ui.quickFilters[definition.field], rows = quickFilterOptions(state, definition), selected = null;
      clear(control); option(control, "", "全部" + definition.label);
      rows.forEach(function (row) {
        option(control, row.value, row.label + (row.count == null ? "" : " · " + row.count));
        if (browseFilterKey(definition.field, state.filters[definition.field]) === browseFilterKey(definition.field, row.value)) selected = row;
      });
      control.value = selected ? selected.value : "";
      markActiveFilter(control, control.value);
      if (selected) active.push([definition.field, definition.label + "：" + selected.label]);
    });
    active.forEach(function (pair) {
      if (!pair[1]) return;
      var remove = button(pair[1] + " ×", function () { var patch = {}; patch[pair[0]] = ""; return controller.setFilters(patch); }, false, "library-filter-chip");
      remove.setAttribute("aria-label", "清除筛选：" + pair[1]); remove.setAttribute("data-filter-active", "true"); ui.activeFilters.appendChild(remove);
    });
    ui.resetFilters.disabled = !hasBrowseFilters(state);
    syncSidebar();
  }
  function renderBrowse(state) {
    ui.count.textContent = state.loading && !state.loaded ? "加载中…" : state.total + " 部作品";
    ui.results.setAttribute("aria-busy", state.loading ? "true" : "false");
    // The input is a draft, while filters.query is the last submitted query.
    // Async loading/detail updates must not discard text being typed meanwhile.
    if (ui.search.value !== state.searchDraft) ui.search.value = state.searchDraft;
    ui.sort.value = state.filters.sort;
    ui.refresh.disabled = state.loading || state.busy; ui.add.disabled = state.busy;
    ui.initialize.disabled = state.busy; ui.maintenance.hidden = !state.identityMissing;
    ui.cards.setAttribute("aria-pressed", state.mode === "cards" ? "true" : "false"); ui.list.setAttribute("aria-pressed", state.mode === "list" ? "true" : "false");
    ui.cardSizes.hidden = state.mode !== "cards";
    refreshFacets(state); clear(ui.pagination); clear(ui.identity);
    // Keep the current page visible while a new query is running. This avoids
    // recreating every image and moving focus for a loading-only state change.
    if (!state.loading || !state.loaded) { disposeCardLayout(); clear(ui.results); ui.cardButtons = Object.create(null); ui.cardGrid = null;
    if (state.error) {
      var recovery = el("div", "library-recovery");
      recovery.appendChild(button("重试", function () { controller.browse(); }, state.loading)); ui.results.appendChild(recovery);
    } else if (state.loading && !state.loaded) ui.results.appendChild(el("p", "library-empty", "正在读取本地作品…"));
    else if (!state.items.length) ui.results.appendChild(el("p", "library-empty", hasBrowseFilters(state) ? "没有符合筛选条件的作品。" : "本地作品库还没有作品。"));
    else {
      var grid = el("div", "library-grid" + (state.mode === "list" ? " is-list" : "")); state.items.forEach(function (item) { grid.appendChild(renderCard(item)); }); ui.results.appendChild(grid);
      ui.cardGrid = grid; updateCardSize();
    }
    }
    // Cached cards remain interactive while a refresh is pending, including
    // returning to this tab after its observers have been disposed.
    if (ui.cardGrid && !state.detailSelector) {
      var gridClass = "library-grid" + (state.mode === "list" ? " is-list" : "");
      if (ui.cardGrid.className !== gridClass) { ui.cardGrid.className = gridClass; updateCardSize(); }
      else if (!ui.cardLayout) updateCardSize();
    }
    var pages = Math.max(1, Math.ceil(state.total / state.filters.page_size));
    ui.pagination.appendChild(button("‹ 上一页", function () { controller.setFilters({ page: state.filters.page - 1 }); }, state.loading || state.filters.page <= 1));
    var pageNumbers = [1, pages]; for (var page = Math.max(1, state.filters.page - 2); page <= Math.min(pages, state.filters.page + 2); page++) pageNumbers.push(page);
    var previousPage = 0;
    pageNumbers.filter(function (number, index, numbers) { return numbers.indexOf(number) === index; }).sort(function (a, b) { return a - b; }).forEach(function (number) {
      if (previousPage && number - previousPage > 1) ui.pagination.appendChild(el("span", "library-page-gap", "…"));
      var pageButton = button(text(number), function () { controller.setFilters({ page: number }); }, state.loading || number === state.filters.page);
      pageButton.setAttribute("aria-label", "第 " + number + " 页"); if (number === state.filters.page) pageButton.setAttribute("aria-current", "page");
      ui.pagination.appendChild(pageButton); previousPage = number;
    });
    ui.pagination.appendChild(button("下一页 ›", function () { controller.setFilters({ page: state.filters.page + 1 }); }, state.loading || state.filters.page >= pages));
    if (state.identityPreview) {
      var preview = state.identityPreview, count = preview.count == null ? array(preview.edits).length : preview.count;
      array(preview.issues).forEach(function (issue) { ui.identity.appendChild(el("p", "library-error", issue.message || issue.reason)); });
      append(ui.identity, el("p", "", "将为 " + count + " 条记录补充本地稳定标识，不修改作品名称或目录。"),
        button("确认初始化", function () { return controller.applyIdentities(); }, state.busy || !count || array(preview.issues).some(function (issue) { return issue.blocking !== false; }), "is-primary"),
        button("取消", function () { state.identityPreview = null; render(); }, state.busy));
      if (state.identityMissing > count) ui.identity.appendChild(el("p", "library-meta", "每次最多处理 200 条；完成后可继续初始化余下记录。"));
    }
  }
  function workFacts(detail, item) {
    var record = detail.record || item.record || {}, dates = attribute(record, "date", {}), meta = el("dl", "library-facts");
    [["分类", item.domain_label || item.domain], ["类型", item.release_type_label || item.release_type],
      ["开播", dateLabel(item.begin_date || dates.start)], ["完结", dateLabel(item.end_date || dates.end)]].forEach(function (pair) {
      if (pair[1]) append(meta, el("dt", "", pair[0]), el("dd", "", pair[1]));
    });
    return meta;
  }
  function renderOverview(parent, detail, item) {
    var record = detail.record || item.record || {};
    parent.appendChild(el("h2", "library-section-title", "作品简介"));
    var metadata = detail.metadata || record.metadata || {}, summary = metadata.summary || item.summary || attribute(record, "summary", "");
    if (metadata.ratings && Object.prototype.hasOwnProperty.call(metadata.ratings, "douban")) parent.appendChild(doubanRating(metadata.ratings.douban));
    parent.appendChild(el("p", "library-summary", summary || "暂无简介。"));
    var aliases = array(item.aliases || metadata.aliases); if (aliases.length) parent.appendChild(el("p", "library-meta", "别名：" + aliases.map(function (row) { return typeof row === "object" ? row.name || row.value : row; }).join(" / ")));
    if (record.id || item.id) {
      var identity = el("details", "library-data-identity"); append(identity, el("summary", "", "数据标识"), el("p", "library-path", record.id || item.id)); parent.appendChild(identity);
    }
  }
  function renderResources(parent, detail, item) {
    var rows = array(detail.resources || item.resources || item.press), collection = attribute(detail.record || item.record, "collection-type", {});
    var workPath = collection.path || item.path;
    var tools = el("div", "library-actions");
    tools.appendChild(button("批量关联资源目录", function () { openResourceEditor(detail); }, controller.state.busy || !detail.record));
    parent.appendChild(tools);
    if (workPath) {
      var directory = el("p", "library-work-directory"), rootPath = el("span", "library-resource-path", workPath);
      rootPath.title = workPath; append(directory, el("span", "library-meta", "作品目录"), rootPath); parent.appendChild(directory);
    }
    if (!rows.length) { parent.appendChild(el("p", "library-empty", "尚未绑定本地资源。")); return; }
    var hasTitles = rows.some(function (row) { return text(row.continuation_title || row.title).trim(); }), numbering = detail.chapter_numbering || {};
    var hasNumbering = !!numbering.has_continuous || rows.some(function (row) { return numberingMode(row) === "continuous"; });
    var table = el("div", "library-resource-table" + (hasTitles ? " has-titles" : "") + (hasNumbering ? " has-numbering" : ""));
    table.setAttribute("role", "table"); table.setAttribute("aria-label", "收集资源");
    var header = el("div", "library-resource-row library-resource-head"); header.setAttribute("role", "row");
    var columns = ["压制格式", "压制组"];
    if (hasTitles) columns.push("分段标题");
    columns.push("目录", "状态", "播放资源", "操作");
    columns.forEach(function (name) { var cell = el("span", "", name); cell.setAttribute("role", "columnheader"); header.appendChild(cell); });
    table.appendChild(header);
    function cell(className, value) {
      var node = el("span", className, value); node.title = value; node.setAttribute("role", "cell"); return node;
    }
    rows.forEach(function (row, resourceIndex) {
      var box = el("div", "library-resource-row"); box.setAttribute("role", "row");
      var group = text(row.press_group).trim();
      append(box, cell("library-resource-format", text(row.press_format).trim() || "—"),
        cell("library-resource-group", !group || group === "----" ? "—" : group));
      if (hasTitles) box.appendChild(cell("library-resource-title", text(row.continuation_title || row.title).trim() || "—"));
      var resourcePath = row.resolved_path || row.path || row.press_path || "未绑定目录";
      var statusLabel = row.state_label || row.status_label || row.state || row.status || (!row.press_path ? "未绑定" : "");
      var playback = el("div", "library-resource-playback"); playback.setAttribute("role", "cell");
      var draftMode = controller.state.playbackNumberingDrafts[resourceIndex] || numberingMode(row), modeChanged = draftMode !== numberingMode(row);
      var savedNumberingLabel = numberingMode(row) === "continuous" ? "续接集数" : "本作集数";
      if (hasNumbering) {
        var numberingSelect = el("select", "library-resource-numbering"); numberingSelect.id = "library-resource-numbering-" + resourceIndex;
        numberingSelect.setAttribute("aria-label", "资源 " + (resourceIndex + 1) + " 的文件集数编号");
        [["local", "本作" + (text(numbering.local_start) ? "(" + text(numbering.local_start) + "起)" : "")], ["continuous", "续接(" + (numbering.has_continuous ? text(numbering.continuous_start) + "起" : "不可用") + ")"]].forEach(function (entry) {
          var option = el("option", "", entry[1]); option.value = entry[0]; option.disabled = entry[0] === "continuous" && !numbering.has_continuous; numberingSelect.appendChild(option);
        });
        numberingSelect.value = draftMode; numberingSelect.disabled = controller.state.busy || !workPath || !text(row.press_path).trim();
        numberingSelect.title = "已保存：" + savedNumberingLabel + "；按资源文件实际集数选择，点击按钮后应用并重新识别";
        numberingSelect.addEventListener("change", function () { if (controller.state.detail === detail) controller.setPlaybackNumbering(resourceIndex, numberingSelect.value); });
        playback.appendChild(numberingSelect);
      }
      var currentPlayback = row.is_playback_resource && !modeChanged;
      var playbackLabel = currentPlayback ? hasNumbering ? "当前播放" : "当前播放资源" : row.is_playback_resource ? "应用" : hasNumbering ? "设为播放" : "设为播放资源";
      var selectPlayback = button(playbackLabel, function () {
        if (!currentPlayback && controller.state.detail === detail) return controller.selectPlaybackResource(resourceIndex, hasNumbering ? draftMode : undefined);
      }, currentPlayback || controller.state.busy || !workPath || !text(row.press_path).trim() || hasNumbering && draftMode === "continuous" && !numbering.has_continuous,
      "library-select-playback" + (row.is_playback_resource ? " library-playback-current" : ""));
      selectPlayback.title = row.is_playback_resource ? "当前播放资源；已保存：" + savedNumberingLabel + (modeChanged ? "；应用编号并重新识别文件" : "") : "设为播放资源并识别文件";
      selectPlayback.setAttribute("aria-label", selectPlayback.title); playback.appendChild(selectPlayback);
      var actions = el("div", "library-resource-actions"); actions.setAttribute("role", "cell");
      append(box, cell("library-resource-path", resourcePath), cell("library-resource-state", statusLabel), playback, actions);
      if (workPath && row.press_path) {
        actions.appendChild(button("打开目录", function () { return controller.openResource(workPath, row.press_path); }, controller.state.busy || controller.state.resourceOpening));
      }
      table.appendChild(box);
    });
    parent.appendChild(table);
  }
  function renderChapters(parent, detail, item) {
    if (global.NimdaChapterPlayer) {
      var owner = controller, player;
      player = global.NimdaChapterPlayer.mount(parent, { detail: detail,
        request: function (url, options) { return (context.fetchJson || global.NimdaCommon.fetchJson)(url, options); },
        setStatus: context.setStatus,
        onChooseResource: function () { if (controller === owner && ui.chapterPlayer === player) owner.setDetailTab("resources"); },
        onSaved: function (result) {
          if (controller === owner && ui.chapterPlayer === player) return owner.acceptPlaybackSave(detail, result);
        } });
      ui.chapterPlayer = player;
      return;
    }
    var rows = array(detail.chapters || item.chapters || (detail.record || {}).chapters);
    if (!rows.length) { parent.appendChild(el("p", "library-empty", "暂无章节信息。")); return; }
    var list = el("ol", "library-chapters"); rows.forEach(function (row, index) {
      var titleDisplay = global.NimdaChapterTitles.display(row, detail.chapter_title_preference);
      var li = el("li", "library-chapter"), title = el("span", "", titleDisplay.text); title.title = titleDisplay.tooltip;
      var number = el("span", "library-meta library-chapter-number", global.NimdaChapterNumbers.label(row, index)); number.title = global.NimdaChapterNumbers.title(row, index);
      li.appendChild(number);
      li.appendChild(title);
      if (row.air_date || row.date || row.airdate) li.appendChild(el("span", "library-meta", dateLabel(row.air_date || row.date || row.airdate))); list.appendChild(li);
    }); parent.appendChild(list);
  }
  function renderClassifications(parent, detail, item) {
    var tags = array(detail.classifications || item.classifications), current = el("div", "library-badges");
    tags.forEach(function (tag) {
      var chip = el("span", "library-classification-chip"); chip.appendChild(badge(((tag.kind || tag.type) === "series" ? "系列 · " : "") + (tag.name || tag.label)));
      chip.appendChild(button("移除", function () { return controller.assignClassification(tag.value_id || tag.id, "remove"); }, controller.state.busy)); current.appendChild(chip);
    });
    parent.appendChild(current); if (!tags.length) parent.appendChild(el("p", "library-empty", "尚未添加分类或系列标签。"));
    var definitions = controller.state.classifications.filter(function (row) { return (row.type || row.kind) === "series"; });
    var available = definitions.filter(function (row) { return !tags.some(function (tag) { return (tag.value_id || tag.id) === row.id; }); });
    var tools = el("div", "library-classification-tools"), select = labelField(tools, "关联已有系列", el("select"));
    option(select, "", "选择系列"); available.forEach(function (row) { option(select, row.id, row.name || row.label); });
    var assign = button("关联作品", function () { return controller.assignClassification(select.value, "assign"); }, true);
    select.addEventListener("change", function () { assign.disabled = !select.value || controller.state.busy; }); tools.appendChild(assign); parent.appendChild(tools);
    var form = el("form", "library-classification-tools"), name = labelField(form, "新建系列", el("input")); name.placeholder = "系列名称"; name.maxLength = 200; name.required = true;
    var create = el("button", "library-button", "创建系列"); create.type = "submit"; create.disabled = controller.state.busy; form.appendChild(create);
    form.addEventListener("submit", function (event) { event.preventDefault(); run(function () { return controller.saveClassification(name.value); }); }); parent.appendChild(form);
    if (definitions.length) {
      var management = el("details", "library-series-management"); management.appendChild(el("summary", "", "重命名系列"));
      var renameForm = el("form", "library-classification-tools"), renameSelect = labelField(renameForm, "系列", el("select")), renameInput = labelField(renameForm, "名称", el("input"));
      definitions.forEach(function (row) { option(renameSelect, row.id, row.name); }); renameInput.value = definitions[0].name; renameInput.maxLength = 200; renameInput.required = true;
      renameSelect.addEventListener("change", function () { var selected = definitions.find(function (row) { return row.id === renameSelect.value; }); renameInput.value = selected ? selected.name : ""; });
      var renameButton = el("button", "library-button", "保存名称"); renameButton.type = "submit"; renameButton.disabled = controller.state.busy; renameForm.appendChild(renameButton);
      renameForm.addEventListener("submit", function (event) { event.preventDefault(); run(function () { return controller.saveClassification(renameInput.value, renameSelect.value); }); }); management.appendChild(renameForm); parent.appendChild(management);
    }
  }
  function renderProvider(parent, state, detail, item) {
    var sources = sourceRows(detail), provider = state.provider, locked = state.busy || provider.loading, policy = sourcePolicy(detail);
    if (provider.guidance) { var guidance = el("p", "library-provider-guidance", provider.guidance); guidance.setAttribute("role", "status"); parent.appendChild(guidance); }
    var sourceToolbar = el("div", "library-source-toolbar"), sourcesHeading = el("h3", "library-section-title", "当前来源页面"); sourcesHeading.id = "library-source-choices"; sourcesHeading.tabIndex = -1;
    append(sourceToolbar, sourcesHeading, button("添加来源", function () { controller.beginSourceSelection(); }, locked)); parent.appendChild(sourceToolbar);
    var defaultProviders = [policy.primary_provider].concat(array(policy.supplement_providers)).filter(Boolean);
    parent.appendChild(el("p", "library-meta", policy.custom ? "手动来源" : "默认：" + defaultProviders.map(providerLabel).join(" + ")));
    var missingProviders = policy.custom ? [] : Array.isArray(policy.missing_providers) ? policy.missing_providers : defaultProviders.filter(function (value) { return !sources.some(function (source) { return source.provider === value; }); });
    if (missingProviders.length) {
      var defaults = el("div", "library-source-defaults");
      missingProviders.forEach(function (value) { defaults.appendChild(button("选择 " + providerLabel(value) + (value === policy.primary_provider ? " 主来源" : " 补充来源"), function () { controller.beginSourceSelection(null, value, value === policy.primary_provider ? "primary" : "supplement"); }, locked)); });
      parent.appendChild(defaults);
    }
    sources.forEach(function (source, index) {
      var row = el("article", "library-resource library-source-card" + (source.enabled === false ? " is-disabled" : "")), title = el("div", "library-source-title");
      append(title, el("h3", "", sourceTitle(source)), badge(providerLabel(source.provider || source.source || "来源")), badge(source.role === "supplement" ? "补充来源" : "主来源"), badge(!supportedProvider(source.provider) ? "历史来源 · 只读" : source.enabled === false ? "已停用" : "已启用")); row.appendChild(title);
      appendSourceLink(row, source);
      if (source.fetched_at || source.updated_at) row.appendChild(el("p", "library-meta", "更新于 " + text(source.updated_at || source.fetched_at))); parent.appendChild(row);
      if (source.provider === "tmdb" && source.season_number != null) row.appendChild(badge("第 " + source.season_number + " 季"));
      else if (supportsChapters(source.provider) && source.episode_range) row.appendChild(badge("正片第 " + source.episode_range.start + "–" + source.episode_range.end + " 集"));
      else if (supportsChapters(source.provider) && source.scope) row.appendChild(badge("范围：" + source.scope));
      else row.appendChild(badge("完整条目"));
      if (supportedSource(source)) {
        var actions = el("div", "library-source-actions");
        actions.appendChild(button("刷新此来源数据", function () { return controller.refreshProvider(source); }, locked || source.enabled === false));
        if (source.provider === "douban") actions.appendChild(button("浏览器读取评分", function () { return controller.startRatingBrowser(source.subject_id || source.external_id, source); }, locked || source.enabled === false || provider.browser && provider.browser.polling));
        if (source.binding_id) {
          actions.appendChild(button("重新选择", function () { controller.beginSourceSelection(source); }, locked));
          actions.appendChild(button(source.enabled === false ? "启用" : "停用", function () { return controller.updateSourceBinding(source, source.enabled === false ? "enable" : "disable"); }, locked));
          var role = el("select"); if (source.provider !== "douban") option(role, "primary", "主来源"); option(role, "supplement", "补充来源"); role.value = source.provider === "douban" ? "supplement" : source.role || "primary"; role.disabled = locked; role.id = "library-source-role-" + index;
          role.setAttribute("aria-label", sourceTitle(source) + " 的来源角色");
          role.addEventListener("change", function () { run(function () { return controller.updateSourceBinding(source, "set-role", role.value); }); }); actions.appendChild(role);
          actions.appendChild(button("移除关联", function () { controller.confirmSourceRemoval(source.binding_id); }, locked));
        }
        if (source.provider === "bangumi" || source.provider === "tmdb") actions.appendChild(button("更新此来源封面", function () { return controller.previewCover(source.subject_id || source.external_id, source); }, locked || source.enabled === false)); row.appendChild(actions);
        if (source.binding_id && provider.removeBindingId === source.binding_id) {
          var removal = el("div", "library-source-removal"); append(removal, el("p", "library-meta", "仅移除这条来源关联，保留已经保存的作品资料。"),
            button("确认移除", function () { return controller.updateSourceBinding(source, "remove"); }, locked), button("取消", function () { controller.confirmSourceRemoval(""); }, locked)); row.appendChild(removal);
        }
      }
    });
    if (!sources.length || provider.selectionOpen) {
    parent.appendChild(el("h3", "library-section-title library-source-editor-heading", provider.replaceBindingId ? "重新选择来源条目" : "添加来源条目"));
    var form = el("form", "library-provider-search"), input = el("input"), sourceSelect = el("select");
    [["bangumi", "Bangumi"], ["tmdb", "TMDB · 电视剧"], ["wikipedia", "中文维基百科 · 补充"], ["douban", "豆瓣评分"]].forEach(function (pair) { option(sourceSelect, pair[0], pair[1]); });
    sourceSelect.value = provider.provider; sourceSelect.disabled = locked; sourceSelect.id = "library-provider-choice";
    sourceSelect.addEventListener("change", function () { controller.chooseProvider(sourceSelect.value); }); labelField(form, "数据来源", sourceSelect);
    input.type = "search"; input.value = provider.query; input.placeholder = provider.provider === "douban" ? "作品名、豆瓣搜索链接、条目链接或数字 ID" : provider.provider === "wikipedia" ? "中文作品名或维基条目链接" : "搜索 " + providerLabel(provider.provider) + " 作品名";
    input.addEventListener("input", function () { if (provider.provider === "douban") controller.setProviderQuery(input.value); else provider.query = input.value; });
    input.id = "library-provider-query";
    labelField(form, "查找来源条目", input); var search = el("button", "library-button", "搜索 " + providerLabel(provider.provider)); search.id = "library-provider-search"; search.disabled = state.busy || provider.loading; search.type = "submit";
    form.addEventListener("submit", function (event) { event.preventDefault(); if (!provider.loading && !state.busy) controller.searchProvider(input.value); });
    form.appendChild(search); parent.appendChild(form);
    if (provider.provider === "douban") {
      var externalSearch = el("a", "library-source-link library-douban-search-link", "在豆瓣打开搜索");
      externalSearch.href = doubanSearchUrl(provider.query); externalSearch.target = "_blank"; externalSearch.rel = "noopener noreferrer"; parent.appendChild(externalSearch);
      parent.appendChild(el("p", "library-meta", "支持关键词及豆瓣电影搜索链接；请根据类型、年份与描述核对同名作品后再预览。也可粘贴条目链接或数字 ID。"));
      if (provider.unavailable || provider.error) parent.appendChild(el("p", "library-meta", "豆瓣搜索暂不可用，可在豆瓣打开搜索后粘贴作品条目链接重试。"));
      if (provider.searchWarnings.length) parent.appendChild(el("p", "library-meta", provider.searchWarnings.join("；")));
    }
    if (provider.provider === "tmdb") {
      var seasonRow = el("div", "library-provider-season"), season = el("input"); season.type = "text"; season.inputMode = "numeric"; season.value = provider.season; season.id = "library-provider-season"; season.disabled = state.busy;
      season.addEventListener("input", function () { controller.setProviderSeason(season.value); }); labelField(seasonRow, "同步季数（0 为特别篇）", season); parent.appendChild(seasonRow);
      var configHint = el("p", "library-meta", "TMDB 使用服务端访问令牌；配置说明见来源说明。"); parent.appendChild(configHint);
    }
    var rangeOptions = el("details", "library-provider-range"); rangeOptions.open = provider.rangeOpen || !!provider.rangeStart || !!provider.rangeEnd;
    rangeOptions.appendChild(el("summary", "", "限定正片集数（可选）"));
    rangeOptions.addEventListener("toggle", function () { provider.rangeOpen = rangeOptions.open; });
    var rangeFields = el("div", "library-range-fields"), rangeStart = labelField(rangeFields, "起始集", el("input")), rangeEnd = labelField(rangeFields, "结束集", el("input"));
    // Keep partial numeric input as text (e.g. "12.") until preview validation;
    // number inputs can silently turn invalid/partial values into empty strings.
    [rangeStart, rangeEnd].forEach(function (field) { field.type = "text"; field.inputMode = "decimal"; field.disabled = state.busy; field.placeholder = "留空"; });
    rangeStart.id = "library-episode-start"; rangeStart.value = provider.rangeStart; rangeEnd.id = "library-episode-end"; rangeEnd.value = provider.rangeEnd;
    function rangeChanged() { controller.setEpisodeRange(rangeStart.value, rangeEnd.value); }
    rangeStart.addEventListener("input", rangeChanged); rangeEnd.addEventListener("input", rangeChanged);
    rangeOptions.appendChild(rangeFields); if (supportsChapters(provider.provider)) parent.appendChild(rangeOptions);
    if (sources.length) parent.appendChild(button("取消选择", function () { controller.cancelSourceSelection(); }, locked));
    }
    var credits = el("details", "library-provider-credits"); credits.appendChild(el("summary", "", "来源说明"));
    var tmdbCredit = el("a"), tmdbLogo = el("img", "library-tmdb-logo"); tmdbLogo.src = "/features/catalog-library/tmdb-logo.svg"; tmdbLogo.alt = "The Movie Database (TMDB)"; tmdbLogo.loading = "lazy"; tmdbLogo.width = 160; tmdbLogo.height = 12;
    tmdbCredit.href = "https://www.themoviedb.org"; tmdbCredit.target = "_blank"; tmdbCredit.rel = "noopener noreferrer"; tmdbCredit.appendChild(tmdbLogo); credits.appendChild(tmdbCredit);
    credits.appendChild(el("p", "library-meta", "TMDB：在工作区 config/features/catalog-library/providers.local.yaml 的 tmdb.read_access_token 配置 API Read Access Token，或设置环境变量 TMDB_API_READ_ACCESS_TOKEN。不要将令牌提交到版本库。"));
    credits.appendChild(el("p", "library-meta", "This product uses the TMDB API but is not endorsed or certified by TMDB."));
    credits.appendChild(el("p", "library-meta", "中文维基百科用于补充中文名称与简介；原文链接及修订信息随来源保存。内容按维基百科相应许可使用。")); parent.appendChild(credits);
    credits.appendChild(el("p", "library-meta", "豆瓣仅补充作品评分与评价人数，不获取或修改人物、作品名称、简介、图片及章节。"));
    var wikiLicense = el("a", "library-source-link", "Wikipedia 文本：CC BY-SA 4.0（本地内容可能经编辑）"); wikiLicense.href = "https://creativecommons.org/licenses/by-sa/4.0/"; wikiLicense.target = "_blank"; wikiLicense.rel = "noopener noreferrer"; credits.appendChild(wikiLicense);
    if (provider.browser && global.NimdaRatingBrowser) global.NimdaRatingBrowser.render(parent, provider.browser, { step: controller.stepRatingBrowser, cancel: controller.cancelRatingBrowser });
    if (provider.loading && !provider.browser) parent.appendChild(el("p", "library-meta", state.peopleRetrying ? "正在重试所选失败项，已成功读取的资料保持不变…" : provider.mode === "sync" ? "正在刷新已启用来源并合并差异…" : provider.mode === "cover" ? "正在获取封面并保存到本地…" : provider.provider === "bangumi" ? "正在获取作品与人物资料…" : "正在获取作品资料…"));
    if (provider.error && provider.validation) { var error = el("p", "library-error", provider.error); error.setAttribute("role", "alert"); parent.appendChild(error); }
    if (provider.retry) parent.appendChild(button("重试", provider.retry, state.busy || provider.loading, "library-provider-retry"));
    provider.results.forEach(function (result) {
      var row = el("div", "library-provider-result"), previewButton = button("预览差异", function () {
        if (previewButton.isConnected === false || provider !== controller.state.provider || provider.results.indexOf(result) < 0) return;
        return controller.previewProvider(result.id);
      }, state.busy || provider.loading);
      previewButton.id = "library-provider-result-" + text(result.id);
      var resultInfo = el("div", "library-provider-result-info", (result.name_cn || result.name || result.title || "未命名") + " · #" + result.id);
      appendSourceLink(resultInfo, result, provider.provider);
      if (provider.provider === "douban") {
        var resultType = result.type || ({ movie: "电影", tv: "电视剧" })[result.media_type] || "", resultYear = result.year || dateLabel(result.date);
        if (resultType || resultYear) resultInfo.appendChild(el("p", "library-meta", [resultType, resultYear].filter(Boolean).join(" · ")));
        if (result.description) resultInfo.appendChild(el("p", "library-meta library-provider-result-description", text(result.description).slice(0, 500)));
      } else if (result.date) resultInfo.appendChild(el("span", "library-meta", dateLabel(result.date)));
      var resultActions = el("div", "library-provider-result-actions"); resultActions.appendChild(previewButton);
      if (provider.provider === "douban") resultActions.appendChild(button("浏览器读取评分", function () { if (provider === controller.state.provider && provider.results.indexOf(result) >= 0) return controller.startRatingBrowser(result.id); }, state.busy || provider.loading || provider.browser && provider.browser.polling));
      append(row, resultInfo, resultActions); parent.appendChild(row);
    });
    if (!provider.preview) return;
    var preview = provider.preview, differences = array(preview.diff || preview.diffs || preview.changes).filter(function (row) { return preview.provider !== "douban" || row.field === "metadata.ratings.douban"; }), panel = el("section", "library-sync-preview");
    var syncHeading = el("h3", "", provider.mode === "sync" ? "多来源差异预览 · 一次确认写入" : provider.mode === "cover" ? "确认更新封面" : "确认要写入的字段"); syncHeading.id = "library-sync-heading"; syncHeading.tabIndex = -1;
    var range = preview.episode_range, counts = preview.episode_counts || {}, scope = range ? "正片第 " + range.start + "–" + range.end + " 集" : "完整条目 · 全量章节";
    if (provider.mode === "sync") scope = "已按主 / 补充来源合并 · 人工修改默认保留 · 空值不清空已有资料";
    else if (provider.mode === "cover") scope = "仅更新本地封面 · 不修改作品信息和章节";
    else if (provider.provider === "tmdb") scope = "第 " + provider.season + " 季 · " + scope;
    else if (provider.provider === "wikipedia") scope = "中文资料补充 · 不替换章节与播放资源";
    else if (provider.provider === "douban") scope = "仅补充豆瓣评分与评价人数";
    if (provider.mode !== "cover" && supportsChapters(provider.provider) && counts.selected != null && counts.total != null) scope += " · " + counts.selected + " / " + counts.total + " 条章节";
    append(panel, syncHeading, el("p", "library-preview-scope", scope), el("p", "library-meta", provider.mode === "cover" ?
      "确认后更新本地封面，不修改其他字段。" : preview.provider === "douban" ? "只更新评分及评价人数；其他作品资料与人物关联保持不变。" : "别名会与已有内容合并去重，空字段默认采用；已有不同内容需确认，也可取消默认选择。"));
    appendSourceLink(panel, preview, provider.provider);
    if (provider.mode === "sync") {
      var outcomes = el("div", "library-source-outcomes");
      array(preview.sources).forEach(function (source) {
        var saved = sources.find(function (row) { return row.binding_id === source.binding_id; });
        outcomes.appendChild(el("p", "library-meta", providerLabel(source.provider) + (saved ? " · " + sourceTitle(saved) : "") + (source.status === "error" ? "：读取失败，保留原数据（详情见处理记录）" : "：已获取")));
      }); panel.appendChild(outcomes);
    }
    var aliasNotice = el("p", "library-provider-guidance library-automatic-aliases"); aliasNotice.setAttribute("role", "note"); panel.appendChild(aliasNotice);
    // Detailed provider failures live in the shared operation panel/log, not a
    // repeated per-person wall of warnings in the work preview.
    if (array(preview.warnings).length) panel.appendChild(el("p", "library-meta", "读取提示 " + array(preview.warnings).length + " 项，详情见处理记录。"));
    function automaticAliases() {
      if (provider.mode === "cover" || preview.cover_only || preview.provider === "douban") return [];
      var aliases = preview.automatic_aliases;
      if (provider.selected.indexOf("name") >= 0) aliases = (preview.automatic_aliases_by_name_binding || {})[provider.fieldChoices.name || preview.default_name_binding_id];
      return array(aliases).filter(function (alias) { return typeof alias === "string" && alias.trim(); });
    }
    var peoplePreview = preview.provider !== "douban" && provider.mode !== "cover" && !preview.cover_only && preview.people_preview;
    function applyLabel() {
      if (provider.mode === "cover") return "确认更新封面";
      if (preview.provider === "douban") return provider.selected.indexOf("metadata.ratings.douban") >= 0 ? "确认更新评分" : "仅确认评分来源";
      if (provider.mode === "sync") return "确认本次作品数据刷新";
      if (peoplePreview) return provider.selected.length ? "确认写入所选字段与人物资料" : "确认来源与人物资料";
      return provider.selected.length ? "确认写入所选字段" : automaticAliases().length ? "确认来源并追加别名" : "仅确认来源关联";
    }
    function applyBlockReason() {
      if (state.busy || state.peopleRetrying || provider.loading) return "正在处理，请完成后再确认。";
      if (preview.can_apply === false || provider.mode === "sync" && !preview.preview_token) return "没有可确认的来源结果，请重新获取来源数据。";
      if (provider.mode === "cover" && !provider.selected.length) return "请先选择要更新的封面。";
      try { if (preview.provider !== "douban") global.NimdaPeoplePanel.collectSelections(preview); } catch (error) { return error.message || "人物资料尚未完成核对。"; }
      return "";
    }
    var apply = button(applyLabel(), function () { return controller.applyProvider(preview); }, !!applyBlockReason(), "is-primary");
    var applyIssue = el("p", "library-person-selection-error library-source-apply-issue"); applyIssue.id = "library-source-apply-issue"; applyIssue.setAttribute("role", "status");
    var differenceRows = [];
    function updateSelection() {
      var additions = automaticAliases(); aliasNotice.hidden = !additions.length;
      aliasNotice.textContent = additions.length ? "确认来源时将自动追加别名：" + additions.map(function (alias) { return "「" + alias + "」"; }).join("、") + "。无需勾选“别名”；已有别名会保留并去重。" : "";
      var blocked = applyBlockReason();
      apply.textContent = applyLabel(); apply.disabled = !!blocked; apply.title = blocked;
      applyIssue.textContent = blocked ? "暂不能保存：" + blocked : ""; applyIssue.hidden = !blocked;
      if (blocked) apply.setAttribute("aria-describedby", applyIssue.id); else apply.removeAttribute("aria-describedby");
      differenceRows.forEach(function (entry) {
        var effective = effectiveProviderDifference(entry.difference, provider.selected, provider.fieldChoices);
        entry.choice.checked = provider.selected.indexOf(entry.key) >= 0;
        entry.choice.disabled = state.busy || state.peopleRetrying || !effective.selectable || !effective.changed;
        if (entry.aliasAfter) { clear(entry.aliasAfter); entry.aliasAfter.appendChild(entry.key === "metadata.cover" ? coverDifference(effective.after_url, "来源封面") : diffValue(effective.after, entry.key)); }
      });
    }
    differences.forEach(function (difference) {
      var key = difference.field || difference.path, row = el("div", "library-diff"), choice = el("input"); choice.type = "checkbox"; choice.setAttribute("aria-label", "采用" + (difference.label || fieldLabel(key)));
      var aliasAfter = key === "metadata.aliases" || array(difference.choices).length ? el("div", "library-diff-value" + (key === "metadata.aliases" ? " library-aliases-after" : "")) : null;
      differenceRows.push({ key: key, difference: difference, choice: choice, aliasAfter: aliasAfter });
      choice.addEventListener("change", function () { controller.selectProviderField(key, choice.checked); updateSelection(); });
      append(row, choice, el("strong", "", difference.label || fieldLabel(key)),
        key === "metadata.cover" ? coverDifference(difference.before_url, "当前封面") : diffValue(difference.before, key), el("span", "library-diff-arrow", "→"),
        aliasAfter || (key === "metadata.cover" ? coverDifference(difference.after_url, "来源封面") : diffValue(difference.after, key)));
      if (array(difference.choices).length > 1) {
        var candidateBox = el("div", "library-field-candidates"), candidates = el("select"); candidates.disabled = state.busy; candidates.id = "library-field-source-" + key.replace(/[^a-z0-9_-]/gi, "-");
        array(difference.choices).forEach(function (candidate) { var saved = sources.find(function (source) { return source.binding_id === candidate.binding_id; }); option(candidates, candidate.binding_id, candidate.binding_id === "all" ? "合并全部来源" : providerLabel(candidate.provider) + (saved ? " · " + sourceTitle(saved) : "")); });
        candidates.value = provider.fieldChoices[key] || difference.binding_id || difference.choices[0].binding_id;
        candidates.addEventListener("change", function () { controller.chooseProviderField(key, candidates.value); updateSelection(); }); labelField(candidateBox, "此字段采用的来源", candidates); row.appendChild(candidateBox);
      } else if (difference.provider) row.appendChild(el("p", "library-meta", "字段来源：" + providerLabel(difference.provider)));
      if (difference.reason) row.appendChild(el("p", "library-meta", difference.reason)); panel.appendChild(row);
    });
    updateSelection();
    if (!differences.length) panel.appendChild(el("p", "library-empty", "暂无可写入的差异。"));
    if (peoplePreview) {
      var peopleSection = el("details", "library-source-people-preview");
      peopleSection.open = !!(global.NimdaPeopleSelection && global.NimdaPeopleSelection.requiresConfirmation && global.NimdaPeopleSelection.requiresConfirmation(peoplePreview)) || !!(global.NimdaPeopleRetry && global.NimdaPeopleRetry.hasFailures(peoplePreview));
      peopleSection.appendChild(el("summary", "", "人物资料 · 随本次确认保存"));
      if (global.NimdaPeoplePanel && typeof global.NimdaPeoplePanel.renderPreview === "function") global.NimdaPeoplePanel.renderPreview(peopleSection, peoplePreview, detail.chapter_title_preference, { busy: state.busy || state.peopleRetrying || provider.loading, onChange: updateSelection,
        onRetry: function (child, ids) { return controller.retryPeopleFailures("provider", preview, child, ids); }, onCancelRetry: controller.cancelPeopleRetry, retryingSnapshotId: state.retryingSnapshotId });
      panel.appendChild(peopleSection);
    }
    append(panel, applyIssue, apply); parent.appendChild(panel);
  }
  function coverDifference(url, caption) {
    var box = el("div", "library-cover-difference");
    append(box, cover({ name: caption, cover_url: url }), el("span", "library-meta", caption)); return box;
  }
  function diffValue(value, field) {
    if (field === "metadata.ratings.douban") return doubanRating(value);
    var rendered = displayValue(value);
    if (rendered.length <= 360 && (!Array.isArray(value) || value.length <= 5)) return el("pre", "library-diff-value", rendered);
    var details = el("details", "library-diff-value"); append(details, el("summary", "", Array.isArray(value) ? value.length + " 条记录 · 展开查看" : "较长内容 · 展开查看"), el("pre", "library-diff-value", rendered)); return details;
  }
  function renderDetail(state) {
    disposeChapterPlayer();
    disposePeoplePanel();
    clear(ui.detail); if (!state.detailSelector) return;
    var toolbar = el("div", "library-detail-toolbar"); toolbar.appendChild(button("← 返回作品库", function () { controller.closeDetail(); }, state.busy)); ui.detail.appendChild(toolbar);
    if (state.detailLoading) { ui.detail.appendChild(el("p", "library-empty", "正在读取作品…")); return; }
    if (state.detailError) {
      ui.detail.appendChild(button("重试", function () { controller.openDetail(state.detailSelector, true); })); return;
    }
    if (!state.detail) return;
    var detail = state.detail, item = detail.item || detail.work || detail, heading = el("h1", "", titleOf(item)); heading.tabIndex = -1; ui.detailHeading = heading;
    var hero = el("header", "library-detail-hero"), info = el("div"); append(info, heading, el("p", "library-meta", [item.domain_label || item.domain, item.year].filter(Boolean).join(" · ")));
    var heroActions = el("div", "library-detail-actions"); append(heroActions,
      button("刷新作品数据", function () { return controller.refreshProvider(); }, state.busy || state.provider.loading, "is-primary"),
      button("编辑作品", function () { openEditor(detail); }, state.busy),
      button("清空作品资料…", function () { openWorkClear(); }, state.busy));
    append(hero, info, heroActions); ui.detail.appendChild(hero);
    var layout = el("div", "library-detail-layout"), sidebar = el("aside", "library-detail-sidebar"), content = el("div", "library-detail-content");
    sidebar.setAttribute("aria-label", "封面与作品资料"); append(sidebar, cover(item), workFacts(detail, item));
    var coverBindings = sourceRows(detail).filter(function (source) { return supportedSource(source) && supportsChapters(source.provider); });
    var coverTargets = coverBindings.filter(function (source, index, all) { return all.findIndex(function (other) { return other.provider === source.provider && text(other.subject_id || other.external_id) === text(source.subject_id || source.external_id) && other.season_number === source.season_number; }) === index; });
    sidebar.appendChild(button(coverTargets.length === 1 ? "更新封面" : item.domain === "animation" && !coverTargets.length ? "匹配 Bangumi 封面" : "匹配来源封面", function () {
      if (coverTargets.length === 1) return controller.previewCover(coverTargets[0].subject_id || coverTargets[0].external_id, coverTargets[0]);
      controller.setDetailTab("sources");
    }, state.busy || state.provider.loading, "library-cover-action"));
    append(layout, sidebar, content); ui.detail.appendChild(layout);
    var tabs = el("nav", "library-detail-tabs"); tabs.setAttribute("aria-label", "作品详情分区"); tabs.setAttribute("role", "tablist");
    var choices = [["overview", "概览"], ["people", "人物"], ["chapters", "章节"], ["resources", "资源"], ["classifications", "分类"], ["sources", "来源"]];
    choices.forEach(function (pair, index) {
      var active = state.detailTab === pair[0], tab = button(pair[1], function () { controller.setDetailTab(pair[0]); }, state.playbackSelecting);
      tab.id = "library-detail-tab-" + pair[0]; tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", active ? "true" : "false"); tab.setAttribute("aria-controls", "library-detail-panel"); tab.tabIndex = active ? 0 : -1;
      tab.addEventListener("keydown", function (event) {
        if (state.playbackSelecting) return;
        var next = event.key === "ArrowRight" ? (index + 1) % choices.length : event.key === "ArrowLeft" ? (index + choices.length - 1) % choices.length : event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1 : -1;
        if (next < 0) return; event.preventDefault(); controller.setDetailTab(choices[next][0]); document.getElementById("library-detail-tab-" + choices[next][0]).focus();
      }); tabs.appendChild(tab);
    }); content.appendChild(tabs);
    var panel = el("section", "library-detail-panel"); panel.id = "library-detail-panel"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", "library-detail-tab-" + state.detailTab); panel.tabIndex = 0;
    if (state.detailTab === "resources") renderResources(panel, detail, item);
    else if (state.detailTab === "chapters") renderChapters(panel, detail, item);
    else if (state.detailTab === "people" && global.NimdaPeoplePanel) global.NimdaPeoplePanel.render(panel, {
      detail: detail, state: state.people, busy: state.busy || state.peopleRetrying, bindings: sourceRows(detail),
      onRetry: function (child, ids) { return controller.retryPeopleFailures("people", state.people.preview, child, ids); }, onCancelRetry: controller.cancelPeopleRetry, retryingSnapshotId: state.retryingSnapshotId,
      onPreview: function () { return controller.previewPeople(); }, onApply: function (preview) { return controller.applyPeople(preview); },
      onWikipediaPreview: function (pages) { return controller.previewWikipediaPeople(pages); },
      onRolesPreview: openRoleCatalog,
      onCancel: controller.cancelPeoplePreview, onSource: controller.choosePeopleSource,
      onChooseSources: function () { controller.setDetailTab("sources"); }
    });
    else if (state.detailTab === "classifications") renderClassifications(panel, detail, item);
    else if (state.detailTab === "sources") renderProvider(panel, state, detail, item);
    else renderOverview(panel, detail, item);
    content.appendChild(panel);
  }
  function render(_state, reason) {
    if (!controller || !view) return; var state = controller.state, focusedElement = document.activeElement, focusedId = focusedElement && focusedElement.id;
    var selectionStart = (reason === "provider-range" || reason === "provider-query") && focusedElement ? focusedElement.selectionStart : null;
    var selectionEnd = (reason === "provider-range" || reason === "provider-query") && focusedElement ? focusedElement.selectionEnd : null;
    ui.browse.hidden = !!state.detailSelector; ui.detail.hidden = !state.detailSelector;
    if (state.detailSelector) disposeCardLayout();
    ui.detail.setAttribute("aria-busy", state.detailLoading || state.provider.loading || state.people.loading || state.busy ? "true" : "false");
    // Provider typing and detail updates never replace browse controls/cards.
    if (!state.detailSelector || reason === "browse") renderBrowse(state);
    if (state.detailSelector) renderDetail(state); else { disposeChapterPlayer(); disposePeoplePanel(); }
    if (reason === "detail" && ui.detailHeading) ui.detailHeading.focus();
    if (reason === "detail-tab") { var tab = document.getElementById("library-detail-tab-" + state.detailTab); if (tab) tab.focus(); }
    if (reason === "provider-search-start" || reason === "provider-source-choose") {
      var startTarget = document.getElementById(reason === "provider-search-start" ? "library-provider-query" : "library-source-choices");
      if (startTarget) startTarget.focus();
    }
    if (reason === "provider" || reason === "provider-range" || reason === "provider-query" || reason === "playback-numbering") {
      var focusTarget = reason !== "playback-numbering" && state.provider.preview && !state.provider.loading ? document.getElementById("library-sync-heading") : focusedId ? document.getElementById(focusedId) : null;
      if (focusTarget && !focusTarget.disabled) {
        focusTarget.focus();
        if (typeof selectionStart === "number" && typeof focusTarget.setSelectionRange === "function") focusTarget.setSelectionRange(selectionStart, selectionEnd);
      }
    }
    if (reason === "close-detail") { var card = ui.cardButtons && ui.cardButtons[returnFocusKey]; if (card) card.focus(); else ui.search.focus(); }
  }
  function openEditor(detail) {
    if (!controller || controller.state.busy) return;
    var epoch = controller.epoch(), currentController = controller, record = detail ? detail.record || (detail.item || {}).record : template();
    if (!record) { if (context.setStatus) context.setStatus("当前作品缺少完整记录，请重新加载详情。", true); return; }
    var ref = detail ? controller.currentRef() : null, config = context.config || {};
    global.NimdaNewWorkDialog.open({ title: detail ? "编辑作品" : "新增作品", submitLabel: "保存到本地作品库", hideHints: true,
      enumOptions: config.enum_options || {}, enumLabels: config.enum_labels || {}, initialData: global.NimdaWorkRecordForm.fromRecord(record),
      onSubmit: async function (patch) {
        if (controller !== currentController || epoch !== controller.epoch() || !controller.state.active) throw new Error("页面已变化，请关闭窗口后重新编辑。");
        return controller.saveRecord(global.NimdaWorkRecordForm.applyPatch(record, patch), ref);
      } });
  }
  function openWorkClear() {
    if (!controller || controller.state.busy || ui.clearDialog) return;
    var owner = controller, epoch = owner.epoch(), detail = owner.state.detail, saving = false, closed = false;
    var dialog = el("dialog", "library-person-dialog"), body = el("div"), heading = el("h2", "", "清空作品资料");
    heading.id = "work-clear-heading"; dialog.setAttribute("aria-labelledby", heading.id);
    function current() { return !closed && controller === owner && owner.epoch() === epoch && owner.state.detail === detail && owner.state.active; }
    function close() { if (saving) return; closed = true; if (dialog.open && dialog.close) dialog.close(); dialog.remove(); if (ui.clearDialog === dialog) ui.clearDialog = null; }
    var cancel = button("取消", close); append(dialog, heading, body, cancel); body.appendChild(el("p", "library-meta", "正在读取影响范围…"));
    ui.clearDialog = dialog; dialog.closePanel = close; dialog.addEventListener("cancel", function (event) { event.preventDefault(); close(); }); document.body.appendChild(dialog); if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    owner.previewClear().then(function (preview) {
      if (!current()) { close(); return; } clear(body); var selected = new Set(), inputs = [];
      array(preview.warnings).forEach(function (warning) { body.appendChild(el("p", "library-meta", warning)); });
      var apply = button("确认清空所选资料", async function () {
        if (!current() || !selected.size || saving) return; saving = true; cancel.disabled = apply.disabled = true; inputs.forEach(function (input) { input.disabled = true; });
        try { await owner.applyClear(preview, Array.from(selected)); saving = false; close(); }
        catch (error) { if (context.setStatus) context.setStatus(errorText(error), true); body.appendChild(el("p", "person-library-error", errorText(error))); }
        finally { saving = false; cancel.disabled = false; apply.disabled = !selected.size; inputs.forEach(function (input) { input.disabled = input.dataset.locked === "true"; }); }
      }, true, "is-primary");
      array(preview.groups).forEach(function (group) { var label = el("label", "person-library-clear-group"), input = el("input"); input.type = "checkbox"; input.disabled = !group.present; input.dataset.locked = String(!group.present); input.addEventListener("change", function () { if (input.checked) selected.add(group.id); else selected.delete(group.id); apply.disabled = !selected.size; }); inputs.push(input); append(label, input, el("span", "", group.label + (group.present ? "" : "（空）"))); body.appendChild(label); }); body.appendChild(apply);
    }).catch(function (error) { if (current()) { clear(body); body.appendChild(el("p", "person-library-error", errorText(error))); } });
  }
  function peopleChanged() { if (controller && controller.state.active && controller.state.detailSelector && !controller.state.busy) controller.openDetail(controller.state.detailSelector, true); }
  function openRoleCatalog() {
    if (!controller || controller.state.busy || ui.roleDialog) return;
    var owner = controller, epoch = owner.epoch(), detail = owner.state.detail, saving = false, closed = false;
    var dialog = el("dialog", "library-person-dialog"), body = el("div"), heading = el("h2", "", "角色档案预览"); heading.id = "role-catalog-heading"; dialog.setAttribute("aria-labelledby", heading.id);
    function current() { return !closed && controller === owner && owner.epoch() === epoch && owner.state.active && owner.state.detail === detail; }
    function close() { if (saving) return; closed = true; if (dialog.open && dialog.close) dialog.close(); dialog.remove(); if (ui.roleDialog === dialog) ui.roleDialog = null; }
    var cancel = button("取消", close); append(dialog, heading, body, cancel); body.appendChild(el("p", "library-meta", "正在预览当前作品的饰演关系…"));
    ui.roleDialog = dialog; dialog.closePanel = close; dialog.addEventListener("cancel", function (event) { event.preventDefault(); close(); }); document.body.appendChild(dialog); if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
    owner.previewRoles().then(function (preview) {
      if (!current()) { close(); return; } clear(body); var selected = new Set(), controls = [];
      body.appendChild(el("p", "library-meta", "按作品和来源角色证据建立档案，不按名字自动合并。不会把演员照片当作角色照片。"));
      array(preview.warnings).forEach(function (message) { body.appendChild(el("p", "library-meta", typeof message === "string" ? message : message.message)); });
      var apply = button("确认保存所选角色档案", async function () {
        if (!current() || !selected.size || saving) return; saving = true; cancel.disabled = apply.disabled = true; controls.forEach(function (input) { input.disabled = true; });
        try { await owner.applyRoles(preview, Array.from(selected)); saving = false; close(); }
        catch (error) { body.appendChild(el("p", "person-library-error", errorText(error))); if (context.setStatus) context.setStatus(errorText(error), true); }
        finally { saving = false; cancel.disabled = false; apply.disabled = !selected.size; controls.forEach(function (input) { input.disabled = false; }); }
      }, true, "is-primary");
      array(preview.entries).forEach(function (entry) {
        var label = el("label", "person-library-clear-group"), input = el("input"); input.type = "checkbox"; input.checked = false; controls.push(input);
        input.addEventListener("change", function () { if (input.checked) selected.add(entry.id); else selected.delete(entry.id); apply.disabled = !selected.size; });
        append(label, input, el("span", "", (entry.name_cn || entry.name) + " · " + ({ create: "新增", update: "更新", unchanged: "检查已有关联" }[entry.action] || entry.action))); body.appendChild(label);
      }); if (!array(preview.entries).length) body.appendChild(el("p", "library-meta", "没有可建立档案的明确角色信息。")); body.appendChild(apply);
    }).catch(function (error) { if (current()) { clear(body); body.appendChild(el("p", "person-library-error", errorText(error))); } });
  }
  function openResourceEditor(detail) {
    if (!controller || controller.state.busy || controller.state.detail !== detail) return;
    if (!global.NimdaBatchResourceDialog) throw new Error("资源编辑组件未加载，请重新打开应用。");
    var owner = controller, epoch = owner.epoch(), ref = owner.currentRef(), config = context.config || {};
    global.NimdaBatchResourceDialog.open({ record: detail.record, workRoot: detail.work_directory,
      enumOptions: config.enum_options || {}, enumLabels: config.enum_labels || {},
      request: function (url, options) { return (context.fetchJson || global.NimdaCommon.fetchJson)(url, options); },
      setStatus: function (message, error) { if (context.setStatus) context.setStatus(message, error); },
      onSubmit: function (record) {
        if (controller !== owner || epoch !== owner.epoch() || !owner.state.active || owner.state.detail !== detail || owner.state.busy) throw new Error("页面已变化，请关闭窗口后重新关联资源。");
        return owner.saveRecord(record, ref);
      } });
  }
  function keydown(event) {
    if (event.key === "Escape" && controller && controller.state.active && controller.state.detailSelector && !document.querySelector("dialog[open]")) { event.preventDefault(); controller.closeDetail(); }
  }
  function mount(nextContext) {
    context = nextContext || context; if (controller) return;
    document.addEventListener("nimda-people-changed", peopleChanged);
    var shell = document.querySelector(".app-shell") || document.body, tabs = document.querySelector(".app-tabs");
    nav = document.getElementById("tab-catalog-library");
    if (!nav && tabs) { nav = el("button", "app-tab", "作品库"); nav.id = "tab-catalog-library"; nav.type = "button"; nav.setAttribute("role", "tab"); tabs.appendChild(nav); ownedTab = true; }
    view = document.getElementById("catalog-library-view");
    if (!view) { view = el("main", "catalog-library-view"); view.id = "catalog-library-view"; view.hidden = true; shell.appendChild(view); ownedView = true; }
    view.classList.add("catalog-library-view"); clear(view);
    controller = createController({ request: function (url, options) { return (context.fetchJson || global.NimdaCommon.fetchJson)(url, options); },
      setStatus: function (value, error) { if (context.setStatus) context.setStatus(value, error); }, onChange: render,
      mode: global.NimdaCommon.readPreference ? global.NimdaCommon.readPreference("nimda.catalog-library.view") : "cards" });
    ui.cardSize = global.NimdaLibraryCardLayout.normalizeSize(global.NimdaCommon.readPreference && global.NimdaCommon.readPreference("nimda.catalog-library.card-size"));
    ui.browse = el("section", "library-browse"); ui.detail = el("section", "library-detail"); ui.detail.hidden = true;
    var header = el("header", "library-header"), title = el("div"); ui.count = el("span", "library-total", "本地作品");
    title.appendChild(el("h1", "", "我的作品库"));
    var actions = el("div", "library-actions");
    ui.maintenance = el("details", "library-maintenance"); ui.maintenance.hidden = true; ui.maintenance.appendChild(el("summary", "", "维护"));
    ui.initialize = button("初始化本地标识…", function () { return controller.previewIdentities(); }); ui.maintenance.appendChild(ui.initialize);
    ui.refresh = button("刷新本地列表", function () { controller.browse(); }); ui.add = button("＋ 新增作品", function () { openEditor(null); }, false, "is-primary");
    ui.displaySettings = button("显示设置", function () {
      if (global.NimdaLibraryDisplaySettings) global.NimdaLibraryDisplaySettings.open({
        load: controller.readDisplaySettings, save: controller.saveDisplaySettings,
        setStatus: function (message, error) { if (context.setStatus) context.setStatus(message, error); }
      });
    });
    append(actions, ui.maintenance, ui.displaySettings, ui.refresh, ui.add); append(header, title, actions); ui.browse.appendChild(header);
    ui.domains = el("nav", "library-domain-nav"); ui.domains.setAttribute("aria-label", "作品品类"); ui.browse.appendChild(ui.domains);
    var columns = el("div", "library-browse-layout"), mainColumn = el("div", "library-browse-main"), sidebar = el("aside", "library-browse-sidebar");
    ui.columns = columns; sidebar.setAttribute("aria-label", "筛选作品"); append(columns, mainColumn, sidebar);
    var filters = el("form", "library-filters"), searchControl = el("div", "library-search-control"); filters.appendChild(searchControl);
    ui.search = el("input"); ui.search.type = "search"; ui.search.placeholder = "搜索本地作品、别名…";
    ui.search.addEventListener("input", function () { controller.setSearchDraft(ui.search.value); });
    labelField(searchControl, "搜索作品", ui.search); var search = el("button", "library-button library-search-submit"); search.type = "submit";
    search.setAttribute("aria-label", "搜索"); search.title = "搜索作品";
    var searchIcon = el("span", "library-search-icon"); searchIcon.setAttribute("aria-hidden", "true"); search.appendChild(searchIcon); searchControl.appendChild(search);
    filters.addEventListener("submit", function (event) { event.preventDefault(); controller.setFilters({ query: ui.search.value }); });
    ui.browse.appendChild(filters); ui.browse.appendChild(columns); mountBrowseSidebar(sidebar);
    ui.activeFilters = el("div", "library-active-filters"); mainColumn.appendChild(ui.activeFilters);
    var layout = el("div", "library-layout-tools"); layout.appendChild(ui.count);
    ui.sort = labelField(layout, "排序", el("select")); [["name", "名称"], ["date_desc", "日期从新到旧"], ["date_asc", "日期从旧到新"], ["series_order", "系列内顺序"]].forEach(function (pair) { option(ui.sort, pair[0], pair[1]); }); ui.sort.addEventListener("change", function () { controller.setFilters({ sort: ui.sort.value }); });
    var modes = el("div", "library-view-modes"); modes.setAttribute("role", "group"); modes.setAttribute("aria-label", "展示方式");
    ui.cards = button("卡片", function () { controller.setMode("cards"); if (global.NimdaCommon.writePreference) global.NimdaCommon.writePreference("nimda.catalog-library.view", "cards"); });
    ui.list = button("列表", function () { controller.setMode("list"); if (global.NimdaCommon.writePreference) global.NimdaCommon.writePreference("nimda.catalog-library.view", "list"); }); append(modes, ui.cards, ui.list); layout.appendChild(modes);
    ui.cardSizes = el("div", "library-card-sizes"); ui.cardSizes.setAttribute("role", "group"); ui.cardSizes.setAttribute("aria-label", "卡片大小"); ui.cardSizeButtons = {};
    [["small", "小"], ["medium", "中"], ["large", "大"], ["auto", "自动"]].forEach(function (pair) {
      var sizeButton = button(pair[1], function () {
        ui.cardSize = pair[0]; updateCardSize();
        if (global.NimdaCommon.writePreference) global.NimdaCommon.writePreference("nimda.catalog-library.card-size", ui.cardSize);
      });
      sizeButton.setAttribute("aria-label", pair[1] === "自动" ? "自动适配卡片大小" : pair[1] + "号卡片");
      sizeButton.setAttribute("aria-pressed", String(ui.cardSize === pair[0]));
      ui.cardSizeButtons[pair[0]] = sizeButton; ui.cardSizes.appendChild(sizeButton);
    }); layout.appendChild(ui.cardSizes); mainColumn.appendChild(layout);
    ui.identity = el("section", "library-identity-preview"); ui.results = el("div", "library-results"); ui.pagination = el("nav", "library-pagination"); ui.pagination.setAttribute("aria-label", "作品分页");
    append(mainColumn, ui.identity, ui.results, ui.pagination); append(view, ui.browse, ui.detail); document.addEventListener("keydown", keydown); render();
  }
  function disposeChapterPlayer() {
    if (ui.chapterPlayer) { ui.chapterPlayer.dispose(); ui.chapterPlayer = null; }
  }
  function disposeCardLayout() {
    if (ui.cardLayout) { ui.cardLayout.dispose(); ui.cardLayout = null; }
    if (ui.cardTooltip) { ui.cardTooltip.dispose(); ui.cardTooltip = null; }
    global.NimdaLibraryCardPopover.close(false);
  }
  function updateCardSize() {
    Object.keys(ui.cardSizeButtons).forEach(function (size) { ui.cardSizeButtons[size].setAttribute("aria-pressed", String(size === ui.cardSize)); });
    disposeCardLayout();
    if (ui.cardGrid) {
      ui.cardGrid.setAttribute("data-card-size", ui.cardSize);
      if (!controller.state.detailSelector) {
        ui.cardLayout = global.NimdaLibraryCardLayout.attach(ui.cardGrid, { size: ui.cardSize });
        ui.cardTooltip = global.NimdaLibraryCardPopover.bindTooltip(ui.cardGrid);
      }
    }
  }
  function disposePeoplePanel() {
    if (global.NimdaPeoplePanel && global.NimdaPeoplePanel.dispose) global.NimdaPeoplePanel.dispose();
  }
  function dispose() {
    document.removeEventListener("nimda-people-changed", peopleChanged);
    if (global.NimdaPersonProfile) global.NimdaPersonProfile.close();
    if (ui.clearDialog) ui.clearDialog.closePanel();
    if (ui.roleDialog) ui.roleDialog.closePanel();
    if (global.NimdaBatchResourceDialog) global.NimdaBatchResourceDialog.dispose();
    if (global.NimdaLibraryDisplaySettings) global.NimdaLibraryDisplaySettings.close();
    disposeChapterPlayer();
    disposePeoplePanel();
    disposeCardLayout();
    if (controller) controller.dispose(); controller = null; document.removeEventListener("keydown", keydown);
    if (ownedView && view) view.remove(); else if (view) clear(view);
    if (ownedTab && nav) nav.remove(); view = nav = null; ui = {}; ownedView = ownedTab = false;
  }
  global.NimdaCatalogLibrary = { createController: createController, localCover: localCover, selector: selector, dateLabel: dateLabel, fieldLabel: fieldLabel, episodeRange: episodeRange, sourceLink: sourceLink, doubanSearchUrl: doubanSearchUrl,
    openWork: function (ref) { if (!controller || !nav) return; nav.click(); return controller.openDetail({ ref: ref }, true); } };
  global.NimdaCommon.ensureFeatureRegistry(global).register({ id: "catalog-library", label: "作品库", tabId: "tab-catalog-library", viewId: "catalog-library-view", order: 5,
    init: mount, activate: function (nextContext) { mount(nextContext); context = nextContext || context; controller.activate(); },
    deactivate: function () { disposeChapterPlayer(); disposePeoplePanel(); disposeCardLayout(); if (global.NimdaPersonProfile) global.NimdaPersonProfile.close(); if (ui.clearDialog) ui.clearDialog.closePanel(); if (ui.roleDialog) ui.roleDialog.closePanel(); if (global.NimdaLibraryDisplaySettings) global.NimdaLibraryDisplaySettings.close(); if (global.NimdaBatchResourceDialog) global.NimdaBatchResourceDialog.dispose(); if (controller) controller.deactivate(); },
    refreshAfterConfig: function (nextContext) { context = nextContext || context; if (controller && controller.state.active) controller.browse(); }, dispose: dispose });
})(window);
