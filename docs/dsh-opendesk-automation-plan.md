# DSH × OpenDesk 页签操控链路统一规划

本文档规划如何打通 **DeepSeek Harness (`dsh`)** 与 **OpenDesk** 的跨页签协作链路，使未来用户可以在 DSH 对话中直接通过自然语言驱动已安装的 OpenDesk 页签功能（例如：「帮我打开塔防助手查看当前收益统计」「用作品管理器检索某个作品并打开详情」「在 XXX 页签里执行某个受控操作」），同时严格遵守 OpenDesk 的架构隔离、单权威状态与分级人工审批边界（见 [架构与边界](architecture.md) 与 [工程基本原则](principles.md)）。

> **文档状态声明**：本文档明确区分「已落地基线」与「后续规划阶段」；未标注为已实现的阶段均为架构设计与演进路线，尚未在宿主或业务页签中实施。

---

## 1. 当前已落地基线（v1）

当前仓库已打通从 OpenDesk 宿主到 DSH 的第一阶段只读闭环：

1. **独立 `hostApi: 2` / `webApp: true` 页签**：`deepseek-harness`（源码版本 `0.3.2`）通过包内 Python 监督 Worker 与本地 HTTP/WebSocket 反向代理承载原生 `dsh web`，支持托管模式、外部模式及本地源码开发工作流（`pnpm install`、`build:artifacts`、`dev:web`）。
2. **宿主 `moduleTools: 1` 跨页签只读中介**：OpenDesk Rust 宿主（`apps/framework/desktop/src-tauri/src/module_packages/tools.rs`）已实现：
   - 发现已打开页签在 `manifest.methods` 中显式声明的 `tools.describe`（`contractVersion: 1`、`readOnly: true`、空对象 `inputSchema`）；
   - 绑定完整目录快照弹出原生授权确认框（内存态授权，不落盘，版本/摘要/代次变化立即失效）；
   - 通过 `modules_tools_call` 复用目标正在运行的实例（绝不启动第二份业务 worker），限 64 KiB 返回。
3. **DSH 本地受控 Streamable HTTP MCP 桥接**：`deepseek-harness` Worker 内置仅监听 `127.0.0.1:<随机端口>/mcp` 的标准 MCP 服务端（强校验 `Host` + 会话级 `Authorization: Bearer <token>`），包装页轮询 `dsh.mcp.poll` → 调用宿主 `bridge.host.tools.call` → 回传 `dsh.mcp.respond`。
4. **本地源码仓双 ACP 模型接入**（位于独立平级仓库 `D:\MyWorkspace\deepseek-harness`，不混入 OpenDesk Git）：
   - `@deepseek-ai/dsh-llm-cursor-cli`：`Cursor CLI (ACP)` 常驻主模型（支持 45 个含 `1M`/`500K`/`300K`/`272K` 上下文选项的模型条目与推理档位）+ `subagent_cursor`；
   - `@deepseek-ai/dsh-llm-workbuddy-acp`：`WorkBuddy (ACP)` 常驻主模型（30 个模型与思考档位）+ `subagent_workbuddy`。

### 当前 v1 缺口（为何还不能直接说「用 XXX 页签做某事」）

| 缺口 | 当前限制 | 影响 |
| --- | --- | --- |
| 1. MCP 配置需手工粘贴 | 授权后需复制配置 JSON 到 DSH 的 MCP 设置 | 新会话或重启页签后会话令牌变化，需要重新配置 |
| 2. 目标页签必须已手动打开 | `discover_tools` 只扫描当前已激活实例 | 未打开的目标页签对 DSH 不可见，模型无法自主发现或唤醒 |
| 3. 仅支持无参数只读工具 | `tools.rs` 强制 `readOnly: true` 与空 `inputSchema` | 无法按条件查询（如关键词、ID、筛选条件），也无法触发受控操作或视图跳转 |
| 4. 缺少统一路由技能与审计视图 | DSH 不知道“OpenDesk 有哪些页签”以及“怎么分步安全执行” | 模型难以稳定规划“发现 → 唤醒 → 读取 → 确认 → 执行 → 复核”流程 |

---

## 2. 目标统一链路架构

