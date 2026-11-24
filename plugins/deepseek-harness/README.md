# DeepSeek Harness 页签（`deepseek-harness`）

独立的 OpenDesk `hostApi: 2`（`webApp: true`）页签包，当前本地源码版本 `0.3.2`。

通过包内冻结的 Python 监督 Worker、纯标准库 `asyncio` HTTP/WebSocket 本地反向代理、本地源码开发工作流（`pnpm install` / `build:artifacts` / `dev:web` 热开发）以及可选的 OpenDesk 只读工具受控 Streamable HTTP MCP 桥接，完整承载原生 `dsh web`（Cordis 全插件树、动态前端插件、HMR、MCP、Subagents、Plan、定时任务与多模型路由）。详细说明、权限与安全边界见 [DeepSeek Harness 页签说明](../../docs/deepseek-harness.md)，跨页签操控链路长期规划见 [DSH × OpenDesk 页签操控链路统一规划](../../docs/dsh-opendesk-automation-plan.md)。

## 开发与构建

```powershell
npm run test:dsh
npm run build:module -- deepseek-harness
# 或使用别名：
npm run build:module -- dsh
```

独立产物输出到 `dist/modules/deepseek-harness/<版本>/`，在 OpenDesk「功能管理」中导入、选择并启用。

- 运行 `dsh` 需要本机安装 **Node.js >= 24.9.0**。
- 插件不会在启动时自动联网安装依赖；若本机未检测到 `dsh`，可在页签控制面板中显式点击安装到模块隔离数据目录，或指定本地源码仓/入口文件，或连接本机已独立运行的 `dsh web`。
- 默认使用 `OPENDESK_MODULE_DATA_DIR/dsh-home` 作为独立 `DSH_HOME`，不静默读写本机 `~/.dsh`。
