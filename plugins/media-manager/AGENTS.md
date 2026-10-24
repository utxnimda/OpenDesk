# 作品管理器协作规则

同时遵守主仓库 AGENTS.md。用户已授权本工具源码和发布信息并入 OpenDesk；原工作区的真实配置、数据库、来源快照、历史报告和凭据不属于公开源码。

- `apps/composition/backend/media_manager_app` 装配业务路由、静态资源和应用生命周期。框架共享层不导入具体业务。`work_catalog_yaml.jp_tv` 中已有兼容转发保留旧导入身份；不要继续往转发层增加业务代码。
- `apps/framework` 保留原独立应用的基础能力，`apps/features/<id>` 保留现有业务。`integrations/opendesk` 只处理宿主桥接、会话认证和展示模式，不复制业务规则。OpenDesk 宿主不得导入本工具的私有实现。
- 原前端的内部注册表、DOM 标识和 CSS 是此独立应用内部兼容接口，不作为 OpenDesk 公共接口。宿主只通过模块 SDK 与 stdio 契约访问页签。新增跨功能能力优先采用显式接口；不要为改目录批量重写 UI。
- 显示名称为「作品管理器」。`nimda-workspace`、`nimda.*` 方法、旧 `NIMDA_*` 环境变量别名、原互斥锁和既有偏好键保持兼容；需要改变时单独设计迁移。
- 新环境变量使用 `MEDIA_MANAGER_APPLICATION_ROOT` / `MEDIA_MANAGER_WORKSPACE_ROOT`，旧别名仅用于兼容；同时配置不同根必须报错。新独立启动配置为 `media-manager-desktop.yaml`，仅新文件不存在时读取旧名。
- 程序根与可写工作区分离。运行数据默认位于主仓 `workspaces/media-manager`，可显式指定其他绝对路径；不得因缺失工作区回退到旧工程或源码目录写入。
- `defaults/config` 只收安全空模板。真实 `config/data`、媒体、日志、缓存、历史备份不进入 Git/安装包。`providers.local.yaml` 等凭据也不加入 SVN。
- 测试使用临时工作区和模拟外部调用。`scripts/test.ps1` 自动建立和清理空工作区；基准测试会读取所选工作区，不能冒充隔离测试。
- 修改后运行相关既有回归，尤其路径分离、HTTP 路由契约、队列排空和页面兼容性。构建产物与源码、桌面验证分别报告。版本以集成 manifest 为准；提交、推送、发布仍需要当次授权。
