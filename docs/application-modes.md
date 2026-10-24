# 应用接入与展示模式

状态：基于当前源码的架构评估。建议保留页签模块与独立应用两类接入方向，由每个工具按需选择展示方式；不将 OpenDesk 收缩为仅启动本地程序的工具。现有实现、建议和后续能力分别如下，本文不代表桌面运行验证或新功能已完成。

## 当前能力与三个概念

| 概念 | 当前实现 | 生命周期 |
|---|---|---|
| 按框架契约开发的页签模块 | 塔防助手是 hostApi 1 `.ndpkg`，包含自己的前端、模块 SDK 和 worker；通过有限业务方法调用后台 | 宿主管理版本、启用状态、页面连接及 worker |
| 完整本地 Web 应用的嵌入与弹窗 | 作品管理器使用 hostApi 2、`webApp: true`，复用原 HTTP 应用和 UI；嵌入文档与 pywebview 弹窗共用一个应用后台和工作区 | 弹窗关闭只隐藏窗口并回到嵌入模式；停用模块或正常退出宿主时排空并关闭后台 |
| 真正独立的桌面应用 | 作品管理器保留 `MediaManager.exe` 的构建和独立启动入口，可不经过 OpenDesk 使用 | 独立入口自行启动服务、窗口并取得单实例锁；当前与页签实例不能同时运行 |

“框架原生模块”指按宿主公开契约开发的页面或工具，不等于 Windows 原生控件，也不等于把业务代码编译进宿主。框架仍有 Vue 组件注册类型，但正式业务包由 Rust 包目录发现；塔防现在已经是独立包，而不是宿主直接导入的业务组件。

源码依据：[模块注册类型](../apps/framework/frontend/src/core/modules.ts)、[正式包发现边界](../apps/framework/frontend/src/core/discovery.ts)、[塔防 manifest](../apps/features/tower_assistant/manifest.json)、[作品管理器 manifest](../plugins/media-manager/integrations/opendesk/manifest.json)。

宿主通过 [PackageFrame](../apps/framework/frontend/src/components/PackageFrame.vue) 加载 iframe 并确认包身份。[作品管理器包装页](../plugins/media-manager/integrations/opendesk/frontend/main.ts)切换时保留嵌入文档；[原生窗口适配](../plugins/media-manager/integrations/opendesk/nimda_opendesk/native.py)切换窗口显示和隐藏；[worker 入口](../plugins/media-manager/integrations/opendesk/nimda_opendesk/worker.py)让两个视图共用一套应用后台。弹出窗口仍属于当前模块会话，不是可以脱离宿主生命周期的独立 EXE。

[包运行器](../apps/framework/desktop/src-tauri/src/module_packages/runtime.rs)只启动 manifest 指定的包内 worker，并要求 stdio 协议、能力集合及编译身份匹配。当前没有通用的本地 EXE 登记、启动和状态管理器，也没有把任意 EXE 界面嵌入页签的功能。独立程序的具体入口见[桌面启动实现](../plugins/media-manager/apps/framework/backend/work_catalog_yaml/desktop.py)与[构建脚本](../plugins/media-manager/scripts/build-desktop.ps1)。

## 选择与维护成本

| 方向 | 收益 | 实际成本与适配 |
|---|---|---|
| 只保留独立程序启动 | 已有桌面程序可自行管理窗口和界面，不需要嵌入适配 | 需要另建通用启动入口；塔防还需增加独立桌面壳和分发方式。会放弃已有页签、包桥接和集中展示能力，不是仅删除几处 iframe 即可完成 |
| 保留页签与独立应用方向 | 小工具留在工作台，完整应用保留原 UI；同一业务实现可按需要提供不同入口 | 维护包契约和需要的适配器；完整 Web 应用额外承担鉴权、来源校验、关闭排空及视图状态验证，不要求所有模块都实现所有模式 |

建议塔防继续使用 API 1 页签包，保留有限命令和现有 UI。它不需要为统一形式而增加 HTTP 服务、弹窗或独立 EXE。作品管理器继续使用 API 2 嵌入与弹出，并保留独立启动；三者复用业务源码，宿主适配层只处理连接、展示和生命周期。

