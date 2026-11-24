# DeepSeek Harness 独立页签

`deepseek-harness` 是 OpenDesk 的独立 `hostApi: 2`（`webApp: true`）页签包（当前本地源码版本 `0.3.2`），用于完整承载 [DeepSeek Harness (`dsh`)](https://github.com/deepseek-ai/deepseek-harness) 的原生 Web 应用及全部 Cordis 插件逻辑。

关于未来通过 DSH 自然语言操作 OpenDesk 其他页签的端到端架构规划，见 [DSH × OpenDesk 页签操控链路统一规划](dsh-opendesk-automation-plan.md)。

App 默认随附清单仍只有 `tower-assistant`；`deepseek-harness` 属于按需独立构建、导入并启用的独立页签。

## 工作原理

1. **监督 Worker（`module-worker.exe`）**：遵循 OpenDesk `hostApi: 2` stdio 控制协议，管理配置持久化、环境探测、本机 `dsh` 子进程（托管模式）或外部连接（外部模式），以及 `module.prepareShutdown` 优雅排空。
2. **本地 HTTP / WebSocket 反向代理**：
   - 仅绑定 `127.0.0.1:<随机端口>`，严格校验请求 `Host`；
   - 服务端捕获 `dsh web` 启动输出的 `?token=...` 并在进程内存中完成会话 Cookie 换票，启动令牌不进入浏览器地址栏，避免 WebView2 嵌套 iframe 的 `SameSite=Strict` 鉴权拦截；
   - 转发请求时重写 `Host` / `Origin` / `Referer` 为上游回环地址并注入受管会话 Cookie；
   - 剥离上游阻断 iframe 的 `X-Frame-Options`、`Content-Security-Policy`、`COOP` / `COEP` 响应头，保持 HTTP 流式（SSE、chunked、文件上传下载）与 WebSocket（`/api/remote.mux` 及插件 WS）全双工透传。
3. **包装页与控制面板**：
   - 遵循 OpenDesk 主题与外观变量；就绪后全屏展示原生 `dsh web` iframe，并提供可收起的极窄顶部控制条（设置与日志、刷新、重启、停止、外部浏览器打开）；
   - 所有控制操作均走 manifest 白名单 RPC（`dsh.status`、`dsh.probe`、`dsh.configure`、`dsh.start`、`dsh.stop`、`dsh.restart`、`dsh.install`、`dsh.install.cancel`、`dsh.source.install`、`dsh.source.build`、`dsh.source.cancel`、`dsh.logs`、`dsh.browser.open`、`dsh.mcp.sync`、`dsh.mcp.poll`、`dsh.mcp.respond`），不暴露通用 shell/exec 接口。
4. **本地源码开发工作流（`0.3.0` 新增）**：
   - 将「DSH 程序来源」设为「本地源码仓 / 自定义入口」并指向本地 `deepseek-harness` 克隆目录（建议平级独立仓库，如 `D:\MyWorkspace\deepseek-harness`）；
   - 自动识别源码仓版本、Git 分支/修改态、`pnpm` / `corepack pnpm` 工具链、依赖与产物构建状态；
   - 支持页签内一键执行受控白名单动作：`pnpm install`、`pnpm run build:artifacts`（支持完成后自动重启）、随时取消（Windows Job Object 回收）；
   - 支持两种源码运行模式：**已构建产物模式（`built`）**（启动快，修改源码后点顶栏「重建并重启」立即生效）与 **源码热开发模式（`dev`）**（运行 `pnpm run dev:web`，开启 `tsc` + `tsdown` + `vite` 监听自动热重建前端与插件源码）。
5. **OpenDesk 只读工具受控 MCP 桥接（`0.2.0` 新增，`0.3.1` 修复握手响应式同步）**：
   - 在页签设置面板中可列出其他已打开 OpenDesk 页签导出的 `contractVersion: 1` 只读工具，经由 OpenDesk 宿主原生授权弹窗（`bridge.host.tools.grant`）显式批准后，由 Worker 在 `127.0.0.1:<随机端口>/mcp` 启动标准 **Streamable HTTP MCP** 端点；
   - 端点强制校验精确 `Host` 与会话级随机 `Authorization: Bearer <token>`，仅暴露经用户勾选授权的无参数只读工具；每次 `tools/call` 均经包装页回流至宿主 `bridge.host.tools.call`，由 Rust 宿主重新核验版本、摘要、代次与授权快照；
   - 关闭页签、撤销桥接或触发 `module.prepareShutdown` 时立即撤销令牌并唤醒所有挂起调用；
   - `0.3.1` 修复了包装页在 `nimdadesk:connect` 异步握手完成前缓存 `bridge.host.tools` 为不可用的响应式问题，并在切换设置面板时自动刷新可用工具。
6. **本地源码仓扩展建议（独立平级仓库，不混入 OpenDesk Git）**：
   - 将 `deepseek-harness` 克隆至独立平级目录（如 `D:\MyWorkspace\deepseek-harness`）后，可在该源码仓内自行扩展模型与子代理插件（例如接入本机 Cursor CLI ACP、WorkBuddy CLI ACP、多上下文窗口与推理档位），在 OpenDesk 页签顶栏点击「重建并重启」即可快速编译生效。

## 运行模式与数据隔离

- **托管模式（`managed`，默认）**：
  - 由插件拉起并管理 `dsh --profile <profile> --host 127.0.0.1 --port <空闲端口> --no-open`，并通过 Windows Job Object 绑定进程树所有权；
  - `DSH_HOME` 默认隔离到 `OPENDESK_MODULE_DATA_DIR/dsh-home`（`DSH_AGENTS_HOME` 到 `dsh-agents`），不静默读写本机 `~/.dsh`；用户可在设置中显式切换为复用本机 `~/.dsh` 或自定义绝对目录；
  - 停用页签、切换版本或退出 OpenDesk 时，`module.prepareShutdown` 会先排空并回收插件拥有的 `dsh` 进程树，确认退出后才报告 `ready: true`。
- **外部模式（`external`）**：
  - 连接本机已独立运行的 `http://127.0.0.1:<端口>`（支持带可选 `?token=...`）；
  - 外部进程归用户所有，断开连接或退出 OpenDesk 时**不会终止**外部 `dsh` 进程；适合需要脱离 OpenDesk 7×24 运行定时任务的场景。

## 先决条件与安全边界

- 需要本机安装 **Node.js >= 24.9.0**（DeepSeek Harness 最低版本要求）。
- 启动阶段绝不自动联网安装依赖。若本机未检测到 `dsh`，可在页签内显式点击「安装 `@deepseek-ai/dsh` 到插件数据目录」，通过本机 `npm` 安装到 `OPENDESK_MODULE_DATA_DIR/runtime-cli`（支持取消），或指定本机已构建的 `deepseek-harness` 源码仓 / 入口文件。
- **安全提示**：DeepSeek Harness 处于 Developer Preview 阶段，官方 `SAFETY.md` 明确提示其尚未完成安全审计、可执行模型生成的命令并安装第三方插件。OpenDesk 的 `.ndpkg` 隔离、回环代理与 iframe **不是安全沙箱**；请仅在受控工作区、审慎权限策略下运行，并只安装可信插件。
- **剪贴板策略**：当前源码的宿主 `PackageFrame` 已针对宿主核验为 `webApp: true` 的模块放行 `allow="clipboard-write"`（静态 `hostApi: 1` 页签继续保持 `allow=""`）。若在旧版 0.21.0 宿主安装器中运行并遇到剪贴板写入受限，可使用顶栏「浏览器打开」在系统默认浏览器中访问同一本地代理地址。

## 开发与构建

```powershell
npm run test:dsh
npm run build:module -- deepseek-harness
```

构建产物位于 `dist/modules/deepseek-harness/<版本>/`（当前 `0.3.2`）。本页签当前已完成本地源码实现、自动化测试、真实 `@deepseek-ai/dsh` 与本地 `deepseek-harness` 源码构建产物联调以及冻结 `.ndpkg` 构建验收，未公开发布 Release，不自动修改任何用户的既有页签选择。
