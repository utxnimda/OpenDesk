# OpenDesk AI 工作台

独立的 hostApi 2 Web App 页签。当前本地开发版本 0.4.2，使用 Vue 3 / TypeScript 与 Python，详细范围、使用和权限见[AI 工作台说明](../../docs/ai-workbench.md)。

支持 OpenAI Responses、Chat Completions 兼容接口、本地 Codex App Server 与 Cursor Agent ACP。API 模式可按用户授权调用 OpenDesk 当前已打开插件的只读工具；本地代理使用各自的只读模式与审批，不接入动态插件工具。模型使用完整目录下拉列表，可在对话空闲时切换；Codex 工作区通过本机 app-server 显式读取。聊天布局参考 Codex，配色和材质继承 OpenDesk。

```powershell
# 在 OpenDesk 工程根目录
npm run test:ai
npm run build:module -- ai-workbench
```

独立产物位于 `dist/modules/ai-workbench/<版本>/`，在功能管理导入并启用。未配置连接时不会发送请求。Key 仅通过界面录入并以 Windows DPAPI 保存在模块数据根，禁止写入本目录、示例、日志或聊天导出。

目录职责：`frontend` 为实际聊天界面；`backend/ai_workbench` 为会话、模型与代理；`integrations/opendesk` 为标准页签包装、冻结入口和独立构建；`tests` 为合成数据与假服务验证。真实 API/登录信息、会话、截图和测试运行目录都不属于源码或包内容。
