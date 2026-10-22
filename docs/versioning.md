# 宿主与模块版本维护

宿主/core 版本与独立页签包版本分别维护。App 发布包含宿主版本和默认模块包；用户后来导入或选择其他版本，不修改宿主版本。

## 权威边界

| 记录 | 权威范围 |
|---|---|
| `config/version.json` | 宿主/core 与已有发布历史 |
| 包内 `manifest.json` | 模块 ID、版本、API、数据 schema 和来源 |
| `app-manifest.json` | 该次 App 默认随附包及摘要 |
| `config/distribution.json` | 本次 App 随附及允许独立公开的正式模块 ID |
| 本机选择与激活状态 | 用户选择和实际运行的精确版本 |

已有塔防子功能历史保持不变，描述过去随 App 交付的事实，不代表当前包版本。共享包内的子功能可以用于分类；拆成独立包并建立接口后才能分别选版本。

OpenDesk 0.6.0 使用独立应用身份，`core.workbench` 为 0.4.0；随附塔防助手 0.1.3 和测试台 1.0.3 的包内容包含当前应用命名。改名通过追加新版本记录表达，保留此前历史中的名称、版本和路径，不覆盖旧发布历史，也不把历史验证改写为当前版本已通过。

## 宿主/core

继续使用：

```powershell
npm run version:prepare -- --file .local/next-release.json
npm run version:sync
npm run version:check
```

现有脚本校验维护者提供的日期、版本、功能变化并追加历史，生成 package/package-lock、Tauri、Cargo 及宿主 CHANGELOG。不得单独手改这些宿主版本，也不借同步更新其他依赖。

现有语义版本和功能递增规则保留：首次能力从 0.1.0 开始，兼容增强升 minor、修复升 patch，不兼容变化明确 breaking，稳定后升 major。脚本只接受正式三段版本，Git 身份单独记录。命令不自动提交、打标签或发布。

## 独立模块包

各模块在自身 manifest 维护版本。单模块更新不强制修改中央历史、宿主 CHANGELOG 或 package/Cargo/Tauri 版本。同模块并行开发自行解决代码和版本冲突，不要求中央变更片段聚合。

同 ID/版本/目标的正式产物不可换为不同摘要；改内容需升版本。构建输出可以重新生成，不能据此覆盖已安装或已发布的版本。Git 标签建议 `modules/<id>/v<version>`；标签追溯源码，摘要追溯实际产物。

包 source 的 repository URL 必须净化，commit 是完整 SHA 或 null，dirty 表示未提交或无法确定完整来源。跨暂存目录构建保守 dirty。复用未改模块的原包时保留其原源码身份，不把宿主新提交当作模块重新发布。fixture 明确测试用途，不创建伪造历史或标签。

## 默认版本、个人选择与数据

App 索引给出随附默认版本；个人精确选择独立保存。App 升级不悄悄换掉个人选择，不兼容则显示原因。安装、选择、激活分别表达，不能把失败显示成升级成功。

hostApi、dataSchema 与包语义版本独立。代码回退不代表数据回退；当前不兼容 schema 直接拒绝，不自动恢复旧快照覆盖升级后数据。

## 交付边界

从 0.12.0 起，当前正式 App 只随附塔防助手 `0.1.7`，对应 `core.module-packages 0.4.0`。`config/distribution.json` 的 `bundledModules`、`publicModules` 均仅含 `tower-assistant`；构建代码将 `test-lab` 标为测试 fixture，不允许将它加入正式分发列表。测试台源码可以保留为开发测试代码，但它的包与其他本机导入、私有或外部分支产物均不上传。此前版本的随附事实保留在历史中，不回写旧记录；用户已导入的包也不会因此被自动卸载或覆盖。

当前源码、App 与塔防页签已有用户于 2026-10-22 给出的明确公开交付授权；本节规定如何落实范围，不把本地安装列表视作上传清单。处理后的阿宝、阿沐按该次请求纳入源码与 App，原视频不上传，也不虚构第三方许可证。

### 干净提交与本地发布暂存

先审阅源码范围并完成已授权的提交，再从干净工作区构建：

```powershell
npm run build
npm run build:module -- tower-assistant
npm run release:stage -- app
npm run release:stage -- module tower-assistant
```

`build:module` 输出到 `dist/modules/<id>/<version>/`。App 构建自动写入 `dist/app-build.json`，绑定提交、版本、安装器及便携目录逐文件摘要。App 暂存要求该收据对应当前干净提交，实际文件清单与字节未变化。正式模块要求包内来源为本仓库已提交且干净的开发历史、ID 在公开白名单中，并核对内部/外部 manifest 与摘要；可以保留可验证的既有干净祖先提交来源，不能把旧 dirty 包仅改清单冒充新来源。

本次暂存目录分别为 `dist/releases/app/0.12.0/` 和 `dist/releases/modules/tower-assistant/0.1.7/`。暂存仅生成本地附件和清单，不执行 Git 写入或网络发布；已有版本目录拒绝覆盖。若已有输出有冲突，先保留并检查，不覆盖不可变版本。

App 使用标签 `v0.12.0`，独立塔防使用 `modules/tower-assistant/v0.1.7`。按审核后的明确文件清单创建各自 Release，并在 [下载索引](downloads.md) 保留版本化入口。不得递归上传整个 `dist`、fixture、缓存或个人模块目录。标签、附件上传、远端下载与 SHA-256 核验是后续独立步骤；本地暂存成功不代表发布完成。

旧历史和标签不覆盖。`version:tags` 只打印宿主历史对应标签，不自动生成模块标签，也不执行 Git 写入。源码提交、标签、包构建、桌面验证与公开发布分别授权、分别记录。

每次执行与改动相关的版本/类型/测试/构建检查。没有本次明确授权不得提交推送 Git/P4/SVN；允许本地出包不等于允许上传 GitHub Release。