作品管理器支持两种视图不等于维护两套业务，但确有展示成本：嵌入模式也预建隐藏的原生窗口，浏览器文档各有内存与事件状态，须验证切换、关闭、上传下载和原生对话框。若某个应用不需要嵌入或弹窗，应允许它只提供所需入口，不为了形式一致增加实现。

## 能力限制

- 嵌入文档与弹窗共享已保存数据，不共享浏览器内存。未保存表单、当前页签和已显示的旧数据不会自动搬到另一视图；受支持的偏好保存在后台，新加载页面读取共享值。见[作品管理器接入说明](../plugins/media-manager/integrations/opendesk/README.md)。
- Web 应用接入只开放已验证的精确回环来源。业务文档自己负责会话鉴权、外部链接和可信资源边界；独立 worker、回环地址及 iframe 都不是安全沙箱。见[API 2 契约](module-web-apps.md)。
- 正常停用和退出先拒绝新任务、取消排队任务并等待已开始的写入。关闭失败保留进程和错误；强制结束、断电或操作系统终止不受正常排空契约保证。
- 作品管理器独立入口和集成入口使用同一单实例锁。独立使用前应先停用模块或退出宿主；不能把两个进程同时写入资料当成现有支持能力。
- 包代码版本、数据 schema 和用户资料分别管理。切换包版本不会回退资料；不兼容 schema 当前直接拒绝，没有通用数据迁移或降级能力。

## 最小演进建议

1. 保留 API 1 和 API 2 包契约，按功能选择接入方式，不重写已有业务 UI。界面应区分“嵌入页签”“弹出窗口”“独立启动”，避免用“独立运行”同时表示进程隔离和独立应用。
2. 继续让业务实现只有一份；构建、宿主适配和独立启动入口分别验证。资料、连接设置、浏览器缓存与可分发程序分开，构建输出不携带本机工作区。
3. 当出现具体的外部 EXE 管理需求时，再增加“本地应用入口”能力。首版可记录可执行路径、参数数组与工作目录，通过受限的命名操作启动；执行边界重新校验，不向前端提供通用 shell。此项尚未实现。
4. 新增独立应用入口时明确进程所有权、重复启动和退出策略。宿主退出不能默认终止外部应用，也不能按进程名清理；需要继续运行的独立程序不能复用现有模块 worker 的自动回收语义。状态查询、唤起现有实例等能力按实际协议逐项增加，不假定任意 EXE 都能提供。

## 作品管理器命名与兼容

显示名统一为“作品管理器”，源码和工作区目录使用 `media-manager`，独立可执行文件使用 `MediaManager`。继续保留稳定包 ID `nimda-workspace`；当前统一构建入口已支持 `media-manager` 友好别名，包身份保持不变。

包 ID 已决定安装目录、个人选择、模块数据目录和 schema 记录，见[包存储实现](../apps/framework/desktop/src-tauri/src/module_packages/store.rs)。直接改成 `media-manager` 会被识别为新模块，既有版本、连接设置与偏好不会自然继承。改显示名称无需付出这项迁移成本。

`nimda.*` RPC、已有偏好键、兼容环境变量、`nimda-desktop.yaml` 和原单实例锁作为明确的兼容接口保留，不再用旧品牌作为界面显示名。尤其不能只改一个入口的锁名，否则新旧实例可能绕过互斥。偏好同步当前按旧键前缀识别，见[兼容适配脚本](../plugins/media-manager/integrations/opendesk/nimda_opendesk/bootstrap.js)。

未来确需更改稳定 ID 或配置键时，应另行定义旧安装识别、选择及数据迁移、冲突处理、原件保留与失败恢复；需要继续支持的旧 RPC 使用明确的兼容映射。不能用全局替换或静默生成默认配置完成迁移。

## 验证范围

本文依据源码和现有契约，不依据真实工作区内容。本次文档整理只检查引用、当前实现与建议之间的一致性，不新增功能、不修改版本历史；类型检查、自动化测试、构建和桌面交互结果以对应实现任务的验证记录为准。
