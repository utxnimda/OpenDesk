import CollectionDetailView from "/features/collection-detail/view.js?v=137";

export default {
  name: "NimdaApp",
  components: { CollectionDetailView },
  data() {
    return {
      featureError: "",
    };
  },
  mounted() {
    this._nimdaDisposed = false;
    this.mountFeatureRuntime();
  },
  beforeUnmount() {
    this._nimdaDisposed = true;
    if (!this._nimdaRuntime) return;
    this._nimdaRuntime.appearance.dispose();
    this._nimdaRuntime.host.dispose();
    this._nimdaRuntime.table.dispose();
    this._nimdaRuntime = null;
  },
  methods: {
    async mountFeatureRuntime() {
      try {
        await import("./common/operation-result.js?v=188");
        await import("./common/runtime.js?v=138");
        await import("./common/enum-fields.js?v=144");
        await import("./common/press-summary.js?v=183");
        await import("./common/directory-browser.js?v=146");
        await import("./common/section-tabs.js?v=147");
        await import("./operation-center.js?v=188");
        await import("./appearance.js?v=138");
        await import("./feature-host.js?v=138");
        const registry = window.NimdaFeatureHost.ensureRegistry(window);
        await import("/features/collection-detail/new-work-dialog.js?v=155");
        await import("/features/collection-detail/work-record-form.js?v=145");
        await import("/features/collection-detail/work-detail-dialog.js?v=155");
        await import("/features/collection-detail/index.js?v=159");
        await import("/features/collection-info/index.js?v=155");
        await import("/features/directory-organizer/structure-preview.js?v=143");
        await import("/features/directory-organizer/index.js?v=159");
        await import("/features/catalog-library/chapter-number-format.js?v=164");
        await import("/features/catalog-library/chapter-title-format.js?v=171");
        await import("/features/catalog-library/chapter-batch-dialog.js?v=168");
        await import("/features/catalog-library/chapter-player.js?v=168");
        await import("/features/catalog-library/rating-browser.js?v=188");
        await import("/features/people-catalog/taxonomy.js?v=180");
        await import("/features/catalog-library/people-retry.js?v=190");
        await import("/features/catalog-library/people-selection.js?v=194");
        await import("/features/catalog-library/people-panel.js?v=194");
        await import("/features/people-catalog/profile-images.js?v=195");
        await import("/features/people-catalog/profile-dialog.js?v=195");
        await import("/features/catalog-library/display-settings.js?v=172");
        await import("/features/catalog-library/batch-resource-dialog.js?v=168");
        await import("/features/catalog-library/card-layout.js?v=183");
        await import("/features/catalog-library/card-popover.js?v=183");
        await import("/features/catalog-library/index.js?v=193");
        await import("/features/people-catalog/index.js?v=189");
        await import("/features/collection-detail/table-controller.js?v=180");
        if (this._nimdaDisposed) return;
        const setStatus = (message, isError) => {
          if (window.NimdaOperationCenter && window.NimdaOperationCenter.notice) {
            const activeFeature = document.body.getAttribute("data-active-tab");
            window.NimdaOperationCenter.notice(message, isError, { path: activeFeature ? "/api/" + activeFeature : "" });
          }
        };
        const fetchJson = window.NimdaCommon.fetchJson;
        let table;
        const host = window.NimdaFeatureHost.create({
          document,
          registry,
          getContext: () => table.getFeatureContext(),
          onBeforeTabChange: () => table.closeDbCatalogPopover(),
          onActiveChanged: () => table.syncSaveToolbar(),
        });
        table = window.NimdaCollectionTableController.create({
          featureHost: host, fetchJson, setStatus,
          collectionView: document.getElementById("collection-info-view"),
        });
        const appearance = window.NimdaAppearance.create({
          document,
          onThemeChanged: () => table.handleAppearanceChange(),
        });
        this._nimdaRuntime = { appearance, host, table };
        appearance.mount();
        host.mount();
        table.mount();
      } catch (error) {
        if (this._nimdaDisposed) return;
        const message = error && error.message ? error.message : String(error);
        if (window.NimdaOperationCenter && window.NimdaOperationCenter.notice) {
          window.NimdaOperationCenter.notice("功能加载失败：" + message, true);
        } else this.featureError = message;
      }
    },
  },
  template: `
    <div class="nimda-app-shell">
      <header class="top">
        <div class="app-brand" aria-label="作品管理器">
          <span class="app-brand-name">作品管理器</span>
        </div>
        <div class="toolbar toolbar-row appearance-toolbar">
          <label class="theme-select-wrap">
            <span class="theme-select-label">色调</span>
            <span class="theme-select-ui">
              <select id="theme-select" class="theme-select" aria-label="界面色调" title="只改变色调，页面布局保持一致">
                <option value="midnight">深蓝</option>
                <option value="paper">纸张浅色</option>
                <option value="forest">森林暗绿</option>
                <option value="rose">玫紫暖色</option>
                <option value="contrast">高对比</option>
                <option value="ocean">海洋青蓝</option>
                <option value="sunset">落日暖橙</option>
                <option value="slate">岩灰靛紫</option>
                <option value="sakura">樱花浅色</option>
              </select>
            </span>
          </label>
          <label class="theme-select-wrap">
            <span class="theme-select-label">字体</span>
            <span class="theme-select-ui">
              <select id="font-select" class="theme-select font-select" aria-label="界面字体">
                <option value="reference">参考网页</option>
                <option value="system">系统默认</option>
                <option value="yahei">微软雅黑</option>
                <option value="song">宋体</option>
                <option value="mono">等宽</option>
              </select>
            </span>
          </label>
        </div>
      </header>

      <nav class="app-tabs nav" aria-label="页面">
        <button type="button" class="app-tab" id="tab-catalog-library" data-tab="catalog-library">
          作品库
        </button>
        <button type="button" class="app-tab is-active" id="tab-collection-detail" data-tab="collection-detail">
          作品数据
        </button>
        <button type="button" class="app-tab" id="tab-collection-info" data-tab="collection-info">
          收集情况
        </button>
        <button type="button" class="app-tab" id="tab-directory-organizer" data-tab="directory-organizer">
          目录整理
        </button>
      </nav>

      <CollectionDetailView />

      <main id="collection-info-view" class="collection-info-view collection-records-view" hidden></main>
      <main id="directory-organizer-view" class="directory-organizer-view" hidden></main>
      <main id="catalog-library-view" class="catalog-library-view" hidden></main>
      <p v-if="featureError" class="status err">功能加载失败：{{ featureError }}</p>
      <footer class="foot muted" aria-hidden="true">&nbsp;</footer>
    </div>
  `,
};
