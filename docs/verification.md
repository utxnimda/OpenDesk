> 以下记录属于源码迁移前的历史验证，不表示 OpenDesk 已重新执行同样检查。旧运行编号、提交和工作区路径仅用于保留原始验证上下文。

# 验证记录

## 2026-10-19：v0.5.0 本地提交与交付门禁

用户已确认当前效果并明确授权打包、提交本地 Git。本轮提交范围是模块包框架、测试页签、外观改进及配套测试/文档，共65个文件；不含运行数据、游戏资源、截图、依赖、安装器或其他生成二进制。以下历史段落中的“未提交”描述各次实现验收当时的状态，后续提交以 Git 历史为准。

提交前重新执行 `npm run check`：33项版本测试、63项前端测试、69项Python测试通过，类型与架构检查通过；12项Rust测试及fmt/Clippy通过。待提交文件与忽略规则已复核，`git diff --check`通过。

交付流程从提交后的干净工作区构建宿主，版本页记录该提交。塔防助手0.1.2、测试台1.0.2沿用此前已验证的不可变包及原source身份，不为了改变dirty标记而重写同版本包。外部交付清单分别记录宿主提交、包的原始来源、版本和SHA-256，不能把二者冒充同一次构建。

最终安装器、便携ZIP、独立模块包与校验清单位于忽略入库的 `dist/releases` 中，实际打包与发布程序运行证据保存在 `.local/release-0.5.0`。安装器生成和本地提交不代表已安装、签名、上传GitHub Release或推送远端；本轮不执行这些操作。

## 2026-10-19：v0.5.0 配色细节复审与四套新外观

根据实际粉色预览复审区域层次、强调色、次级按钮、主题卡和窄窗口：减少粉色铺底，调整为低饱和莓粉；独立定义普通边框、控件边框和主按钮文字。新增海盐蓝、松石绿、暖砂岩、石墨夜，共七种实际配色及跟随系统选项。主题卡使用同源颜色变量展示微缩界面，保存按钮移至设置页标题旁；具体判断见 [外观与配色](appearance.md)。

| 验证 | 实际结果 |
|---|---|
| 源码与回归 | 33 项版本测试、63 项前端测试、69 项 Python 测试通过；类型与60源文件架构检查通过 |
| 原生设置 | 12 项 Rust 测试通过；主题保存/新store重读覆盖全部8种设置，未知主题保护保留；fmt/Clippy通过 |
| 颜色 | 正式工程 themes.css 的56组关键不透明色对通过设定的4.5文字/3控件阈值；这是指定色对检查，不是完整可访问性审计 |
| 浏览器 | 实际预览优化后的樱花与四种新增外观；860px内嵌正式预览采用两列，无名称挤出；Tab/空格成功从樱花切到海盐蓝 |
| 发布产物 | 0.5.0便携目录和31.88MiB NSIS安装器生成；未运行安装器 |
| 发布程序 | 隔离数据根预置 graphite，真实模块smoke和核心smoke均通过；两次实际启动及设置往返后仍保存graphite，无遗留测试worker |

实际模块链路验证了测试台 `1.0.2 → 1.1.0 → 1.0.2`、不兼容原件保留、加载失败恢复、旧token拒绝、开关与新增页签。协议1继续发送合法的基础light/dark；graphite回退dark，由契约测试明确覆盖，旧包不会因未知appearance拒绝握手。像素观感由浏览器截图验证，不能据此宣称所有原生窗口控件均已人工点验。

两份自带包为塔防助手0.1.2、测试台1.0.2。主题变量提取到themes.css，与预览和包构建共用，颜色文件已纳入模块缓存指纹。测试使用隔离目录，未改写用户旧包选择或正常外观配置；旧便携目录保留在 `dist/NimdaDesk-before-palettes-20261019-173355`。

本机证据（忽略入库）：

```text
.local/palette-verification/source-checks.log
.local/palette-verification/native-tests.log
.local/palette-verification/clippy.log
.local/palette-verification/release-build.log
.local/palette-verification/check-palettes.mjs
.local/palette-verification/report.json
.local/palette-verification/nimdadesk-palettes-sakura.png
.local/palette-verification/nimdadesk-palettes-graphite.png
.local/palette-verification/nimdadesk-palettes-narrow.png
.local/palette-verification/run-palette-smoke.ps1
.local/palette-verification/desktop-20261019-173642/module-smoke-result.json
.local/palette-verification/desktop-20261019-173642/smoke-result.json
```

