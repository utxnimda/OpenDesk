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

构建烘焙 `__MODULE_ID__`、`__MODULE_VERSION__`、`__MODULE_MARKER__`。版本来源不能是用户输入或运行环境变量。

## 4. 后台与构建接入

后台向通用 MethodRegistry 显式注册自身方法，严格校验参数。stdout 只用于协议，日志写 stderr 且不回显秘密。

构建组合显式导入模块注册入口并注册烘焙的 `module.identity`。每包 worker 及依赖一同冻结，不依赖使用者安装 Python。

当前受控构建目标在 `scripts/module-pack.mjs`，包含塔防助手和测试台。新增模块需增加受审阅的源码目录/后台入口，并明确是否随 App 默认分发；运行框架不新增业务分支或任意导入路径。

## 5. 数据与版本

业务数据在包外；不兼容 dataSchema 拒绝激活，不为旧代码覆盖新版数据。当前框架不提供自动迁移或降级数据。App 默认版本不能覆盖个人选择，禁用不删除安装包或数据。

独立模块更新自己的 manifest 版本，无需提升宿主；宿主/core 变更继续使用中央版本流程。同模块并行分支自行解决代码与版本冲突。

## 6. 验证与交付

验证正常导入、新 ID、同 ID 两版本、前端/worker 身份一致、不兼容原件保留且正常页签继续可用、同版本异摘要拒绝。后台检查非法参数和退出。

源码、类型、构建、包生成、桌面导入和真实切换分别记录。构建包不等于发布；Git 提交、推送、打标签和对外上传继续遵守本次用户明确授权。
