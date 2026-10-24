# 作品管理器与本地统一工程

作品管理器（原 Nimda）保留独立 UI、业务功能和原内部包名，作为 `nimda-workspace` 页签接入。显示名变更不改变方法、数据 schema、单实例锁或用户偏好键。当前开发包 0.3.1，要求 hostApi 2，dataSchema 1。

## 目录与版本库

| 目录 | 内容 | 管理方式 |
|---|---|---|
| `plugins/media-manager` | 代码、测试、空配置模板、构建入口 | OpenDesk Git |
| `workspaces/media-manager/config` | 真实配置 | 独立 SVN；访问凭据忽略 |
| `workspaces/media-manager/data` | 正式作品、人物、收集资料与原始来源 | 独立 SVN；缓存、日志、锁文件忽略 |
| `workspaces/media-manager/legacy-documents` | 旧应用包含实际资料的维护记录 | SVN |
| `workspaces/media-manager/legacy-backups` | 原始备份副本 | 仅本地，版本库忽略 |
| `runtime` | OpenDesk 设置、插件安装、选择和模块偏好 | 仅本地，Git 忽略 |
| `run/OpenDesk` | 本机运行 EXE 与数据目录标记 | 仅本地，Git 忽略 |
| `dist` | 可审核的干净 EXE、安装器与独立插件包 | 构建产物，不入源码 Git |
| `.venvs/media-manager` | 插件专用构建环境 | 本机依赖，不入 Git |
| `.local` | 迁移清单、旧代码历史备份、构建设置 | 仅本地，Git 忽略 |

不要对整个工程运行 `svn add`，也不要将 SVN 工作副本嵌套进插件源码。Git 发布范围只含源码与公共文档。SVN 提交在 `workspaces/media-manager` 执行，并先查看 `svn status`、`svn diff`，不得提交 `providers.local.yaml`、缓存或凭据。

本机插件独立启动快捷方式放在 `plugins/media-manager/作品管理器.lnk`；工程根目录只保留宿主的 `OpenDesk.lnk`。快捷方式仅为启动入口，由 Git 忽略；代码仍在插件目录，真实资料仍在独立 SVN 工作区。

## 结构与构建

插件继续保留 `apps/framework` 和 `apps/features`；HTTP 业务装配集中到 `apps/composition/backend/media_manager_app/application.py`，旧入口只承担兼容转发。资源根与数据工作区分别解析，不依赖当前工作目录。前端仍在自己的文档内运行，宿主不改动业务 DOM。

开发、独立启动、合成数据测试、Python 锁文件及依赖初始化详见[插件 README](../plugins/media-manager/README.md)。主仓 `.local/dev.json` 可配置 `mediaManagerPython`，也可使用环境变量 `OPENDESK_MEDIA_MANAGER_PYTHON`。统一命令 `npm run build:module -- media-manager` 输出 `.ndpkg`、`manifest.json`、`SHA256SUMS` 到 `dist/modules/nimda-workspace/<版本>`。

插件 source 元数据读取 OpenDesk 的实际 Git 身份。旧 Nimda 的 Git 历史含真实资料，保留在本机备份，不作为子模块或历史合并导入主仓。既有模块 ID 不变；更新独立 manifest 即可，不要求每次升级宿主。主仓公开分发清单允许独立发布本插件，App 默认随附内容仍只含塔防助手。

## SVN 数据布局与迁移

SVN 数据统一放在仓库的 Manager/OpenDesc 下，每个插件使用独立子目录。OpenDesc 是本次指定的 SVN 路径名称，与应用 OpenDesk 的拼写不同；不要自动改名。插件当前只建立 media-manager，后续模块有正式资料时再增加各自目录。

| 模块 ID | SVN 相对路径 | 工程内工作副本 |
|---|---|---|
| nimda-workspace | Manager/OpenDesc/media-manager | workspaces/media-manager |

本机连接设置已指向上述新工作区。此目录是新地址的完整独立工作副本；不要把其他工作副本的 .svn 复制进来，也不要将源码 plugins/media-manager 纳入该数据目录。新增插件的数据目录名应保持稳定，并在插件文档中记录它与模块 ID 的对应关系。

2026-10-25 的 SVN r195 将旧 Project/nimda 的正式资料迁至 OpenDesc/work-manager，并删除旧路径；config/data/docs 的复制历史保留，docs 改为 legacy-documents。随后按统一命名要求，在 r196 将数据目录改为 OpenDesc/media-manager，并同步根 README。两次提交的历史仍可查询。

本地完整工作副本现位于 workspaces/media-manager。r196 改名时核对 29,465 个本地文件，路径变更前后内容完全一致；工作副本干净。模块连接设置已更新，凭据、缓存、日志、锁文件和 legacy-backups 仍为本地忽略项。迁移清单与旧副本保存在 Git 忽略的 .local 中，实际资料不进入源码或安装包。

外部媒体目录及配置中的媒体绝对路径保持原值。SVN 迁移由用户单独授权；本轮代码修改没有提交或推送 Git，也没有公开发布。

## 名称与兼容

中文显示名为“作品管理器”，英文名称为 Media Manager。源码、工作副本和构建环境分别为 plugins/media-manager、workspaces/media-manager、.venvs/media-manager；独立程序为 MediaManager.exe，新启动配置为 media-manager-desktop.yaml。统一构建命令使用 media-manager 别名。

已安装的包继续使用稳定 ID nimda-workspace；包文件、runtime 数据和 Git 版本标签按该 ID 追溯。nimda.* 控制方法、浏览器偏好键、原单实例锁及旧环境变量/启动配置名是明确兼容接口，不作为新的产品名称。这样旧设置和旧版本继续可识别，新旧程序仍不能同时写同一资料库。

规范环境变量为 MEDIA_MANAGER_APPLICATION_ROOT / MEDIA_MANAGER_WORKSPACE_ROOT；旧 NIMDA_* 仅在规范名缺失时读取，同时配置不同路径会报错。新启动配置不存在才查旧 nimda-desktop.yaml；存在但损坏或版本未知时拒绝加载。新构建 Python 使用 OPENDESK_MEDIA_MANAGER_PYTHON 或 .local/dev.json 的 mediaManagerPython，旧键仍可兼容。

嵌入页签、弹出窗口和独立 EXE 的区别及建议见[应用接入与展示模式](application-modes.md)。本轮审查的修复范围见[作品管理器代码审查](media-manager-review.md)。

## 本机运行与公开分发

`run/OpenDesk/opendesk.portable.json` 指向 `../../runtime`，使插件管理与设置归入新工程；公开 `dist/OpenDesk` 无此标记。发布审计会拒绝包含本机标记、runtime 或数据的 App 目录。WebView2 缓存仍由系统本地应用目录管理。

导入包与选择包分别处理；保留既有已安装版本和个人设置，新增版本需通过正常包校验。退出时等待后台任务完成，再复制、备份或迁移工作区。发布之前仍需审阅源码、从干净提交构建及审核附件；后续构建或本地迁移不自动创建 Git/SVN 提交或 Release；上文的数据地址迁移提交来自用户的单独授权。
