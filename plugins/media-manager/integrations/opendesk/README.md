# 作品管理器的 OpenDesk 页签

保留原应用的完整前端和业务后端，程序、业务装配和可写工作区已分层，见[工具说明](../../README.md)。本目录只负责 OpenDesk 接入。插件使用同一后台支持两种展示：OpenDesk 内嵌页签、原 pywebview 独立窗口（1480 × 920）。弹窗关闭后回到嵌入模式；停用模块或退出 OpenDesk 才关闭后台。

## 使用

需要 OpenDesk 0.16.0 或兼容 hostApi 2 的更新版本。在「功能管理」导入已构建的 `nimda-workspace-0.3.1.ndpkg`，选择并启用。首次打开填写外部工作区的绝对路径，该目录应同时含 `config/` 与 `data/`；主仓默认是 `workspaces/media-manager`。之后点击「弹出窗口」或「嵌入页签」切换。

0.3.1 从干净源码构建正式包，保留本地 0.3.0。0.3.0 将显示名称统一为作品管理器、英文名称统一为 Media Manager，并修复工作区解析、资料版本校验和关闭/启动失败重试。0.2.0 完成源码并入 OpenDesk（当时显示名为“作品管理工具”）；此前 0.1.1 的复制路径修复继续保留。

两种展示共用一个后台和工作区，保存的修改在同一个地方。两个文档会在切换时保留，不丢弃各自当前输入；它们不是同一个浏览器内存，未提交的表单或正在查看的页签不承诺自动搬到另一个视图。主题等受支持的 `nimda.*` / `jp-tv-browse-*` 偏好保存到模块设置，新加载页面时读取共享值。若另一视图已显示旧数据，可使用原页面刷新入口读取已保存内容。

独立作品管理器与页签共用原有单实例锁。使用页签前正常关闭独立应用；需要恢复独立使用时，先停用页签或退出 OpenDesk。停用会拒绝新任务、取消排队任务并等待进行中的文件操作完成。错误时保留进程并提示，不强制结束写入。

程序资源随包冻结，真实工作区不复制进包。连接设置及浏览器缓存放在宿主提供的 `OPENDESK_MODULE_DATA_DIR`，模块不要求运行机器安装 Python 或检出源码。源代码模式与独立桌面启动方式保留。

## 构建

从 OpenDesk 根目录使用锁定的 Python 构建环境，以及主仓已安装的 Node 依赖：

```powershell
$env:OPENDESK_MEDIA_MANAGER_PYTHON = (Resolve-Path .venvs/media-manager/Scripts/python.exe).Path
npm run build:module -- media-manager
```

输出版本由 manifest 决定，当前位于 `dist/modules/nimda-workspace/0.3.1/`，包含 `.ndpkg`、`manifest.json` 和 `SHA256SUMS`。底层 `build.mjs` 可用 `--opendesk`、`--project`、`--python`、`--output` 和 `--work` 显式指定输入输出。构建器从 manifest 生成冻结身份，编译原前端资源和后端，并在出包前验证冻结 worker 的身份、HTTP 鉴权和正常关闭。不会安装或激活包，也不会上传或提交 Git。

更改内容后提升本目录 manifest 的三段语义版本；同版本已有产物时构建拒绝覆盖。来源元数据从 OpenDesk 主 Git 读取，未提交或无法完整确定来源时如实标记 dirty。公开范围由主仓分发清单决定，不能递归上传本机工作区或运行目录。

连接设置与偏好新写入扁平的 `schemaVersion: 1`。旧裸对象按版本 1 兼容读取，读取本身不改写；未知版本、错误结构和非法字段会报错并保留原文件。返回给业务页面的偏好不含版本元数据。

## 接入边界

- `worker_entry.py` / `nimda_opendesk`：stdio 控制、原生窗口、应用启动、关闭排空、仅当前本地会话可用的 HTTP 访问。
- `frontend/`：标准模块 SDK 包装页，原应用以独立文档呈现，不向业务 DOM 注入宿主样式。
- `bootstrap.js`：在模块模式中对原同源请求增加会话认证，支持原上传、图片和日志下载；外部请求不附带令牌。
- `module-worker.spec`：仅冻结程序及默认配置，不收集真实配置、数据库、媒体库或操作历史。

HTTP 服务只监听回环地址，每次进程生成会话令牌，入口片段不进入 HTTP 请求/Referer。业务 API 要求会话鉴权，下载资源仅白名单路径接受短期会话参数；外部 Origin 被拒绝。原应用的来源保护继续保留。模块仍是用户信任的本地代码，不是运行任意不可信软件的安全沙箱。

## 验证

```powershell
$env:MEDIA_MANAGER_TEST_APPLICATION_ROOT = (Resolve-Path plugins/media-manager).Path
$env:OPENDESK_TEST_ROOT = (Resolve-Path .).Path
& ./.venvs/media-manager/Scripts/python.exe -m unittest discover -s plugins/media-manager/integrations/opendesk/tests -p "test_*.py"
node --test plugins/media-manager/integrations/opendesk/tests/*.test.cjs
```

回归覆盖复制路径、错误提示保留、模式切换不重建 iframe、认证边界和安全关闭。历史 0.1.1 的验证记录不代表当前重构后的构建或桌面验证结果。

单元测试使用假队列/临时工作区，不操作真实媒体目录。`tests/frozen_smoke.py` 由构建器自动执行。桌面验证还应检查嵌入、弹出、关闭弹窗返回、原页面导航、图片及原生目录选择；测试结果不能由冻结构建成功代替。
