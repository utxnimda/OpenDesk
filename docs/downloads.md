# App 与页签下载

App 与正式页签分别按版本发布。每个版本保留独立下载页与 SHA-256 清单，二进制附件不进入源码 Git。2026-10-29 已发布 App 0.19.3、AI 工作台 0.4.4 与塔防 0.9.3，三个 Release 共 14 个附件已重新下载核对大小与 SHA-256。旧版本和历史核验记录继续保留。

## App

| 版本 | Windows x64 下载 | 随附页签 |
|---|---|---|
| [0.19.3](https://github.com/utxnimda/OpenDesk/releases/tag/v0.19.3) | [安装器](https://github.com/utxnimda/OpenDesk/releases/download/v0.19.3/OpenDesk_0.19.3_x64-setup.exe) · [便携包](https://github.com/utxnimda/OpenDesk/releases/download/v0.19.3/OpenDesk_0.19.3_windows-x64_portable.zip) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/v0.19.3/SHA256SUMS.txt) | 塔防山贼助手 0.9.3（与独立包一致）；AI 单独下载 |
| [0.18.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.18.0) | [安装器](https://github.com/utxnimda/OpenDesk/releases/download/v0.18.0/OpenDesk_0.18.0_x64-setup.exe) · [便携包](https://github.com/utxnimda/OpenDesk/releases/download/v0.18.0/OpenDesk_0.18.0_windows-x64_portable.zip) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/v0.18.0/SHA256SUMS.txt) | 塔防山贼助手 0.8.2（与独立包一致） |
| [0.17.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.17.0) | [安装器](https://github.com/utxnimda/OpenDesk/releases/download/v0.17.0/OpenDesk_0.17.0_x64-setup.exe) · [便携包](https://github.com/utxnimda/OpenDesk/releases/download/v0.17.0/OpenDesk_0.17.0_windows-x64_portable.zip) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/v0.17.0/SHA256SUMS.txt) | 塔防山贼助手 0.1.9（沿用公开原包） |
| [0.15.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.15.0) | [安装器](https://github.com/utxnimda/OpenDesk/releases/download/v0.15.0/OpenDesk_0.15.0_x64-setup.exe) · [便携包](https://github.com/utxnimda/OpenDesk/releases/download/v0.15.0/OpenDesk_0.15.0_windows-x64_portable.zip) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/v0.15.0/SHA256SUMS.txt) | 塔防山贼助手 0.1.9 |
| [0.12.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.12.0) | [安装器](https://github.com/utxnimda/OpenDesk/releases/download/v0.12.0/OpenDesk_0.12.0_x64-setup.exe) · [便携包](https://github.com/utxnimda/OpenDesk/releases/download/v0.12.0/OpenDesk_0.12.0_windows-x64_portable.zip) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/v0.12.0/SHA256SUMS.txt) | 塔防山贼助手 0.1.7 |
| [0.6.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.6.0) | 历史 Release 中保留原产物与校验文件 | 以该历史版本清单为准 |

便携包需要完整解压后启动 `OpenDesk.exe`。当前 App 0.19.3 只随附塔防 0.9.3；AI 工作台 0.4.4 和作品管理器 0.3.1 按需单独导入。升级 App 保留已有精确版本选择，不自动切换或删除个人插件。部署工作台、模块测试台二进制、外部导入包和私人数据不属于公开交付。

## 正式页签

### ai-workbench / AI 工作台

| 版本 | 独立下载 | 契约 |
|---|---|---|
| [0.4.4](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Fai-workbench%2Fv0.4.4) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Fai-workbench%2Fv0.4.4/ai-workbench-0.4.4.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Fai-workbench%2Fv0.4.4/SHA256SUMS.txt) | Windows x64、hostApi 2、dataSchema 1；推荐 OpenDesk 0.19.3 |

支持 API、Codex 和 Cursor Agent、模型参数、可用工作区树、可调整侧栏、Markdown／代码高亮，以及 API 会话中的授权只读插件工具；修复流式终态读取竞态。CLI 需自行安装登录，原软件聊天不自动导入，WorkBuddy 与通用 MCP 尚未实现。包 SHA-256：`6e83e2f5bd877ed490ea0c48fd359cb87307d7e38e4f1ce12e9439ec07c6c6c6`。

### nimda-workspace / 作品管理器

| 版本 | 独立下载 | 契约 |
|---|---|---|
| [0.3.1](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Fnimda-workspace%2Fv0.3.1) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Fnimda-workspace%2Fv0.3.1/nimda-workspace-0.3.1.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Fnimda-workspace%2Fv0.3.1/SHA256SUMS.txt) | Windows x64、hostApi 2、dataSchema 1；需要 OpenDesk 0.16.0 或兼容更新版本 |

作品管理器保留原界面、嵌入页签与完整弹窗，连接用户指定的外部工作区。本次公开附件为标准页签包，不含真实资料、本机连接设置或独立 MediaManager.exe ZIP。包 SHA-256 为 `a47b0681b9791e58225a626f9af57f894540d2306baf58b6e92e2862db498f18`。本地开发 0.3.0 原包保留，正式版采用新的 0.3.1 身份。

### tower-assistant / 塔防山贼助手

| 版本 | 独立下载 | 契约 |
|---|---|---|
| [0.9.3](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.9.3) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.9.3/tower-assistant-0.9.3.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.9.3/SHA256SUMS.txt) | Windows x64、hostApi 1、dataSchema 1；AI 只读工具需宿主 0.19.0 或更新 |
| [0.8.2](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.8.2) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.8.2/tower-assistant-0.8.2.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.8.2/SHA256SUMS.txt) | Windows x64、hostApi 1、dataSchema 1；透明排行榜需 OpenDesk 0.18.0 |
| [0.1.9](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.9) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.1.9/tower-assistant-0.1.9.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.1.9/SHA256SUMS.txt) | Windows x64、hostApi 1、dataSchema 1；支持薄荷奶油 |
| [0.1.7](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.7) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.1.7/tower-assistant-0.1.7.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.1.7/SHA256SUMS.txt) | Windows x64、hostApi 1、dataSchema 1 |

