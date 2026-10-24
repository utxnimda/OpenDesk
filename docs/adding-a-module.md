# 添加一个版本化页签

先读 AGENTS、[工程原则](principles.md) 和[模块包契约](module-packages.md)。独立可切换单位是页签包；共享实现的页签内子功能不能直接宣称可任意组合版本。

## 1. 定义身份和目录

在 `apps/features/<source-folder>` 放置 `manifest.json`、`frontend` 和可选 `backend`。磁盘目录、模块 ID、显示名称分开，例如 `tower_assistant` 对应 `tower-assistant`。确定输入输出、精确业务方法、数据拥有者和副作用。

纯页面使用 `worker:null`、`methods:[]`，不要顺带引入任意执行器或数据库。

## 2. 填写 manifest

按 schema v1 填写 ID、标签、三段版本、宿主 API、目标、入口、方法和数据 schema。源码模板的 `source` 使用未知提交和 dirty；构建时填真实 Git 基线，不手填伪造身份。

业务 methods 禁止 `runtime.*`、`module.*`。已发布的同 ID/版本/目标包不能替换内容。需要测试变体时明确标记 fixture，不伪造正式发布历史。

## 3. 页面使用 SDK

从 `frontend/index.html`、`main.ts` 启动独立 Vite 构建，base 为 `./`。调用 `createModuleBridge()` 等待身份就绪，再挂载页面，使用 `bridge.host.callWorker()`。不直接导入 Tauri API，不挂全局注册表，不读取其他页签 DOM。

这里的入口属于模块自己的 `frontend`，与宿主的 `apps/composition/frontend` 入口分开。宿主 Vite 根已在组合层，框架提供可复用界面与契约；模块不能通过修改宿主入口隐式注册业务。

引用模块 SDK 的公共表面样式，由它解析可选 `uiStyle` 与 `surfaceEffects`，保留 protocol 1 和基础 theme 回退。当前宿主按风格提供固定静态表面，`surfaceEffects` 只保留兼容描述，不提供用户材质、浓度、动效或背景图调节；消息不包含图片字节或路径。iframe 填满宿主分配的区域，业务页面在自身 body 滚动，不要求宿主读取模块 DOM 或测量内容高度。

`glass` 的显示名为“深海透明”，存储与传输 ID 不变。模块使用公共样式适配其深蓝黑半透明、青色细边和低圆角紧凑分区，不按显示名另建协议分支；宿主顶部标签栏仍由框架负责。

构建烘焙 `__MODULE_ID__`、`__MODULE_VERSION__`、`__MODULE_MARKER__`。版本来源不能是用户输入或运行环境变量。

## 4. 后台与构建接入

后台向通用 MethodRegistry 显式注册自身方法，严格校验参数。stdout 只用于协议，日志写 stderr 且不回显秘密。

构建组合显式导入模块注册入口并注册烘焙的 `module.identity`。每包 worker 及依赖一同冻结，不依赖使用者安装 Python。

模块定义在 `scripts/distribution.mjs`，构建由 `scripts/module-pack.mjs` 执行。新增正式模块先增加受审阅的源码目录/后台入口，再明确加入 `config/distribution.json` 的 `publicModules`；需要随 App 分发时另加入 `bundledModules`。当前正式源码包含塔防助手与 `plugins/media-manager` 的作品管理器；后者通过明确的外部 builder 接入主仓构建。测试台标为 `testFixture`，源码用于开发验证，产物禁止进入两份公开列表。不要从本机导入、已安装或其他分支目录扫描待发布模块。

宠物资源扩展属于宿主组合，不遵循业务页签包注册流程。通用渲染器消费 `CompanionCatalog`，当前组合根注册阿宝 `traveler-01` 和阿沐 `amu-01`。模型需要稳定 `id`、必需的 `still` 与 `idle`；`walk`、`wave`、`sleep` 是可选独立资源，没有素材或使用重复路径时不显示入口。两只各装配 `loop.png` 与 `still.png`；阿沐完整动作仅保存在本机产物目录，不重复加入运行时。新增素材须按 [宠物资源说明](companion-assets.md) 记录来源、帧区间、帧率、摘要和授权。当前处理后角色按用户 2026-10-22 的明确公开交付请求纳入源码与 App，原视频不上传；这不为未来新素材产生自动授权，也不附加未提供的第三方许可。

## 5. 数据与版本

业务数据在包外；不兼容 dataSchema 拒绝激活，不为旧代码覆盖新版数据。当前框架不提供自动迁移或降级数据。App 默认版本不能覆盖个人选择，禁用不删除安装包或数据。

独立模块更新自己的 manifest 版本，无需提升宿主；宿主/core 变更继续使用中央版本流程。同模块并行分支自行解决代码与版本冲突。

## 6. 验证与交付

验证正常导入、新 ID、同 ID 两版本、前端/worker 身份一致、不兼容原件保留且正常页签继续可用、同版本异摘要拒绝。后台检查非法参数和退出。

源码、类型、构建、包生成、桌面导入和真实切换分别记录。构建包不等于发布；Git 提交、推送、打标签和对外上传继续遵守本次用户明确授权。

正式模块每次新增/更新后按自己的 ID/版本独立构建和发布下载。当前塔防示例：

```powershell
npm run build:module -- tower-assistant
npm run release:stage -- module tower-assistant
```

前者输出 `dist/modules/tower-assistant/0.1.7/`，后者输出 `dist/releases/modules/tower-assistant/0.1.7/`。暂存要求已提交的干净源码、本仓库来源、当前开发历史内的提交、匹配 manifest 与摘要，并拒绝已有发布版本目录。它不执行提交、打标签或上传；发布时使用 `modules/tower-assistant/v0.1.7` 并维护 [下载索引](downloads.md)。

App 本轮为 0.12.0，仅随附塔防 0.1.7。测试台 baseline 与四个变体由 `npm run build:module-fixtures` 单独生成，在独立 smoke 根预置 baseline 后验证，全部留在本地；已导入用户配置不会因此被卸载或覆盖。发布附件只能来自审核后的正式暂存目录，不能包含 fixture、imports/installed、缓存或私有数据。