```mermaid
flowchart TB
    U["用户自然语言<br/>例：用 XXX 页签帮我做 YYY"]
    DSH["DeepSeek Harness (dsh web)<br/>主模型 (Cursor/WorkBuddy/DeepSeek)<br/>+ OpenDesk Router Skill"]
    MCP["deepseek-harness Worker<br/>本地受控 Streamable HTTP MCP<br/>(自动注入隔离 DSH_HOME + list_changed)"]
    WRAP["deepseek-harness 包装页<br/>(module-sdk 协议桥)"]
    HOST["OpenDesk Rust 宿主 (唯一权威门禁)<br/>1. 模块目录与状态投影<br/>2. 受控唤醒/导航<br/>3. Schema/权限/代次/摘要重校验<br/>4. 分级原生审批弹窗 + 审计日志"]
    T1["塔防助手 Worker<br/>tools.describe v1/v2"]
    T2["作品管理器 Worker<br/>tools.describe v2"]
    TN["未来任意业务页签<br/>tools.describe v2"]

    U --> DSH
    DSH <-->|"Streamable HTTP MCP<br/>127.0.0.1 + 会话 Bearer"| MCP
    MCP <-->|"hostApi 2 白名单 RPC"| WRAP
    WRAP <-->|"MessagePort + moduleTools"| HOST
    HOST <-->|"stdio v1 (复用现有实例)"| T1
    HOST <-->|"stdio v1 (复用现有实例)"| T2
    HOST <-->|"stdio v1 (复用现有实例)"| TN
```

### 核心设计原则（不可突破的架构红线）

1. **宿主保持通用、轻薄且业务无关**：Rust 宿主只理解通用契约（模块 ID、版本、摘要、代次、工具描述符、JSON Schema、风险级别），**绝不内置任何具体业务页签的命令逻辑或私有数据结构**。
2. **单一权威与单实例所有权**：目标页签只能由 OpenDesk 宿主激活与管理，绝不因为一次工具调用而私自拉起第二份 worker，也不允许 DSH 直接连接其他模块的内部端口或直接读写 `modules/<id>/data`。
3. **禁止跨 iframe DOM 操控**：一切“操作某页签”都走**声明式命名工具 + 受控导航契约**，绝不向其他页签注入脚本、跨 DOM 查询或模拟键鼠点击。
4. **分级审批与最小权限**：
   - 只读查询可按会话批量授权；
   - **任何有副作用的写操作必须走宿主逐次原生弹窗确认（Human-in-the-loop）**，由宿主展示目标模块身份与实际参数摘要，用户点击确认后才转发执行。
5. **契约复用**：OpenDesk 宿主演进的 `tools.describe` v2 契约同时服务于 `deepseek-harness`、`ai-workbench` 及未来任何 AI/自动化页签，业务页签只需声明一次工具契约即可被所有受控调用方复用。

---

## 3. 分阶段演进路线

### 阶段 1：零配置 MCP 自动接通（纯插件侧，无宿主变更）

- **目标**：打开 `deepseek-harness` 页签并在面板授权后，DSH 立刻自带 `opendesk-bridge` MCP 工具，无需用户手工复制粘贴 URL 和 Bearer Token。
- **技术方案**：
  1. 在 `deepseek-harness` Worker 托管启动 `dsh web` 时，自动在隔离目录 `<data>/dsh-home/` 下生成/注入一份独立的 MCP overlay 配置（指向本轮会话的 `http://127.0.0.1:<mcp_port>/mcp` 与临时 Bearer Token，仅写进插件隔离 `DSH_HOME`，绝不改写用户本机 `~/.dsh`）；
  2. 为 `mcp_bridge.py` 补全 `notifications/tools/list_changed` 与可选 SSE 推送，当用户增删已授权工具时通知 DSH 刷新工具目录；
  3. 仅对托管隔离模式自动注入；外部模式或复用 `~/.dsh` 时仍保留一键复制配置 JSON，尊重用户已有环境。
- **验收点**：打开塔防助手 → 在 DSH 页签勾选授权 → 直接在 DSH 聊天中询问当前统计即可命中 MCP 工具，零手动粘贴。

### 阶段 2：页签目录发现与受控唤醒/导航（OpenDesk 宿主 `moduleTools` v2-a）

- **目标**：解决「我要用 XXX 页签，但它现在还没打开」的问题，让 DSH 能先查清有哪些页签、经确认后由宿主打开/切换过去。
- **宿主新增通用元能力**（均经 `module-sdk` `bridge.host.tools` 与 Rust 边界严格校验）：
  1. `host.modules.list`（只读元数据）：返回当前已安装且已启用的模块列表：`{ moduleId, label, version, opened, supportsTools, toolCount }`（不暴露磁盘路径、token 或禁用/失败包的敏感详情）；
  2. `host.modules.open`（受控激活）：参数 `{ moduleId, focus?: boolean }`，校验目标模块已安装、已选择、兼容且有效；经用户授权后复用主界面标准打开逻辑激活该页签并等待其就绪，返回当前打开代次（`generation`）；
  3. `host.modules.focus`（低风险导航）：在已打开页签间切换焦点，便于用户亲眼看到即将操作的目标页签状态。
- **MCP 映射**：在 `deepseek-harness` 的 MCP Server 中导出两个通用元工具：`opendesk_list_modules` 与 `opendesk_open_module`。

### 阶段 3：参数化工具与分级写操作契约（`tools.describe` `contractVersion: 2`）