未提交、推送、打标签或发布。没有运行干净机器安装/卸载与长期常驻验收。

## 2026-10-19：v0.4.0 樱花浅色外观

新增 NimdaClaw 原 `sakura`／「樱花浅色」配色：`#faf5f7` 背景、白色面板、`#e8d4dc` 边框、`#db2777` 强调色。保留 NimdaDesk 布局与字体，侧栏固定颜色改为主题变量。设置支持即时预览、明确保存，以及下次启动恢复。原参考来源及旧页签兼容行为见 README。

主题通信保留协议 1 的 `theme: light|dark`，可选 `appearance: sakura` 由新 SDK 识别，旧 SDK 继续使用浅色。新随附包升级为塔防助手 0.1.1、测试台 1.0.1；不覆盖旧包摘要，也不自动替换用户旧版本选择。

| 验证 | 实际结果 |
|---|---|
| 源码 | 33 项版本测试、56 项前端测试、69 项 Python 测试通过，类型与 60 源文件架构检查通过 |
| Rust | 12 项测试通过，包括四种主题保存后以新 store 读取、未知主题保留原件；fmt/Clippy 通过 |
| 浏览器预览 | 实际选择樱花浅色，检查侧栏/设置卡/面板/文字；切回深色正常；保存按钮在无宿主预览中继续禁用 |
| 发布构建 | 0.4.0 便携版及 NSIS 安装器生成成功；未运行安装器 |
| 隔离桌面验证 | 全新数据根预置 sakura，实际发布 EXE 完成模块 smoke 和核心 smoke；两次进程启动及设置往返后仍保存 sakura，退出后无测试 worker 遗留 |

模块 smoke 在这次主题设置下验证了 `1.0.1 → 1.1.0 → 1.0.1` 的真实 iframe/worker、旧 token 拒绝、不兼容原件保留、坏页面恢复、新页签和开关；没有以静态截图代替运行证明。浏览器截图展示外观，不宣称原生窗口所有控件已逐项人工点击。

本机证据（忽略入库）：

```text
.local/sakura-verification/source-checks.log
.local/sakura-verification/native-tests.log
.local/sakura-verification/clippy.log
.local/sakura-verification/release-build.log
.local/sakura-verification/settings-preview.png
.local/sakura-verification/run-sakura-smoke.ps1
.local/sakura-verification/desktop-20261019-171852/module-smoke-result.json
.local/sakura-verification/desktop-20261019-171852/smoke-result.json
.local/sakura-verification/desktop-20261019-171852/config/framework/settings.json
```

用户正常配置没有改为测试主题；默认仍跟随系统，用户可自行保存外观。旧便携目录保留于 `dist/NimdaDesk-before-sakura-20261019-171603`；旧默认压缩包保留于 `.local/sakura-verification/previous-bundled`，已安装旧版本不受影响。没有 Git 提交、推送、打标签或发布。

## 2026-10-19：v0.3.0 本地独立页签包与模块测试台

正式工程 `E:\Workspace\Fun\NimdaDesk` 已接入 `.ndpkg` 本地导入、独立前端/worker、版本选择、启停、失败原件保留与就绪前失败恢复。App 默认包含 `tower-assistant@0.1.0` 和 `test-lab@1.0.0`。本轮是基于 `327e3a0577f3ccbe2181d91806b96cf46d2f6562` 的未提交构建，来源如实标记 dirty；没有提交、推送、打标签或发布。

| 检查 | 实际结果 |
|---|---|
| 源码检查 | 类型、60 个源文件架构边界、33 项版本规则测试、54 项前端测试、69 项 Python 测试通过；无跳过 |
| Rust | 11 项测试通过；`cargo fmt --check` 和 `cargo clippy --locked --all-targets ... -- -D warnings` 通过 |
| 模块产物 | 两份自带包、四份明确标识的 fixture 生成；含 worker 的产物真实验证身份/回显/非法参数/退出 |
| 发布构建 | Vue、核心 worker、Tauri release、便携目录以及 31.88 MiB NSIS 安装器生成成功；未执行安装器安装 |
| 发布 EXE 核心 smoke | WebView/Vue 挂载、IPC、设置往返、参数错误后进程保留、worker 停止后重启均通过；退出码 0，无残留核心 worker |
| 发布 EXE 模块 smoke | 独立 iframe 身份确认和真实 MessageChannel→Tauri→冻结 worker 回显通过；见下方详细证据 |

