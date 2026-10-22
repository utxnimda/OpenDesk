# App 与页签下载

App 与正式页签分别按版本发布。每个版本保留独立下载页与 SHA-256 清单；GitHub 标签的源码归档对应该版本源码。安装器、便携 ZIP 和 `.ndpkg` 放在 Release 附件中，不写入 Git 历史。下列版本已公开发布；2026-10-22 已下载核对 0.12.0 App 与 0.1.7 页签的全部附件摘要，并确认公开下载链接可访问。

## App

| 版本 | Windows x64 下载 | 随附页签 |
|---|---|---|
| [0.12.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.12.0) | [安装器](https://github.com/utxnimda/OpenDesk/releases/download/v0.12.0/OpenDesk_0.12.0_x64-setup.exe) · [便携包](https://github.com/utxnimda/OpenDesk/releases/download/v0.12.0/OpenDesk_0.12.0_windows-x64_portable.zip) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/v0.12.0/SHA256SUMS.txt) | 塔防山贼助手 0.1.7 |
| [0.6.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.6.0) | 历史 Release 中保留原产物与校验文件 | 以该历史版本清单为准 |

便携包需要完整解压后启动 `OpenDesk.exe`。0.12.0 仅随附塔防山贼助手；模块测试台保留为源码开发测试，不随 App 发布。App 升级保留已有用户的模块版本选择，不自动切换或删除已导入的包。

## 正式页签

### tower-assistant / 塔防山贼助手

| 版本 | 独立下载 | 契约 |
|---|---|---|
| [0.1.7](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.7) | [页签包](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.1.7/tower-assistant-0.1.7.ndpkg) · [校验清单](https://github.com/utxnimda/OpenDesk/releases/download/modules%2Ftower-assistant%2Fv0.1.7/SHA256SUMS.txt) | Windows x64、hostApi 1、dataSchema 1 |

下载 `.ndpkg` 后，在 OpenDesk「功能管理」中导入、选择对应版本并启用。不同版本可并存，同 ID/版本但摘要不同的包会被拒绝，数据不兼容也不会静默降级。只有本仓库明确列入分发清单的正式页签有独立下载；本机额外导入、其他开发分支、私有模块与测试 fixture 不会自动上传。

## 维护方式

App 标签使用 `v<版本>`；页签标签使用 `modules/<模块ID>/v<版本>`。每次增加一行下载记录，保留旧版本，不替换旧 Release 的同名产物。发布者从 `dist/releases/app/<版本>/` 或 `dist/releases/modules/<模块ID>/<版本>/` 的审核结果上传明确列出的文件；不得递归上传整个 `dist`、缓存或个人模块目录。
