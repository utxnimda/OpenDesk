# OpenDesk

OpenDesk 是 NimdaDesk 桌面工作台的公开源码仓库。本仓库从经过检查的源码快照建立全新 Git 历史，不导入旧提交、标签或生成产物。应用名称、标识和数据目录仍保持 NimdaDesk，既有版本记录保留其历史含义。

[![Check](https://github.com/utxnimda/OpenDesk/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/utxnimda/OpenDesk/actions/workflows/check.yml)

面向 Windows 的本地桌面工具台，用于挂载和开发独立功能。基于 **Tauri 2 + Vue 3 / TypeScript + Python worker**，通用框架管理窗口、设置和后台通信，业务模块各自负责界面与逻辑。

**当前宿主版本：v0.5.0** · [更新记录](CHANGELOG.md) · [页签包](docs/module-packages.md) · [版本维护](docs/versioning.md)

## 页签包与测试台

应用自带「塔防山贼助手 0.1.2」与「模块测试台 1.0.2」两个独立页签包。打开「功能管理」可导入本地 `.ndpkg` 文件，查看导入记录、启停页签、选择已安装版本和查看包的 Git 来源。导入成功只增加候选版本，不自动替换正在使用的版本；同版本异摘要被拒绝，失败包的原件和具体错误保留在应用数据目录。

页签前端是独立构建的 HTML/JS/CSS，后台是每包自己的 worker 和依赖。切换版本同时更换对应前后端，不重编译宿主；其他页签的运行实例保持独立。禁用保留所选版本和数据。首版数据 schema 必须精确兼容，不执行自动迁移、回退或删除数据。

测试包由 `npm run build:module-fixtures` 生成：

| 包 | 用途 |
|---|---|
| `dist/module-fixtures/test-lab-1.1.0.ndpkg` | 导入后将测试台从 1.0.2 切到 1.1.0，再切回；对照前端版本与后台 echo |
| `dist/module-fixtures/test-lab-2.0.0-incompatible.ndpkg` | host API 不兼容，应保留原件并拒绝，不影响当前测试台 |
| `dist/module-fixtures/test-extra-1.0.0.ndpkg` | 导入全新页签，再选择版本并启用 |
| `dist/module-fixtures/test-lab-1.2.0-ui-failure.ndpkg` | 合法纯前端包故意不发送SDK hello；应超时、记录错误并恢复此前可用版本 |

上述额外包是明确的测试 fixture，不是已经发布的正式版本。模块测试台提供计数器和后台 echo，便于观察页签切换、独立进程与版本配套；它不接触真实游戏数据。

当前包含「塔防山贼助手」：只读本地存档，分析渡劫布局和仓库灵物，生成前后对比图与可手工执行的迁移步骤。应用内的「版本与更新」页可查看各项功能版本、按功能筛选更新，以及构建时的 Git 提交与工作树状态。

## 功能与版本

宿主/core 版本历史由 [config/version.json](config/version.json) 维护。下表保留历史能力分类；可安装页签的实际版本以其不可变包清单与本机选择为准，不能从子功能版本推断当前运行的包。

| 稳定功能 ID | 功能 | 当前功能版本 | 最近更新所属应用版本 |
|---|---|---|---|
| `core.workbench` | 桌面工作台：主题、设置、诊断、托盘及快捷键 | 0.3.0 | 0.5.0 |
| `core.versioning` | 版本与更新追踪 | 0.1.1 | 0.2.1 |
| `core.module-packages` | 页签导入、开关与独立版本管理 | 0.1.0 | 0.3.0 |
| `tower.save-reader` | 存档与仓库读取 | 0.1.0 | 0.2.0 |
| `tower.tribulation-plan` | 渡劫迁移规划 | 0.1.0 | 0.2.0 |
| `tower.map-details` | 对比地图与对象详情 | 0.1.0 | 0.2.0 |

宿主与页签分别追溯 Git。拟发布页签使用 `modules/<模块ID>/v<版本>` 标签；当前本地构建包含未提交修改时，会明确记录 dirty 状态。构建脚本不自动创建标签、提交、推送或发布包；版本库操作遵循当次用户授权。

## 塔防山贼助手

侧栏打开「塔防山贼助手」，填写游戏安装目录，读取最近存档，再生成渡劫布局方案。若工程位于游戏目录下，工具可自动识别父目录；发布程序也可以手工指定游戏目录。

目标依次为：达到当前约束下可用灵物的最高渡劫台灵气，再减少人物与建筑/设施迁移；换装与距离作为进一步优化项。仓库灵物和已摆灵物共同参与计算，使用现有置物台。界面明确说明灵气上界、迁移最优性是否已证明，不把启发式结果标成全局最优。

工具只读存档与游戏规则，提供前后地图、编号操作清单、仓库清单及 SVG 对比图导出。地图显示对应人物与灵物图标、名称和基础灵气；点击对象查看人物职业/装备、灵物五行及前后变化，同格对象可切换。它不连接游戏运行进程、不修改存档，也不自动执行迁移。存档时间不等于当前游戏实时状态；请在游戏中正常保存后重新读取。详细规则与限制见 [游戏模块说明](docs/tower-assistant.md)。

## 外观配色

在「设置 → 外观」可以通过主题卡片的微缩界面预览比较配色，选择后即时预览，点击「保存设置」后持久化。原有浅色、深色和跟随系统保持可选；跟随系统仍为默认设置。

| 外观 | 配色方向 |
|---|---|
| 樱花浅色 `sakura` | 以近白面板和克制的粉色底衬区分层级，低饱和莓粉用于选中与主要操作 |
| 海盐蓝 `ocean` | 清浅蓝灰背景与蓝色强调，适合日常工具工作区 |
| 松石绿 `forest` | 柔和绿灰背景与青绿色强调，保持长时间阅读的平静感 |
| 暖砂岩 `sand` | 暖米灰背景与棕金色强调，减少冷白界面的生硬感 |
| 石墨夜 `graphite` | 中性深灰层级与清晰强调色，提供低亮度的深色方案 |

樱花配色最初参考 [NimdaClaw sakura](https://github.com/utxnimda/NimdaClaw/blob/76f4599d176502caaa1b9189c354e8c3e207f0ef/apps/framework/frontend/styles/app.css#L570-L639)，此版按 NimdaDesk 的页面层级与控件进一步调整。

具体复审结论、颜色职责和扩展规则见 [外观与配色](docs/appearance.md)。

新随附的塔防助手 0.1.2、测试台 1.0.2 支持上述外观同步。旧包继续收到兼容的基础浅色或深色主题：石墨夜传递 `dark`，其余彩色外观传递 `light`；认识具体外观的旧包仍可按自身版本呈现。应用升级保留用户固定的包版本，如需页签内部显示本次配色，在「功能管理」手动选择对应新版。

## 桌面基础

- 工作区、浅色/深色/跟随系统及五种配色外观，以及真实宿主运行诊断。
- 显式模块声明、校验、导航和页面懒加载；框架不导入具体业务模块。
- Rust 宿主统一设置读写；格式带版本，暂存后替换，损坏/未来格式不被默认设置覆盖。
- Python worker 按需启动；stdio v1 握手、有限方法路由、超时、错误与退出。
- Windows Job Object 仅管理宿主拥有的 worker。正常业务校验失败不会销毁健康进程。
- 单实例唤起、托盘、关闭时隐藏到托盘（可配置）、Ctrl+Shift+Space 唤起（冲突会写日志）。
- 开发示例默认关闭，并从生产前端构建排除。
- 本地日志只记录诊断字段；没有遥测、后台 AI 请求或自动联网安装插件。

这些是实现范围；类型检查、构建、桌面运行与安装测试分别记录在 [验证记录](docs/verification.md)。第三方插件市场、持久任务引擎、本地 HTTP API 和自动更新不在当前实现范围。

## 从源码启动

目标平台为 **Windows x64**。需要 Node 22.12+、Python 3.11+、Rust 工具链、Visual Studio C++ / Windows SDK 和 WebView2 Runtime。

Rust 版本由 `rust-toolchain.toml` 固定。若本项目存在 `.tools/cargo/bin`，启动脚本只对当前子进程启用对应工具链，不修改全局 PATH；没有该目录时使用正常安装的 Cargo。

```powershell
git clone https://github.com/utxnimda/OpenDesk.git
cd OpenDesk
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packaging/requirements-build.txt
$env:NIMDADESK_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm run build:worker
npm run build:modules
npm run dev
```

上面的环境变量只影响当前终端。也可在忽略入库的 `.local/dev.json` 中保存本机 Python 路径；以下仅为路径示例：

```json
{
  "python": "C:\\Dev\\OpenDesk\\.venv\\Scripts\\python.exe"
}
```

通用 Python worker 仅使用标准库；游戏模块通过锁定的 Pillow 解码本机游戏图集，裁出小图标。运行依赖见 [requirements-runtime.txt](packaging/requirements-runtime.txt)，构建依赖文件包含它。游戏安装包与美术资源不随源码或应用分发。

实际机器路径只保存在本机配置。诊断工具可检查工具链：

```powershell
npm run doctor
```

## 启动与检查

从项目根执行；也可双击 `scripts/run.cmd`：

```powershell
npm run dev
```

| 命令 | 用途 |
|---|---|
| `npm run dev:web` | 127.0.0.1:1420 浏览器预览，明确不提供桌面能力 |
| `npm run build:modules` | 构建自带页签包及 App 默认版本清单 |
| `npm run build:module-fixtures` | 构建新版本、不兼容版本及新增页签测试包 |
| `npm run check` | 版本一致性、类型、架构边界、前端与 Python 子进程测试 |
| `npm run version:check` | 检查版本历史、各构建版本与生成的 CHANGELOG 一致 |
| `npm run version:prepare -- --file .local/next-release.json` | 校验并加入一次更新草稿，同步派生文件 |
| `npm run version:sync` | 从版本清单同步构建版本并生成 CHANGELOG |
| `npm run version:tags` | 打印当前应用及本次变更功能的 Git 标签，不创建或推送标签 |
| `npm run desktop:check` | Rust 静态编译检查 |
| `npm run desktop:test` | Rust 测试 |
| `npm run build` | 前端、Python 目录包、Tauri 发布构建、NSIS、便携目录 |

发布包的真实桌面链路检查可运行 `.\scripts\smoke.ps1`。它使用独立的框架测试数据目录，从其他工作目录启动 EXE，验证 WebView、IPC、设置读写与打包 worker 启停，并在完成后退出。

模块导入与真实版本切换检查使用 `.\scripts\module-smoke.ps1`，默认读取 `dist/NimdaDesk/NimdaDesk.exe` 与 `dist/module-fixtures`；可用 `-Executable`、`-Fixtures` 显式指定。先执行 `npm run build:module-fixtures` 准备四个测试包；只补建UI加载失败包可执行 `node scripts/module-pack.mjs ui-failure-fixture`。测试以全新的 `.local/module-smoke` 数据根启动 `--module-smoke`，最多等待140秒，核对报告、退出码及该测试根无遗留模块worker；超时只停止本次测试宿主，不停止其他正常实例。源码中有此测试入口不代表已经运行通过。

首次运行 Rust 检查前需要 `npm run build:worker` 和 `npm run build:modules`，因为桌面构建描述引用 worker 与自带包资源。首次取得仓库应使用已有 Cargo.lock；不要在正常检查时主动更新锁文件。

生产包为 `dist/NimdaDesk/NimdaDesk.exe`，必须连同同目录的 `nimdadesk-worker.exe`、`_internal` 和 `bundled-modules` 使用。便携压缩包应完整解压后启动，不能只复制 EXE。NSIS 安装器位于 `apps/framework/desktop/src-tauri/target/release/bundle/nsis`。生成安装器不代表已安装、已签名或已发布；上传 Git 源码和标签也不会自动创建 GitHub Release 或上传安装包。

便携目录重建时若存在不属于本次产物清单的文件或目录，构建会在复制前拒绝，避免旧依赖或个人文件混入新包。请将旧输出移走后重新生成，或先检查并手工清理；脚本不自动删除未知内容。

## 配置与运行数据

默认数据根由 Tauri `app_data_dir` 决定，运行诊断页显示实际路径。可用绝对路径环境变量 `NIMDADESK_DATA_DIR` 显式覆盖，适合隔离测试。相对路径会拒绝。

该覆盖值只隔离本框架的设置、诊断日志和 smoke 结果。WebView2 浏览器缓存仍使用 Tauri 的默认 `app_local_data_dir`，不宣称完全隔离所有运行缓存。首版不保存窗口位置，避免引入另一套配置写入机制。

- `config/framework/settings.json`：真实设置，唯一写入方为 Rust。
- `logs/framework.jsonl`：当前诊断日志；超过约 2 MiB 时保留一份 previous 文件。
- `smoke-result.json`：仅使用 `--smoke-test` 时生成的桌面链路结果。

源码中的 `config/framework/defaults.json` 是共享默认模板；前端只用它建立初始草稿。读取真实设置失败时禁止保存默认草稿。

请勿将真实游戏存档、`package.nw`、游戏图集、导出的私人布局图、日志、凭据或本机配置加入 Git。`.local/` 用于本机验证数据和更新草稿；`.venv/`、构建产物与运行数据也不应提交。测试使用合成数据，读取用户游戏文件的验收结果只保存在本机。

## 目录

```text
apps/framework/frontend/           Vue 外壳、公开契约与组合入口
apps/framework/desktop/src-tauri/  Rust 宿主与 capabilities
apps/framework/worker/             Python 协议及运行时
apps/composition/worker/           应用 worker 装配入口
apps/features/tower_assistant/     游戏读取、规划、对比地图模块
apps/features/                    其他内置模块
apps/features/_examples/          仅开发环境的扩展示例
config/framework/                 可分发默认模板
config/version.json               应用与功能版本的权威历史
tests/                            契约和行为测试
scripts/                          开发、检查、打包编排
packaging/                        打包依赖锁定
docs/                             工程规范、架构和验证
```

新增普通页面集中在模块目录内完成。只有增加新的宿主能力或 worker 命令时，才修改相应公开契约与执行边界。具体流程见添加模块文档。

## 开发约定

开始修改前阅读 [AGENTS.md](AGENTS.md)、[工程基本原则](docs/principles.md) 和 [架构与边界](docs/architecture.md)。新增模块按 [扩展流程](docs/adding-a-module.md) 接入；新增或更新功能按 [版本维护](docs/versioning.md) 记录。版本脚本不会自动提交或推送 Git，版本库操作遵循当次明确授权。

后续功能交付按以下顺序进行：

1. 完成实现与验证，在 `.local/next-release.json` 记录实际变更；新增功能登记稳定 ID，已有功能沿用原 ID，只升级本次改变的功能。
2. 执行 `npm run version:prepare -- --file .local/next-release.json`，同步应用版本、功能历史和 CHANGELOG。
3. 更新 README 的功能说明、版本表及受影响的使用文档，执行 `npm run check` 和本次改动所需的构建或桌面验证。
4. 获得当次 Git 授权后提交源码与文档；用 `npm run version:tags` 查看应创建的标签，推送后检查 [GitHub Actions](https://github.com/utxnimda/OpenDesk/actions/workflows/check.yml) 结果。

仅补充文档、没有功能行为变化时，使用 `docs:` 提交，保留现有应用和功能版本，不重复创建版本标签。CI 当前覆盖版本、类型、架构边界、自动化测试及前端构建；桌面与安装验收另见验证记录。

NimdaClaw 只提供分层参考，dyd2 只提供组件化界面参考，没有迁移两者的业务代码。具体已执行检查和未验证项目见 [验证记录](docs/verification.md)，不要用一次编译成功代替桌面或安装验收。