模块测试使用全新隔离框架数据目录，从系统临时目录启动实际便携 EXE，清除源码 Python/worker 覆盖。测试先等待页面自身自动 echo，之后再做后端身份核对，不用界面上的版本文字替代执行证据。

- 实际前端与后台依次使用 `test-lab@1.0.0 → 1.1.0 → 1.0.0`，返回 marker 分别为 `v1-baseline → v1.1-enhanced → v1-baseline`；后台 PID 分别为 47172、42256、22372。
- 导入 `hostApi=99` 的 2.0.0 包被拒绝，原始文件仍存在，原活动实例不变。
- 导入合法但不发送 SDK hello 的 1.2.0 页面，真实等待页面超时后自动恢复 1.0.0；恢复 worker PID 56220，页面重新握手并成功 echo，失败原因保留。
- 切换 B 的过程中，独立 A（塔防助手）的 token 与 worker PID 保持不变。本次只启动 A 的身份握手，没有读取游戏或存档，也没有据此宣称 A 的全部业务操作完成验收。
- 旧版本 token、停用后的当前 token 均不能继续调用；等待父界面实际关闭旧 iframe 后，重新启用成功。
- 导入新 ID `test-extra@1.0.0`，选择并启用后，真实新页签与独立 worker PID 23384 成功回显。
- 关闭模块管理器后，从同一 registry 创建新管理器，精确版本选择与启用状态保持；这属于持久化配置重载验证，不冒充跨崩溃事务恢复。
- 最终退出码 0，按本次测试根下的精确路径检查，无残留模块 worker。

本机证据（忽略入库）：

```text
.local/module-implementation/source-checks.log
.local/module-implementation/native-tests.log
.local/module-implementation/clippy.log
.local/module-implementation/release-build-final.log
.local/module-smoke/20261019-170418-246-b0bc83b9/module-smoke-result.json
.local/module-smoke/20261019-170418-246-b0bc83b9/logs/framework.jsonl
.local/smoke/20261019-170447-527/smoke-result.json
```

真实验收曾暴露 Windows 解压后目录落位的暂态拒绝访问；同路径延迟 rename 探针成功且没有修改权限。现仅对同 store 目录 rename 的 Windows 5/32/33 错误进行最多 2 秒有界等待重试，保留路径/阶段/OS 错误上下文，目标存在时绝不覆盖。新增回归验证源和既有目标均保留；修复后的真实首次导入通过。另一轮 smoke 在界面尚未处理停用事件时立即启用导致超时，测试已改为等待真实 iframe 关闭确认，再执行下一步。

当前限制：UI 自动恢复只覆盖当前宿主会话内、就绪确认前的加载失败；不支持跨宿主崩溃的待确认选择事务或自动数据格式迁移。模块 smoke 通过宿主测试协调器调用真实导入入口并使用真实 WebView/worker，不包含手工点击系统文件选择器的验收。安装/卸载、干净机器、长期常驻及所有按钮的人工体验验收仍未执行。

## 2026-10-18：v0.2.1 Windows Git 来源识别修复

首次推送 `v0.2.0` 后，GitHub Actions 39134785704（迁移前运行 ID 39134785704） 在 Git 来源测试中失败：同一 Windows 目录的路径表示不同，被误判为未知仓库。本机复现了小写路径无法识别；改用 BigInt 文件系统目录身份比较，兼容大小写与短路径别名，同时保留嵌套源码目录的拒绝判断。应用升为 `0.2.1`，仅 `core.versioning` 升为 `0.1.1`，已推送的旧版本标签保持不变。

本次提交前，`npm run check` 通过：33 项版本测试（包含大小写路径回归）、41 项前端测试、类型及 43 个源文件架构边界检查；69 项 Python 测试中 68 通过、1 项因本机符号链接权限跳过。`npm run build:web` 与 `git diff --check` 通过。远端本次结果以该提交对应的 Actions 为准；本段记录不提前宣称推送后检查已通过。

推送后补充：提交 `8a097a2ab5e4d5030567a70ee5679d04f7f0e3aa` 的 main 检查（迁移前运行 ID 39135318788） 与 v0.2.1 标签检查（迁移前运行 ID 39135318627） 均已成功完成。浏览器版本页复核显示 `v0.2.1`、5 项功能、3 个应用版本，Git 短提交号 `8a097a2ab5e4` 与干净工作区标记一致；版本追踪功能为 `0.1.1`，按该功能筛选可见新增与修复两次更新。此结果对应上述提交，不代表未来提交的检查状态。

