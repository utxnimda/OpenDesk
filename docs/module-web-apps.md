# 完整本地 Web 应用接入（hostApi 2）

OpenDesk 0.16.0 开发版提供可选的本地 Web 应用接入契约。适合已有完整 HTTP 服务、上传下载、长任务和原生窗口的应用；业务页面无需重写成宿主 Vue 组件。包仍是标准 `.ndpkg`，源码归属和公开分发由明确配置决定：作品管理器已迁入 `plugins/media-manager`，稳定 ID `nimda-workspace` 列入允许独立公开的清单，但不属于 App 默认随附模块。其他私有应用继续独立维护，不因可导入或使用此契约而自动进入公开分发范围。接入与展示方式的选择见[应用模式评估](application-modes.md)。

## 包与页面

包清单沿用 schemaVersion 1，增加可选 `"webApp": true`，同时要求 `"hostApi": 2` 和非空 worker。未声明或值为 false 的包继续使用静态页面隔离。原 hostApi 1 的 manifest 无需修改。

`frontend/index.html` 是使用模块 SDK 的包装页面。宿主首先确认 worker 的身份、方法和 API，再请求 `module.webapp` 获得 `{ "url": "http://127.0.0.1:<port>/..." }`。只接受数字 IPv4 回环地址、明确端口且无用户名密码的 HTTP 地址。包装页面的 CSP 只为这个精确 origin 开放嵌套 iframe，仍禁止包装页面直接联网及执行动态脚本。所有业务 HTTP 通信发生在应用自己的文档内。

只有已验证的 webApp 实例会得到表单、模态确认、下载、弹窗权限；普通静态页签的 sandbox 保持原样。应用文档必须自己实施会话鉴权、外部链接策略和可信资源边界。回环地址与 iframe 隔离不等于恶意本地程序的安全沙箱。

模块自己的控制方法可以切换嵌入视图与原生独立窗口。同一 worker 可以保留两个视图并复用一个服务和数据目录；宿主不接管业务 DOM，也不假设不同 WebView 的内存、草稿或 localStorage 自动相同。应明确记录哪些偏好同步，哪些输入只在各自视图保留。

## 关闭契约

hostApi 2 worker 必须在 `runtime.hello.capabilities` 中声明 `module.prepareShutdown`；webApp 还需声明 `module.webapp`。这些是宿主管理方法，不列入 manifest 的业务 methods，也不能由业务页面绕过宿主调用。

`module.prepareShutdown({})` 要立即进入停止接收新任务的状态，取消尚未开始的工作，返回：

```json
{"ready": false, "active": 2}
```

进行中的写入完成后返回 `{"ready": true, "active": 0}`。宿主继续轮询，不以等待时限强杀正在执行业务的进程。就绪后才发送 `runtime.shutdown` 并回收自己拥有的进程。控制失败或响应无效时保留 worker 和会话；停用、版本切换或正常退出报错，可再次尝试。操作系统结束进程、断电及强制退出不受此契约保证。

版本切换失败恢复旧 worker 时，其 HTTP 端口可能变化。宿主会重新创建静态包装服务并递增页面修订号，使前端重新握手。首次正常打开不会触发重复重挂载。

## 数据与构建

宿主向 worker 提供 `OPENDESK_MODULE_DATA_DIR`。模块可在此保存自身连接设置和偏好，并明确选择原应用的工作区。应使用原应用的单实例锁，避免独立程序与模块同时写同一个工作区。程序资源、用户配置和业务数据必须分离，私人数据库或凭据不能进入 `.ndpkg`。

统一 `scripts/module-pack.mjs` 支持已注册的静态模块、测试 fixture，以及通过 `scripts/distribution.mjs` 中 `externalBuilder` 声明委托的应用构建器。作品管理器使用该入口调用自身 `integrations/opendesk/build.mjs`，可通过 `npm run build:module -- nimda-workspace` 构建 API 2 包；这里的 `externalBuilder` 表示独立构建流程，不表示源码必须位于主仓之外。公开范围仍由 `config/distribution.json` 的 `bundledModules` / `publicModules` 明确限定，私人构建器、本机安装和导入内容不自动加入白名单。应用构建器应复用 SDK、冻结独立 worker、烘焙与 manifest 一致的身份，校验实际包和关闭协议。构建目录不是运行数据目录，同版本包的字节不得被不同内容替换。

验证应覆盖旧 API 1 兼容、精确来源、非法 manifest、方法白名单、排空期间拒绝新操作、关闭失败保留进程、恢复后重新加载，以及真实 WebView 下的上传、图片、下载、原生对话框和模式切换。自动化检查和实际交互结果应分别记录。
