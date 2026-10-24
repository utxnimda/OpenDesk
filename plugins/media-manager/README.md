# 作品管理器

原 Nimda 应用作为 OpenDesk 的独立页签继续维护。作品库、人物资料、作品数据、收集情况和目录整理功能保留原界面与业务实现。显示名称统一为「作品管理器」；稳定模块 ID 为 `nimda-workspace`，当前正式页签版本为 `0.3.1`，`hostApi=2`、`dataSchema=1`。

[下载正式页签 0.3.1](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Fnimda-workspace%2Fv0.3.1)；按需在 OpenDesk「功能管理」导入。此 Release 不含真实资料或带本机工作区配置的独立 EXE ZIP。

可以在 OpenDesk 页签中使用，也可以弹出完整原应用窗口或独立启动。页签与弹窗共用后台，关闭弹窗返回页签；两视图保留各自未提交的输入，不自动同步表单草稿。原单实例锁继续防止独立应用和插件同时写同一工作区。

## 代码与数据

```text
OpenDesk/
  plugins/media-manager/
    apps/composition/backend/   HTTP 业务装配
    apps/framework/             原应用基础后端与页面壳
    apps/features/              业务代码、页面与测试
    defaults/config/            可公开的空配置模板
    integrations/opendesk/      页签包装、worker、认证与打包入口
    scripts/ packaging/         独立应用启动、测试和构建
  workspaces/media-manager/      SVN 工作区：真实 config、data、历史档案
  runtime/                      宿主管理的本地运行信息，不进入 Git
  .venvs/media-manager/           构建/开发 Python，不进入 Git
  dist/modules/nimda-workspace/ 页签包、manifest、SHA256SUMS，不进入 Git
  dist/plugins/media-manager/   可选独立桌面包，不进入 Git
```

Git 管理程序、测试、空模板、版本 manifest 和公共说明。当前 SVN 数据独立位于 Manager/OpenDesc/media-manager，本地仍使用 workspaces/media-manager；后续插件在 OpenDesc 下使用各自的数据目录。SVN 管理用户选择纳入版本控制的作品数据、正式配置及历史记录；缓存、日志、浏览器资料和备份是否纳入 SVN 由工作区策略决定。`providers.local.yaml` 等访问凭据同时排除 Git 和 SVN，本地使用环境变量或明确的私密文件。

`MEDIA_MANAGER_APPLICATION_ROOT` 指向本目录，`MEDIA_MANAGER_WORKSPACE_ROOT` 指向外部工作区。配置中的相对数据路径从工作区解析，不依赖启动目录。现有媒体库等外部绝对路径保持原值；移动代码不移动媒体或生成新的快捷方式。

HTTP 路由由 `apps/composition/backend/media_manager_app/application.py` 统一装配。旧 `work_catalog_yaml.jp_tv.browse_app` 是兼容转发，保留旧 CLI 和测试导入；新的宿主入口直接使用组合层。原前端在独立文档中运行，OpenDesk 不操作业务 DOM 或注入宿主主题。

## 开发环境与空工作区

以下命令在 OpenDesk 根目录运行，需要 Windows、Python 和 WebView2；Node 依赖使用主仓锁文件安装。

```powershell
python -m venv .venvs/media-manager
& ./.venvs/media-manager/Scripts/python.exe -m pip install -r ./plugins/media-manager/packaging/requirements-lock.txt
& ./.venvs/media-manager/Scripts/python.exe -m pip install --no-deps ./plugins/media-manager/apps/framework/backend
```

已有数据使用迁移后的 `workspaces/media-manager`，不执行初始化。只有创建新的空工作区时运行：

```powershell
& ./plugins/media-manager/scripts/init-workspace.ps1
# 或者指定另一个全新目录：
& ./plugins/media-manager/scripts/init-workspace.ps1 -WorkspaceRoot 'E:\Workspaces\new-catalog'
```

初始化只复制公共模板并建立空数据目录。目标已存在时拒绝执行，不覆盖现有配置或数据。之后在工作区配置中设置需要使用的媒体目录。

## 启动

本机独立启动快捷方式位于本插件目录的 `作品管理器.lnk`，指向已构建的独立程序；它由 Git 忽略，不放在 OpenDesk 根目录。页签内的嵌入与弹出入口保持原用法。

```powershell
# 默认 .venvs/media-manager 和 workspaces/media-manager
& ./plugins/media-manager/scripts/run-desktop.ps1
# 显式选择另一个工作区；Python 也可用 -Python 指定
& ./plugins/media-manager/scripts/run-desktop.ps1 -WorkspaceRoot 'E:\Workspaces\catalog'
# 本地浏览器开发入口
& ./plugins/media-manager/scripts/run.ps1 -Port 8765
```

OpenDesk 使用已构建、导入并启用的 `nimda-workspace` 包。首次连接填写外部工作区绝对路径；切换工作区需要先停用并重新启用模块。包内不附用户数据库，运行包不要求安装 Python。宿主连接设置通过 `OPENDESK_MODULE_DATA_DIR` 管理，详见[接入说明](integrations/opendesk/README.md)。

## 构建与版本

```powershell
# 页签包，使用主仓构建依赖
$env:OPENDESK_MEDIA_MANAGER_PYTHON = (Resolve-Path ./.venvs/media-manager/Scripts/python.exe).Path
npm run build:module -- media-manager
# 可选独立桌面包
& ./plugins/media-manager/scripts/build-desktop.ps1 -SkipDependencyInstall
```

页签产物位于 `dist/modules/nimda-workspace/0.3.1/`，包括 `.ndpkg`、`manifest.json`、`SHA256SUMS`；版本来自 `integrations/opendesk/manifest.json`。构建 Python 也可通过主仓 `.local/dev.json` 的 `mediaManagerPython` 指定。独立桌面包输出到 `dist/plugins/media-manager/standalone/MediaManager` 和对应 ZIP；旁边使用 `media-manager-desktop.yaml`，只记录外部工作区入口。独立包的本机启动配置不应作为通用发布默认配置。

新增兼容能力升 minor，修复升 patch，不兼容变化单独升级并说明迁移。模块版本、宿主 API 和数据 schema 分别维护；同 ID/版本不同摘要不能覆盖。源码、正式发布来源及附件统一由 OpenDesk 主仓记录，原 Nimda 的旧 Git 历史和私人运行报告不嵌入主仓。构建、提交、推送和发布是独立步骤。

## 验证

```powershell
# 自动建立临时空工作区；不读取 SVN 业务库
& ./plugins/media-manager/scripts/test.ps1
# 适配器回归，不启动真实工作区
$env:MEDIA_MANAGER_TEST_APPLICATION_ROOT = (Resolve-Path ./plugins/media-manager).Path
$env:OPENDESK_TEST_ROOT = (Resolve-Path .).Path
& ./.venvs/media-manager/Scripts/python.exe -m unittest discover -s ./plugins/media-manager/integrations/opendesk/tests -p 'test_*.py'
node --test ./plugins/media-manager/integrations/opendesk/tests/*.test.cjs
```

HTTP 契约测试保留迁移前 94 条路由和业务静态资源挂载；页面、目录选择、评分窗口和完整桌面行为仍需单独交互验证。基准入口 `scripts/benchmark.ps1 -WorkspaceRoot <绝对路径>` 会读取指定业务库，只在明确选择该工作区时执行。文档中的构建命令表示可用入口，不表示当前源码已经完成打包或发布。