## 2026-10-18：v0.2.0 版本体系与 Git 交付准备

应用版本统一为 `0.2.0`，五项功能有各自的稳定 ID 与独立版本。`config/version.json` 生成 CHANGELOG，并同步 npm、Tauri 与 Cargo 的版本；构建时读取 Git 提交及 dirty 状态，客户端没有新增文件或命令执行能力。

| 检查 | 实际结果 |
|---|---|
| `npm run check` | 版本一致性、43 个源文件架构边界及类型检查通过；33 项版本工具测试、41 项前端测试通过；69 项 Python 测试中 68 通过、1 跳过 |
| 版本工具行为 | 临时工程内验证草稿准备、仅变更功能升级、重复/回退/非法版本拒绝、全文件预检、写入失败回滚、锁文件和 CHANGELOG 漂移检测 |
| Git 构建身份 | 临时 Git 工程验证 clean、未跟踪文件、已修改文件、无 Git 的源码目录及嵌套源码目录；未知来源不冒充某个提交 |
| `npm run build:web` | 生产前端构建通过，包含版本清单与构建时 Git 身份 |
| `npm run desktop:test` | NimdaDesk `0.2.0` 编译通过，2 项 Rust 测试通过 |
| 开发桌面 | Tauri 重新编译并启动 `0.2.0`，日志确认 `frontend.ready` 与 IPC 连接 |

正式浏览器预览的「版本与更新」页已实际验证：侧栏版本按钮进入页面，显示 `v0.2.0`、5 项功能、2 个应用版本、真实构建时 SHA 与未提交标记；按地图详情筛选后仅保留该功能的 1 次更新，选择全部恢复完整历史。截图为忽略入库的 `.local/version-page.png`、`.local/version-feature-history.png`。浏览器页面验证不等同于原生窗口按钮验收。

Python 跳过项仍是当前 Windows 账户缺少创建符号链接权限。此次未重跑安装器、完整 release 构建或干净机器安装；上一节之前的真实游戏/冻结 worker 证据保留在下方，不重复冒充本轮执行。

GitHub Actions 配置在主分支、应用标签和 PR 上执行版本、类型、边界、测试与前端构建，应用标签另核对清单版本。工作流只授予仓库读取权限，官方 Actions 依赖固定到提交；不上传游戏资源、存档或安装器。在本地验收完成时，远端运行尚待推送后触发，不能用本地结果代替远端结果。

本轮用户明确授权提交并推送当前源码与版本体系。暂存范围仅包含工程源码、配置、测试和文档；已复核 `.local/` 快照、游戏图标、依赖目录、生成产物和本机路径配置均未入库。最终提交及标签以 Git 历史和远端引用为准。

## 2026-10-18：对比图图标与对象详情

本轮新增游戏原生人物/灵物图标、地图物品名称与基础灵气、人物职业/工作/穿戴、灵物等级/品质/五行、前后位置与物品绑定详情。原有规划算法没有修改。

| 检查 | 实际结果 |
|---|---|
| `npm run check` | 类型与 41 个源文件的架构边界检查通过；33 项前端测试通过；69 项 Python 测试中 68 通过、1 跳过 |
| Python 跳过项 | Windows 当前账户无法创建符号链接；其余存档路径、图标白名单、资源大小和裁剪边界测试通过 |
| `npm run build:web`、`npm run build:worker` | 前端生产构建与含固定 Pillow 13.0.0 的 worker 目录包生成成功 |
| 源码及冻结 worker | 均从系统临时目录启动，清除 Python 源码路径覆盖；握手、读取最新存档、分析、分批图标读取和退出通过 |
| 图标读取 | 124 个不同的人物、灵物、装备图标全部命中，无占位警告；32/32/32/28 四批，冻结 worker 各批 0.620–0.835 秒，最大 290,182 字节，低于 8 秒/2 MiB 协议限制 |
| 开发版桌面 | 更新 worker 后 Tauri 自动重新编译启动；21:08:15 日志记录 `frontend.ready`，Vue 挂载和 IPC 连接成功 |

此次最新真实存档为 `save:ea3save.dat`，索引时间北京时间 **2026-10-18 20:57:17**，游戏内 49 年第 121 天 9:00。读取 44 人、88 件仓库灵物、34 件已摆灵物；结果为 **615.8 → 992.0**，5 台、3 处设施、2 人搬动，10 次灵物绑定变化、26 个有序步骤。人物装备按实际装备反向绑定校验，前后图分别按各自 `itemId` 显示，卸下后的台显示为空台。