- **目标**：让业务页签能够安全导出“带参数查询”“视图导航”与“受控写操作”，满足「在 XXX 页签里做某件事」的真实需求。
- **契约升级（向后兼容 v1）**：
  - 保留 `contractVersion: 1`（无参只读）完全兼容；
  - 新增 `contractVersion: 2` 工具描述符字段：

```json
{
  "contractVersion": 2,
  "name": "search_works",
  "method": "media.search",
  "title": "检索作品条目",
  "description": "按关键词和标签在当前已打开的作品库中只读检索匹配条目。",
  "access": "read",
  "confirm": "session",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "maxLength": 120 },
      "limit": { "type": "integer", "minimum": 1, "maximum": 50 }
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

- **三级访问与审批矩阵（Rust 宿主强制执行）**：

| `access` | 语义 | 参数边界（Rust 复验） | 审批策略 |
| --- | --- | --- | --- |
| `read` | 只读查询与计算 | 有界标量/数组/枚举，禁止控制字符与路径遍历；JSON 载荷 ≤ 16 KiB | 会话内批量原生弹窗授权一次 |
| `ui-navigate` | 切换页签子视图、聚焦条目、打开受控第二视图 | 仅允许声明的枚举视图名与有限 ID | 会话内授权或单次确认 |
| `write` | 创建/修改业务数据、提交计划、触发外部可控操作 | 同上 + 禁止任何任意 shell/命令/任意绝对路径参数 | **强制逐次原生弹窗审批**（展示目标模块、版本、摘要、操作标题与完整参数摘要） |

- **协议与安全守卫**：
  - 工具结果上限保持 64 KiB，统一附带 `{ moduleId, version, generation, executedAt }` 来源元数据；
  - `scripts/check-boundaries.mjs` 静态检查拒绝 `tools.describe` 映射到 `runtime.*`、`module.*` 或通用执行类方法名；
  - 宿主审计日志（`framework.jsonl`）记录每一次跨页签调用的关联 ID、调用方模块、目标模块/版本、工具名、风险级别、审批结论、耗时与错误码（不记录敏感令牌或完整私人提示词）。

### 阶段 4：DSH 侧意图路由 Skill 与业务页签渐进接入

- **DSH Router Skill（托管隔离 `DSH_HOME` 内置）**：
  为 DSH 提供一份简洁的内置 Skill，约定当用户提出「我要用 XXX 页签做 YYY」时的标准执行协议：
  1. **发现**：先调 `opendesk_list_modules` 确认目标页签名称、是否已启用、是否已打开；
  2. **唤醒**：目标未打开时说明原因并调用 `opendesk_open_module`；打开后再读取目标最新工具列表；
  3. **只读探查**：优先调用 `read` 类工具获取当前权威快照，避免凭空猜测状态；
  4. **受控执行**：涉及 `write` 工具时先向用户简述将要提交的参数，再发起工具调用等待 OpenDesk 宿主原生审批框结果；用户拒绝则立即停止，不重复弹窗骚扰；
  5. **结果复核**：执行完毕后回传目标页签返回的结构化结果或重新读取只读快照，不伪造成功。
- **各业务页签未来按需接入方向（均由各自模块按实际需求独立演进，不强制捆绑）**：
  - **`tower-assistant`（塔防助手）**：已有 v1 无参只读统计摘要；后续可按需增加参数化角色/阶段查询、渡劫方案只读试算与聚焦对应视图；
  - **`nimda-workspace`（作品管理器）**：后续可按需导出只读检索、作品详情查询、定位到指定作品视图等命名工具；
  - **其他独立页签**：只需在各自 `manifest.methods` 注册 `tools.describe`（v1 或 v2）与受控命名方法，无需修改 `deepseek-harness` 或 OpenDesk 框架代码即可被自然语言发现与操控。

---

## 4. 实施优先级与风险控制

| 优先级 | 阶段 | 改动范围 | 风险等级 | 依赖前提 |
| --- | --- | --- | --- | --- |
| P0（下一步推荐） | 阶段 1：零配置 MCP 自动注入 + `list_changed` | 仅 `plugins/deepseek-harness` | 低 | 无需改宿主版本，可直接发插件 `0.4.0` |
| P1 | 阶段 2：页签目录发现与受控唤醒/导航 | 宿主 `module_packages` + `module-sdk` + `deepseek-harness` | 中 | 需推进宿主新 minor 版本（`version:prepare`） |
| P2 | 阶段 3：`tools.describe` v2（参数化 + `write` 逐次审批） | 宿主 Rust 校验与审批 UI + `module-sdk` + `check-boundaries` | 中高 | 需完整的 Rust/前端契约测试与拒绝路径测试 |
| P3 | 阶段 4：DSH Router Skill + 首批业务页签接入 | DSH 隔离 Skill + 目标业务页签独立升版 | 低 | 依赖阶段 2/3 契约稳定 |