下载 `.ndpkg` 后，在 OpenDesk「功能管理」中导入、选择对应版本并启用。不同版本可并存，同 ID/版本但摘要不同的包会被拒绝，数据不兼容也不会静默降级。只有本仓库明确列入分发清单的正式页签有独立下载；本机额外导入、其他开发分支、私有模块与测试 fixture 不会自动上传。

0.8.2 从干净提交 `036ba0ace3583cbff410a0ae1155b46a44386e13` 构建，独立包和 App 随附包 SHA-256 均为 `6ac145c18c7caf5de865894a50b50866322ef544bfb935f55d171c79ac3a2bad`。它保留 0.8.1 的历史明细快照修复；0.8.1 及更早本地开发包不覆盖，导入新包不自动更改游戏采集入口。此前已只读核对三份入口脚本一致，本次公式修复无需重装；其他环境若显示入口需要更新，按[模块说明](tower-assistant.md)显式操作。

0.1.8 是已使用过的本地开发包，没有作为正式版本上传；正式版升为 0.1.9，保留旧包与用户选择。0.1.9 与 App 随附包 SHA-256 均为 `bacc16c3aa03054932ccb76cad08039a1091b972ab449c8ea620a89c66df4b91`。

## 维护方式

App 标签使用 `v<版本>`；页签标签使用 `modules/<模块ID>/v<版本>`。每次增加一行下载记录，保留旧版本，不替换旧 Release 的同名产物。发布者从 `dist/releases/app/<版本>/` 或 `dist/releases/modules/<模块ID>/<版本>/` 的审核结果上传明确列出的文件；不得递归上传整个 `dist`、缓存或个人模块目录。可重复执行的操作顺序见[发布手册](releasing.md)，实际验证与限制见[验证记录](verification.md)。

2026-10-29 的 App 0.19.3、AI 0.4.4、塔防 0.9.3 构建来源均为 `92c22f21d21f5287e8acb515d1de4509ed1bec50`。App 随附与独立塔防包 SHA-256 均为 `5f94831ef3952937873c3a3c41e53a74f4a201d475eac9b12a82774f3f6b246a`。本地候选 0.19.2 / 0.4.3 / 0.9.2 被发布前 CI 拦截，没有公开，其原包和失败证据保留。