游戏包与 8 个 `.dat` 文件在完整读取前后的 SHA-256 一致。冻结验收脚本、快照、PNG 图标缓存及报告留在忽略入库的 `.local/verify_tower_icons.py`、`.local/tower-inspection.json`、`.local/tower-analysis.json`、`.local/tower-visuals.json`、`.local/tower-icon-verification.json`。最新 `.local/tower-comparison.svg` 包含内嵌 PNG、人物名称、物品灵气和悬停详情，大小 1,243,066 字节；SVG 是静态图，点击详情在应用内使用。

Chrome 隔离回放页面使用正式 Vue 组件和以上真实快照，明确标注没有连接游戏/worker。已实际点击生成并验证图标、名称、灵气、人物与灵台选择、Enter/Space 键、双图同对象高亮、详情跳转、同格对象切换和关闭。详情展开前后地图面板均为 `1183×488`；关闭后详情与高亮均清除。截图为 `.local/tower-icons-comparison.png`、`.local/tower-person-details.png`、`.local/tower-spirit-details.png`。这项验收不等同于原生桌面 IPC 点击；本轮未重新执行原生窗口截图。历史开发日志有一条 ResizeObserver 通知警告，本轮增加仅在尺寸变化时更新布局，回放没有可见错误，未做浏览器控制台专项收集。

本轮未执行 NSIS 安装或完整发布构建，未提交或推送 Git。下面的首次模块验收使用当时较早的存档，结果保留为历史证据。

## 2026-10-18：塔防山贼助手

本轮在中文路径的 Windows 工程中验证，使用 Node 24.14.1、Python 3.13.9、项目固定 Rust 1.99.0。以下是本轮实际结果；后面的 2026-10-15 内容为原项目历史记录，不代表本轮重新执行过发布安装测试。

| 检查 | 实际结果 |
|---|---|
| `npm run doctor` | Node、Python、Rust、Cargo 均通过 |
| `npm run check` | 类型检查、38 个源文件的架构边界检查通过；27 项前端测试通过；54 项 Python 测试中 53 通过、1 跳过 |
| Python 跳过项 | 当前 Windows 账户无法创建实际符号链接；路径穿越及非法存档编号拒绝测试已通过 |
| `npm run desktop:test` | 2 项 Rust 测试通过 |
| `cargo fmt --check` | 通过 |
| `cargo clippy --locked --all-targets ... -- -D warnings` | 通过 |
| `npm run build:web`、`npm run build:worker` | Vue 生产构建和包含游戏模块的 Python 目录包生成成功 |
| 冻结 worker 验收 | 从系统临时目录启动，清除源码 Python 路径覆盖，依次完成握手、发现目录、读取、分析、退出；约 1.32 秒，最大响应 98,307 字节 |
| 开发版桌面启动 | Tauri 编译通过；实际日志记录 `frontend.ready`，Vue 挂载且 IPC 连接成功 |

真实存档使用游戏索引时间最新的 `save:ea3save.dat`，北京时间 2026-10-18 16:49:49。读取 94 件仓库灵物、33 件已摆灵物和 34 座现有置物台。规划结果为 **569.3 → 977.6**，搬动 **5 座置物台、3 处林地、2 人**；10 个不同实体、10 次搬动，另有 11 个台的灵物绑定变化，共 28 个有序步骤。

该存档的灵气达到匹配上界，移动数达到可独立证明的下界：16 格中已有 11 台，至少迁入 5 台，另有 5 个占格实体必须让位。未声称步行距离或换装次数最少。对全部步骤另行模拟验证了当步占位、解锁边界、设施所属范围、物品唯一性、库存守恒与仓库中间容量。

对 `package.nw` 与两个存档目录内全部 8 个 `.dat` 文件读取前后计算 SHA-256，全部一致。只读验收数据、报告和导出图保存在忽略入库的 `.local/tower-inspection.json`、`.local/tower-analysis.json`、`.local/tower-verification.json` 和 `.local/tower-comparison.svg`，没有改写游戏或存档。

浏览器已验证正式入口的无宿主提示与禁用操作，以及真实 SVG 对比图的完整显示。另用 `.local/tower-ui-verification.html` 加载正式 Vue 组件，并明确标记只回放已验证的真实 JSON、无游戏或 worker 连接；点击生成后显示上述指标和 28 步，选择第 12 步可定位地图、联动迁移箭头。此隔离页面不进入生产构建，也不代表桌面 IPC 点击验收。

地图适配修正后，单幅箭头视图在 `1183×425` 面板中显示 `425×425` SVG，默认无滚动溢出；放大后才滚动。截图记录为 `.local/tower-ui-verification.png` 和 `.local/tower-ui-arrow-verification.png`。SVG 生成及文件渲染已验证；导出按钮的实际下载调用被中断，未确认浏览器或 WebView 的下载落盘流程。

原生 Windows 窗口截图在重新选择窗口后仍报 `window capture timed out: timed out waiting on channel`，因此不能把桌面启动及 worker 验证写成实际点击按钮的端到端验证。本轮未运行 NSIS 安装、完整 release 构建或安装器验收；未提交、推送或发布。

## 2026-10-15：初始化历史记录

日期：2026-10-15。此文件记录实际执行证据，不能由源码存在推断运行成功。

## 环境

Windows 11 x64；Node 24.14.1；Python 3.12.14；Rust 1.99.0；Visual Studio 2022 C++ 工具链；WebView2 Runtime 155.0.4283.45。

## 已完成

- TypeScript / Vue 类型检查通过。
- 静态架构边界检查通过：前端 import、模块依赖、宿主访问集中化、Python AST、命令命名与 capabilities。
- 前端 13 项行为测试通过。
- Python worker 9 项真实子进程及边界测试通过。
- 前端生产构建通过；即使显式设置开发示例开关，生产产物也不包含示例页面。
- 浏览器预览：空工作台、设置主题、运行诊断、开发示例懒加载验证通过；浏览器模式不伪造桌面能力。

## 正式工程集成结果

在 `E:\Workspace\Fun\NimdaDesk` 执行：

| 检查 | 实际结果 |
|---|---|
| `npm run check` | 通过：类型、静态边界、13 项前端测试、9 项 Python 测试 |
| `cargo clippy --locked --all-targets ... -- -D warnings` | 通过，无警告 |
| `cargo test --locked ...` | 2 项 Rust 测试通过：设置持久化/未来版本保留、有界协议读帧 |
| `npm run build` | 前端、Python onedir、Tauri release、NSIS 和便携目录全部生成成功 |
| `scripts/smoke.ps1` | 真实发布 EXE 启动、WebView 挂载和 IPC 成功，设置往返保存成功 |

发布目录：`dist/NimdaDesk`，包含 58 个文件。安装器：`apps/framework/desktop/src-tauri/target/release/bundle/nsis/NimdaDesk_0.1.0_x64-setup.exe`。安装器已生成，本轮未执行安装或发布。

桌面 smoke 的本机证据：

```text
.local/smoke/20261015-162935-155/smoke-result.json
.local/smoke/20261015-162935-155/logs/framework.jsonl
```

该次测试关闭了 1420 开发服务器，清除了源码 Python/worker 路径覆盖，从系统临时目录启动便携版；结果为：

- `frontendMounted=true`，`ipc=true`，`settingsRoundTrip=true`。
- 打包 worker 首次 PID 13988，停止后重新启动 PID 27476；两次均返回 protocol v1 和 Python 3.12.14。
- 一个参数校验失败后，原 worker 仍保持运行，普通业务错误不会被误当成通信故障。
- 最终 `workerStopped=stopped`，宿主退出码 0；按便携版 worker 的精确路径复核，无残留进程。

启动与归属：Windows worker 以暂停状态创建，先加入本应用的 Job Object，再恢复初始线程，以覆盖 Python 虚拟环境启动器及其后续子进程。此实现使用独立进程治理，不构成权限沙箱。

打包检查：便携目录只允许本次清单中的文件和目录；遇到未知旧文件会在复制前失败，不删除未知文件，也不将其混入新包。窗口布局记忆插件未纳入首版，避免绕过框架设置的数据根与损坏保留规则。

## 后续独立验收

- 干净机器上的安装、卸载和 WebView2 引导。
- 托盘点击、热键冲突、多显示器/DPI与长期常驻。
- 宿主异常终止、系统注销/休眠等生命周期故障注入。
- 后续业务模块及外部 Agent/游戏能力。

版本库操作：初始化验证阶段未提交、推送或发布；后续本地提交以 Git 历史为准。
