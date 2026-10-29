# OpenDesk

OpenDesk 是独立的本地桌面工作台，应用标识为 `net.nimda.opendesk`，使用 OpenDesk 名称、构建产物和独立数据目录。本仓库从经过检查的源码快照建立全新 Git 历史，不导入旧提交、标签或生成产物。既有版本历史和验证记录保留原始名称、路径及历史含义，不能代替当前 OpenDesk 的构建与运行验证。

**正式交付（2026-10-26）**：[App 0.18.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.18.0) 与 [塔防助手 0.8.2](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.8.2) 从干净提交 `036ba0ace3583cbff410a0ae1155b46a44386e13` 构建；App 仅随附塔防 0.8.2，独立页签使用字节一致的同一包。2026-10-26 已公开发布，App 6 个附件与模块 4 个附件均已重新下载并核对 SHA-256。 测试包、作品管理器附件和本机数据不在本次交付范围内，旧公开版本及本地开发包保持不变。

[![Check](https://github.com/utxnimda/OpenDesk/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/utxnimda/OpenDesk/actions/workflows/check.yml)

面向 Windows 的本地桌面工具台，用于挂载和开发独立功能。基于 **Tauri 2 + Vue 3 / TypeScript + Python worker**，通用框架管理窗口、设置和后台通信，业务模块各自负责界面与逻辑。

**最新公开版本：[v0.18.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.18.0)**（2026-10-26） · [下载索引](docs/downloads.md) · [更新记录](CHANGELOG.md) · [页签包](docs/module-packages.md) · [版本维护](docs/versioning.md) · [发布手册](docs/releasing.md)

## 正式页签与本地开发测试

当前公开 App 0.18.0 只随附「塔防山贼助手 0.8.2」，该页签也已[独立发布](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.8.2)。历史 [App 0.17.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.17.0) 与[塔防 0.1.9](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.9) 保持不可变。分发清单继续只随附 `tower-assistant`；作品管理器 `nimda-workspace` **[0.3.1](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Fnimda-workspace%2Fv0.3.1)** 保留既有独立发布，按需下载导入。模块测试台和 fixture 仅用于开发验证，部署工作台及其他私有模块不在公开清单内。

页签前端是独立构建的 HTML/JS/CSS，后台是每包自己的 worker 和依赖。切换版本同时更换对应前后端，不重编译宿主；其他页签的运行实例保持独立。禁用保留所选版本和数据。首版数据 schema 必须精确兼容，不执行自动迁移、回退或删除数据。

当前正式塔防版本为 **0.8.2**，宿主为 **0.18.0**。0.8.2 与已完成本地限定范围验证的 0.8.1 功能一致，并已从审阅后的干净提交构建和独立验证；既有 0.8.1 dirty 包及其验证记录保持不可变。主页面采用紧凑表格、透明窗采用游戏条形榜，工作按类型分榜，战斗支持属性多选以及按属性／职业分榜，横向排满后自动换行。保留 0.8.1 的逐次明细修复：显示时携带经 worker 校验的事件快照，点击后仍复算原会话、原序号的工作或伤害。本轮发布不改变个人选择、运行数据或游戏文件；已固定旧版本的用户需在「功能管理」导入并选择 0.8.2，透明窗口同时需要 0.18.0 宿主。

0.7.0 新增复用同一模块 worker 的原生透明排行榜窗口，已完成构建、自动化和通用原生窗口链路验证，游戏上的透明叠加视觉尚未验收。这些结果不代替 0.7.1 的验证；此前 **0.6.1** 也保留为已完成本地限定范围验收的不可变包。塔防继续基于 Git 主线 0.1.9 开发，没有合入本机 0.3.0；保留指定存档的灵物地图、灵气排名和 23 层存档支持，以及人物工作统计、伤害分类、固定人物与公式详情。用户此前确认 0.5.2 修复入口的游戏画面正常、采集初始化成功；此前已只读核对游戏内三份采集脚本与当前源码一致，无需为本次明细修复重装入口；新字段的实际工作/伤害事件与长时间性能尚未完整验证，具体实现范围见[模块说明](docs/tower-assistant.md)。

正式版 **0.17.0** 延续 0.16.0 的 [本地 Web 应用接入](docs/module-web-apps.md)，并支持工程内统一管理状态、外部结构插件的统一构建。作品管理器 **0.3.1** 已纳入本仓库源码，保留原应用界面、嵌入与完整弹出窗口；真实数据在 Git 忽略的独立 SVN 工作副本中。宿主在停用、切换版本和退出前等待已开始的操作结束，旧 `hostApi: 1` 继续兼容。App 与作品管理器均已从干净源码构建并公开发布，10 个远端附件已逐项下载核对；实际验证和限制见[验证记录](docs/verification.md)。

## 作品管理器与统一工程

原 Nimda 的当前代码位于 [`plugins/media-manager`](plugins/media-manager/README.md)，稳定模块 ID 继续使用 `nimda-workspace`，避免改名丢失旧设置。代码、测试、空配置模板和发布元数据由 OpenDesk Git 管理；SVN 数据在 OpenDesc 下按插件分目录，作品管理器使用 OpenDesc/media-manager；`workspaces/media-manager` 的真实资料与配置由独立 SVN 工作副本管理，凭据、日志和缓存不提交。

本机工程入口为 `E:\OpenDesk`。 双击根目录 `OpenDesk.lnk` 启动工具台，`plugins/media-manager/作品管理器.lnk` 启动独立窗口；两者连接同一个工作区，单实例锁避免同时写入。宿主放在 `run/OpenDesk`，其 `opendesk.portable.json` 指向 `../../runtime`；导入的插件运行包、版本选择和设置集中到 `runtime`。公开产物仍输出到 `dist`，不能含此本机标记、运行状态或私人资料。WebView2 缓存沿用系统目录。目录与迁移边界见[统一工程说明](docs/media-manager.md)。

```powershell
# 在 OpenDesk 根目录构建独立插件；依赖初始化见插件 README
npm run build:module -- media-manager
# 原应用的独立启动入口，默认连接工程下的 SVN 工作区
& ./plugins/media-manager/scripts/run-desktop.ps1
```

模块测试台的本地测试包由 `npm run build:module-fixtures` 生成，不属于公开下载清单：

| 包 | 用途 |
|---|---|
| `dist/module-fixtures/test-lab-1.0.7.ndpkg` | 测试台当前源码 baseline，由 smoke 在独立测试根显式导入 |
| `dist/module-fixtures/test-lab-1.1.0.ndpkg` | 导入后将测试台从 1.0.7 切到 1.1.0，再切回；对照前端版本与后台 echo |
| `dist/module-fixtures/test-lab-2.0.0-incompatible.ndpkg` | host API 不兼容，应保留原件并拒绝，不影响当前测试台 |
| `dist/module-fixtures/test-extra-1.0.0.ndpkg` | 导入全新页签，再选择版本并启用 |
| `dist/module-fixtures/test-lab-1.2.0-ui-failure.ndpkg` | 合法纯前端包故意不发送SDK hello；应超时、记录错误并恢复此前可用版本 |

上述五个包仅用于本机开发验证，不上传为 App 附件或独立 Release。`test-lab` 源码属于本仓库开发测试代码，可随源码保留；这不表示其产物有公开分发资格。正常用户升级不会卸载或覆盖先前自行导入的测试台及其他包，仍保留个人版本选择。

当前包含「塔防山贼助手」：存档视图只读分析渡劫布局和全部灵物，生成前后对比图与可手工执行的迁移步骤；正式页签另提供需要显式安装采集入口的工作与战斗统计。应用内的「版本与更新」页可查看各项功能版本、按功能筛选更新，以及构建时的 Git 提交与工作树状态。

## 功能与版本

宿主/core 版本历史由 [config/version.json](config/version.json) 维护。下表保留历史能力分类；可安装页签的实际版本以其不可变包清单与本机选择为准，不能从子功能版本推断当前运行的包。

| 稳定功能 ID | 功能 | 当前功能版本 | 最近更新所属应用版本 |
|---|---|---|---|
| `core.workbench` | 七种界面风格，新增薄荷奶油；独立配色、分类设置与外观保存 | 0.8.1 | 0.19.1（本地开发） |
| `core.versioning` | 版本与更新追踪 | 0.1.1 | 0.2.1 |
| `core.module-packages` | 独立页签、Web 应用、模块第二视图与经授权的只读工具调用 | 0.8.1 | 0.19.1（本地开发） |
| `core.personalization` | 已移除：背景图片、材质与氛围调节；保留历史 ID | 0.2.0 | 0.9.0 |
| `core.companion` | 双模式宠物、共享菜单面板、受控独立菜单窗口与即时角色选择 | 0.6.0 | 0.15.0 |
| `tower.save-reader` | 存档与仓库读取 | 0.1.0 | 0.2.0 |
| `tower.tribulation-plan` | 渡劫迁移规划 | 0.1.0 | 0.2.0 |
| `tower.map-details` | 对比地图与对象详情 | 0.1.0 | 0.2.0 |

宿主与页签分别追溯 Git。拟发布页签使用 `modules/<模块ID>/v<版本>` 标签；当前本地构建包含未提交修改时，会明确记录 dirty 状态。构建脚本不自动创建标签、提交、推送或发布包；版本库操作遵循当次用户授权。

塔防 0.8.2 包内的新增功能沿用 `tower.spirit-inventory`（灵物总览），并登记 `tower.output-statistics`（工作/战斗采集与计时统计）、`tower.damage-explanation`（逐击公式与局部敏感度）、`tower.work-explanation`（实际工作公式与取整步骤）及 `tower.statistics-overlay`（透明统计排行榜）。这些 ID 不改写上表的宿主历史；发布版本也不代表本机已选择或成功激活该包。

## 塔防山贼助手

侧栏打开「塔防山贼助手」，填写游戏安装目录，读取最近或指定存档。当前版本默认进入「灵物总览」，可筛选已摆/仓库、搜索灵物并用地图与全局排名相互定位；切到「渡劫布局」可生成原有规划方案。若工程位于游戏目录下，工具可自动识别父目录；发布程序也可以手工指定游戏目录。

目标依次为：达到当前约束下可用灵物的最高渡劫台灵气，再减少人物与建筑/设施迁移；换装与距离作为进一步优化项。仓库灵物和已摆灵物共同参与计算，使用现有置物台。界面明确说明灵气上界、迁移最优性是否已证明，不把启发式结果标成全局最优。

存档视图只读存档与游戏规则，提供前后地图、编号操作清单、仓库清单及 SVG 对比图导出。地图显示对应人物与灵物图标、名称和基础灵气；点击对象查看人物职业/装备、灵物五行及前后变化，同格对象可切换。它不修改存档，也不自动执行迁移。存档时间不等于当前游戏实时状态；请在游戏中正常保存后重新读取。

「工作与战斗」首先需要填写包含 `game.exe` 与 `package.nw` 的游戏安装目录。目录未填写、状态未返回或检查失败时显示待确认，不代表游戏未运行或入口未安装。确认尚未安装时，先手动退出游戏，再点击「安装采集入口（需重启游戏）」。安装会校验版本、备份原包并修改 `package.nw`，加入随模块提供的固定原创观察脚本；重新启动游戏并连接后，才能开始计时。已安装入口与当前页签脚本不一致时显示更新提示：先退出游戏，移除并恢复原包，再安装新入口并重启游戏。旧入口仍可提供原有字段，导入页签不会自动补齐新字段。移除要求当前包与备份摘要匹配，游戏更新后拒绝用旧备份覆盖。工具不会关闭、启动或附加当前运行的游戏。

工作每个已选类型一张独立排行表，只显示该工作的最近实际量、次数和累计；例如实际输出 100 万而任务仅剩 1 万，仍统计 100 万。战斗多选力（物理）、智（魔法）、敏（穿刺）及其他/未知，可合并一个榜，也可按属性或职业分榜，每榜展示完整伤害、DPS 和占比。普攻、技能、持续伤害的命中次数、平均及最近一次伤害保留在独立细表，命中次数不等于技能施放次数。主页面的排行筛选与明细筛选独立。点击头像或工作最近单次可固定人物与公式详情，刷新、筛选或新会话不会自动关闭；「暂停显示」只冻结逐次明细，不停止采集。汇总最近公式按人物/分类和会话原子读取后固定，历史明细仍严格对应原序号。完整数字模式的输出四舍五入为整数，中文单位模式使用千、万、亿、兆、京的四位有效数字，仅改变显示；后台累计、排名及公式原始输入保留精度。容量、公式与验证边界见 [游戏模块说明](docs/tower-assistant.md)。

「透明排行榜」需配合 **OpenDesk 0.18.0**；0.17.0 宿主仍可使用基本页签功能，透明窗入口会提示需升级。透明窗沿用 0.8.0 引入的布局，采用整行背景条形与多榜换行，继承打开时主页面的排行筛选，之后可独立调整。工作突出累计、最近实际量及次数；战斗突出完整伤害、占比及 DPS。支持人物搜索、职业多选、属性多选、分榜方式、Top 5/10/20 或全部和隐藏零输出。透明窗另保留攻击类别口径，与属性筛选互斥；不推造无法确认的交叉统计。占比分母为每榜 Top N 前的完整筛选结果，条形长度按同榜最大累计量缩放。窗口默认置顶，可拖动、调整大小及将背景不透明度设为 30%–100%；关闭窗口不停止或清空计时，主包关闭或换版会撤销窗口。不提供地区筛选或鼠标穿透。通用原生窗口链路已验证，新布局的实际游戏视觉仍待验收。

## 界面风格与配色

设置分为「风格与配色」「宠物陪伴」「窗口行为」三类。「风格与配色」提供七种布局，`uiStyle` 与配色 `theme` 独立；切换风格不强制改变配色，「使用建议配色」是独立操作。默认使用简洁布局与跟随系统配色，宠物关闭。旧 schema 1 设置可继续读取，旧 `background` / `effects` 仅兼容忽略，不恢复背景或氛围。

| 界面风格 | 布局方向 |
|---|---|
| 简洁 `simple` | 保留原有侧栏、欢迎区和清楚的内容分区 |
| Codex `codex` | 参考专注工作区的留白、轻量侧栏与低边界列表 |
| Cursor `cursor` | 参考 Cursor IDE 的活动栏、工作区标签和紧凑状态栏 |
| 现代 Web `web` | 悬浮侧栏、功能卡片和宽阔内容画布 |
| 二次元 `anime` | 顶部导航、柔彩静态装饰和圆润面板 |
| 深海透明 `glass` | 深蓝黑半透明、青色细边、低圆角紧凑分区与顶部标签栏，建议搭配深色 |
| 薄荷奶油 `mint` | 浅粉与薄荷静态柔色渐变、深青文字、大圆角卡片、中心内容列与轻导航，建议搭配浅色 |

风格只改变呈现方式；工具入口、模块名称、所选版本与环境状态都来自实际数据，不增加 AI 对话、任务队列或代码编辑能力。桌面设置中选择风格或配色会即时预览，点击「保存设置」后只持久化配色、风格和关闭到托盘偏好；离开设置页会恢复尚未保存的外观预览。角色选择独立即时生效，宠物菜单变更不会被尚未保存的外观草稿覆盖。浏览器仅支持当前会话预览，不读取或写入桌面配置，刷新后不保留预览。

「风格与配色 → 配色」继续提供浅色、深色、跟随系统及以下配色，通过微缩界面卡片比较：

| 外观 | 配色方向 |
|---|---|
| 樱花浅色 `sakura` | 以近白面板和克制的粉色底衬区分层级，低饱和莓粉用于选中与主要操作 |
| 海盐蓝 `ocean` | 清浅蓝灰背景与蓝色强调，适合日常工具工作区 |
| 松石绿 `forest` | 柔和绿灰背景与青绿色强调，保持长时间阅读的平静感 |
| 暖砂岩 `sand` | 暖米灰背景与棕金色强调，减少冷白界面的生硬感 |
| 石墨夜 `graphite` | 中性深灰层级与清晰强调色，提供低亮度的深色方案 |

樱花配色最初参考 [NimdaClaw sakura](https://github.com/utxnimda/NimdaClaw/blob/76f4599d176502caaa1b9189c354e8c3e207f0ef/apps/framework/frontend/styles/app.css#L570-L639)，此版按 OpenDesk 的页面层级与控件进一步调整。

具体复审结论、颜色职责和扩展规则见 [外观与配色](docs/appearance.md)。

薄荷奶油保留其他配色选择，深色和石墨夜沿用各自的语义颜色；浅色最接近参考方向。七项风格选择器随可用宽度自动换列。柔色背景由静态 CSS 绘制，不读取图片，也不恢复用户背景与氛围设置。

新版随附模块支持七种风格与配色同步。模块仍使用既有 protocol 1 消息标识，`uiStyle`、`surfaceEffects` 保留可选扩展兼容；当前宿主只由风格生成固定、无动效的表面描述，没有用户材质或浓度调节。旧包可以忽略扩展，并继续收到兼容的基础浅色/深色主题。宿主不发送图片、路径或图片数据。共享 CSS 和 SDK 的薄荷样式从塔防 0.1.9 起提供，0.8.2 继续支持；升级保留用户固定选择，已有用户需在「功能管理」手动选择已安装的新版本。

打开功能后，模块外层固定占据主内容区的剩余空间，宿主不再叠加重复的卡片边框与模块标题；内容滚动由 iframe 内的页面负责。宿主不读取或测量 iframe 的业务 DOM 来调整高度，模块保留自己的布局与滚动责任。

## 宠物陪伴与旧设置兼容

已有两只用户视频角色的默认素材保持不变：阿宝（`traveler-01`）保留完整站立—跑步—站立节奏，使用 768×768、97 帧、24 FPS、约 4.041667 秒的 APNG；阿沐（`amu-01`）的骑行循环使用全身视频 (3) 的连续源帧 51–130（含两端），80 帧、24 FPS、约 3.333333 秒。两只各自的 `loop.png` 与 `still.png` 不因追加动作而替换。

处理仅使用原视频完整帧的透明化、固定画布和格式转换，没有 AI 重画、插帧或变速。阿沐短循环是当前可逆的默认装配选择，不宣称数学上无缝；完整 145 帧、24 FPS、约 6.041667 秒的动作另存于本机 `dist/companion-assets/amu-01/full-cycle.png`，不作为运行时重复资源打包。近景视频 (2) 已弃用，仅全身视频 (3) 用于阿沐。来源、摘要与本地制作范围见 [阿宝来源](apps/features/companion/assets/traveler-01/SOURCES.md)、[阿沐来源](apps/features/companion/assets/amu-01/SOURCES.md) 及 [宠物资源说明](docs/companion-assets.md)。原视频不进入 Git、源码包或安装器。既有两只默认素材曾按用户 2026-10-22 请求随 0.12.0 交付；用户随后于 2026-10-23 要求提交当前代码及相关资源，新思考动作的处理后素材纳入审阅后的提交；同日用户进一步授权执行本次发布流程，远端推送与发布结果分别记录。原素材与新动作均不附加未提供的第三方许可证，历史发布状态见下载索引。

设置中的「宠物陪伴」只保留角色卡与「不启用宠物」；桌面点击后立即保存，再次点击当前角色可召回已收起的宠物。显示位置、动作、大小、置顶和播放由宠物自己的右键菜单调整；托盘「显示宠物」也可召回。浏览器中的角色选择和菜单只预览应用内模式，不写入桌面设置，也不提供桌面悬浮或系统托盘。

右键菜单提供暂停/继续动画、桌面悬浮/应用内陪伴、实际已有素材的动作模式、96/128/160/200/256 px 大小、桌面模式的始终置顶、暂时收起和打开 OpenDesk。0.15.0 使用统一 Vue 菜单面板：角色头像、分组、分段位置选择、动作与大小按钮、置顶开关及底部操作。桌面通过 App 拥有的独立透明 `companion-menu` 窗口承载面板，基准 320×480 并受屏幕可用区域约束，避免被小型宠物窗口裁切；应用内使用同一面板，浏览器仅作本地预览。失焦或 Escape 关闭菜单，代次校验拒绝已经失效菜单的迟到写入。0.14.0 使用系统原生菜单的结果保留为历史，不代表当前面板已经验收。

选择动作模式后持续循环，阿沐思考模式在视频 `ended` 后重新播放；主点击触发的思考仍只播放一次，结束后返回当前所选模式。`animated` 是唯一持久化播放开关，旧设置缺少 `actionMode` 时按 `idle` 读取。菜单权限限定为读取宠物上下文、受控单字段偏好更新、隐藏宠物、打开主界面和关闭自身，不授予任意 shell、业务调用或完整设置写入。

通用组件通过组合层注入角色，仍支持应用内拖动和同进程透明桌面窗口；应用内位置只在当前会话保留，退出 App 时桌面窗口一并退出。暂时收起不关闭 App 或停用角色，主窗口收起到托盘后桌面宠物可继续显示。空目录或模型不存在时保留显示守卫，不自动选择替代角色。透明窗口仍有矩形命中区域，不承诺按角色像素穿透。

当前已移除「背景与氛围」整项，包括图片导入、背景显示、粒子及氛围调节。旧配置中的 `personalization.background` / `effects` 仅兼容忽略；以前导入的图片保留在原目录，当前版本不读取也不删除。`--import-wallpaper` 明确拒绝，不再提供导入入口。0.8.0 的旧能力与验证仍保留为历史记录。

阿沐的“托腮思考”（`thinking`）来自用户视频 (4)。0.13.0 保留整幅房间的实现已按反馈修正：0.13.1 保留阿沐、显示器、键盘及桌椅等真实前景，将墙面、天花板和物体间房间空隙透明化；保持原半身构图，不补身体。

正式运行时资源为 14,061,447 字节的 alpha VP9 `thinking.webm`，768×768、145 帧、24 FPS；源时长为 145/24 秒，WebM 容器显示 6.042 秒。透明 APNG 母版单独导出，不进入运行时。前景颜色来自原视频采样，首帧生成 alpha 仅作轮廓辅助参考，不重画角色；VP9/YUV420 颜色转换可能产生色差，不能称为 RGBA 逐像素无损。

视频在加载就绪后播放，以 `ended` 判断结束，重触发从头播放；暂停、隐藏、减少动效及模型切换清理当前播放。0.15.0 调整风格与菜单呈现，已有素材及摘要不变。0.13.1 的正式 WebM 曾在可见 Chrome 黑/白/棋盘底检查透明、完整播放和重播，并完成 Tauri/NSIS 构建、原生核心 smoke 与保留既有偏好的正常启动；这些历史结果不代替 0.15.0 的完整验收，也不证明当前菜单窗口或新动作的原生视觉已经检查。本轮 0.15.0 正式构建完成 302 项自动化测试（255 项工程测试 + 47 项 Rust 测试）、核心/模块/两只宠物的原生 smoke，并已发布且下载核对全部 10 个远端附件。CI、原生人工交互及安装/卸载等证据边界分别见 [验证记录](docs/verification.md)，不以发布成功代替未执行的检查。

## 桌面基础

- 七种界面风格、独立配色设置、真实功能入口与宿主运行诊断。
- 既有静态风格保留并新增薄荷奶油；两只宠物素材不变，独立菜单面板已随 0.15.0 发布。
- 显式模块声明、校验、导航和页面懒加载；框架不导入具体业务模块。
- Rust 宿主统一设置读写；格式带版本，暂存后替换，损坏/未来格式不被默认设置覆盖。
- Python worker 按需启动；stdio v1 握手、有限方法路由、超时、错误与退出。
- Windows Job Object 仅管理宿主拥有的 worker。正常业务校验失败不会销毁健康进程。
- 单实例唤起、托盘、关闭时隐藏到托盘（可配置）、Ctrl+Shift+Space 唤起（冲突会写日志）。
- 开发示例默认关闭，并从生产前端构建排除。
- 本地日志只记录诊断字段；没有遥测、后台 AI 请求或自动联网安装插件。

主界面空白区域不显示浏览器默认右键菜单；输入框、可编辑区域和选中文字仍保留标准菜单。宠物显示自己的菜单，模块 iframe 的内部菜单保持各模块原有行为；此规则不影响系统或其他应用。

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
$env:OPENDESK_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
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
| `npm run build:modules` | 仅按分发清单构建 App 随附页签与默认清单，当前为塔防助手 |
| `npm run build:module-fixtures` | 构建本地测试台 baseline 及四个变体，均不公开分发 |
| `npm run build:module -- tower-assistant` | 按当前 manifest 独立构建塔防页签至 `dist/modules/tower-assistant/0.8.2/` |
| `npm run release:stage -- app` | 校验干净提交与构建清单，本地整理 App 发布附件 |
| `npm run release:stage -- module tower-assistant` | 本地整理塔防页签的独立版本发布附件 |
| `npm run check` | 版本一致性、类型、架构边界、前端与 Python 子进程测试 |
| `npm run version:check` | 检查版本历史、各构建版本与生成的 CHANGELOG 一致 |
| `npm run version:prepare -- --file .local/next-release.json` | 校验并加入一次更新草稿，同步派生文件 |
| `npm run version:sync` | 从版本清单同步构建版本并生成 CHANGELOG |
| `npm run version:tags` | 打印当前应用及本次变更功能的 Git 标签，不创建或推送标签 |
| `npm run desktop:check` | Rust 静态编译检查 |
| `npm run desktop:test` | Rust 测试 |
| `npm run build` | 前端、worker、Tauri、NSIS和便携目录，并记录 `dist/app-build.json` 构建清单 |

发布包的真实桌面链路检查可运行 `.\scripts\smoke.ps1`。它使用独立的框架测试数据目录，从其他工作目录启动 EXE，验证 WebView、IPC、设置读写与打包 worker 启停，并在完成后退出。

模块导入与版本切换检查使用 `.\scripts\module-smoke.ps1`，默认读取 `dist/OpenDesk/OpenDesk.exe` 与 `dist/module-fixtures`；可显式指定 `-Executable` / `-Fixtures`。先运行 `npm run build:module-fixtures` 准备五个本地测试包。smoke 在独立数据根按 fixture 索引导入测试台 baseline，再进行版本切换，不要求正式 App 随附测试台。报告、退出码及无遗留测试 worker 分别核对；超时只停止本次测试宿主。源码入口存在不代表已经验证通过。

正常使用时从资源管理器双击便携目录中的 `OpenDesk.exe`。`--enable-companion` 可保存启用桌面宠物模式，使用前需从托盘退出已有实例；该参数只使用已经装配的角色，不导入或生成资源。单实例回调只唤起旧窗口，不处理第二次启动参数，即使显式设置 `OPENDESK_DATA_DIR`，普通启动也不会绕过单实例。已移除的 `--import-wallpaper` 参数会被拒绝。

开发排障时，Codex 等 MSIX 打包宿主派生的进程可能受到文件系统重定向影响，导致模块实际路径与受校验路径不同。普通使用按资源管理器双击启动；若只在开发宿主内出现路径校验失败，应核对启动环境和规范化路径，保留模块 canonical 路径校验，不以放宽校验作为启动修复。

三种检查模式 `--smoke-test`、`--module-smoke`、`--companion-smoke` 必须显式提供绝对路径的 `OPENDESK_DATA_DIR`，才会跳过单实例并允许与用户实例并存。调用方应选择不同于正常用户数据的独立测试目录；仅要求绝对路径不等于自动识别和隔离所有数据。变量缺失、空值、相对路径，或重复/混用 smoke 模式时，启动阶段以退出码 2 拒绝，不回退到默认用户目录。现有核心和模块 smoke 脚本会自行创建并传入绝对测试目录。

`--companion-smoke` 使用独立测试根，检查设置往返、所选角色图片就绪与窗口显示/隐藏/重新显示，写入 `companion-smoke-result.json` 后退出。无资源或所选模型不存在时不能以其他形象冒充验证通过。smoke 只约束框架数据根与实例启动，WebView2 缓存仍可能共用默认应用缓存目录，不代表完整浏览器隔离。本次实际结果见验证记录，不用历史运行代替当前证据。

首次运行 Rust 检查前需要 `npm run build:worker` 和 `npm run build:modules`，因为桌面构建描述引用 worker 与自带包资源。首次取得仓库应使用已有 Cargo.lock；不要在正常检查时主动更新锁文件。

生产包为 `dist/OpenDesk/OpenDesk.exe`，必须连同同目录的 `opendesk-worker.exe`、`_internal` 和 `bundled-modules` 使用。便携压缩包应完整解压后启动，不能只复制 EXE。NSIS 安装器位于 `apps/framework/desktop/src-tauri/target/release/bundle/nsis`，当前源码构建目标文件名为 `OpenDesk_0.18.0_x64-setup.exe`。本次已从干净提交构建 App 0.18.0 安装器／便携包及塔防 0.8.2 独立页签，实际生成与检查结果见验证记录。生成安装器不代表已安装、已签名或已发布；上传 Git 源码和标签也不会自动创建 GitHub Release 或上传安装包。

便携目录重建时若存在不属于本次产物清单的文件或目录，构建会在复制前拒绝，避免旧依赖或个人文件混入新包。请将旧输出移走后重新生成，或先检查并手工清理；脚本不自动删除未知内容。

## App 与独立页签交付

2026-10-23 已发布 [App `v0.15.0`](https://github.com/utxnimda/OpenDesk/releases/tag/v0.15.0) 与[塔防 `0.1.9`](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.9)，构建来源均为干净提交 `aa7bdd7346fc1544bebff939b79ed40886c215a2`。App 的 6 个附件与页签的 4 个附件均已下载核对 SHA-256；App 标为最新，独立页签不覆盖 App 的最新入口。本机导入的部署工作台没有纳入源码或任何发布附件。此前公开 0.12.0 / 0.1.7 及已使用过的本地 dirty 开发包 0.1.8 保留。完整操作、附件清单、失败处理和远端校验见[发布手册](docs/releasing.md)，直接下载见[索引](docs/downloads.md)。后续发布结果文档提交不改变包的来源或移动标签。

后续发布仍先审阅并提交准备内容，再从干净提交构建并暂存：

```powershell
npm run build
npm run build:module -- tower-assistant
npm run release:stage -- app
npm run release:stage -- module tower-assistant
```

2026-10-23 的历史附件从 `dist/releases/app/0.15.0/` 与 `dist/releases/modules/tower-assistant/0.1.9/` 审核后发布；本次 2026-10-26 使用 `dist/releases/app/0.18.0/` 与 `dist/releases/modules/tower-assistant/0.8.2/`。`release:stage` 本身只在本地整理文件，不提交、打标签或上传；它要求当前工作树干净，App 的 `dist/app-build.json` 与当前提交、版本和实际文件摘要一致，模块来源为本仓库已提交的干净开发历史，且在公开白名单内。已有发布版本目录会拒绝覆盖。

上传时只使用各暂存目录中审核通过的明确文件清单。不得递归上传整个 `dist`、本机 imports/installed、测试 fixture、缓存或其他分支产物。新增/更新的正式页签按自己的 ID/版本独立构建、暂存与发布下载；本机安装或导入行为不产生发布授权，也不自动加入分发清单。

## 配置与运行数据

数据根优先级为绝对路径环境变量 `OPENDESK_DATA_DIR`、EXE 旁 `opendesk.portable.json` 的相对 `dataDirectory`、默认 Tauri `app_data_dir`。标记格式为 `{"schemaVersion":1,"dataDirectory":"../../runtime"}`；无效配置拒绝启动，诊断页显示实际路径。应用不会自动扫描、迁移或清理旧数据；本次迁移是用户明确授权的一次性操作。

该覆盖值控制本框架当前使用的设置、导入模块、诊断日志和 smoke 结果的数据根；调用方仍需选择独立测试目录。旧壁纸文件不再读取或删除。WebView2 缓存仍使用 Tauri 默认 `app_local_data_dir`，不宣称完全隔离所有运行缓存。

- `config/framework/settings.json`：真实设置，唯一写入方为 Rust。
- 旧 `wallpapers/`：历史图片保留在原位，当前应用不读取、不删除、不打包。
- `logs/framework.jsonl`：当前诊断日志；超过约 2 MiB 时保留一份 previous 文件。
- `smoke-result.json`：仅使用 `--smoke-test` 时生成的桌面链路结果。

源码中的 `config/framework/defaults.json` 是共享默认模板；前端只用它建立初始草稿。读取真实设置失败时禁止保存默认草稿。

请勿将真实游戏存档、`package.nw`、游戏图集、导出的私人布局图、日志、凭据或本机配置加入 Git。`.local/` 用于本机验证数据和更新草稿；`.venv/`、构建产物与运行数据也不应提交。测试使用合成数据，读取用户游戏文件的验收结果只保存在本机。

## 目录

```text
apps/framework/frontend/           Vue 外壳、公开契约与组合入口
apps/framework/desktop/src-tauri/  Rust 宿主与 capabilities
apps/framework/worker/             Python 协议及运行时
apps/composition/frontend/        宿主 Vite 入口、主界面/宠物窗口选择与资源装配
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

作品管理器按本次授权迁入，旧 Nimda Git 历史不合并，私人数据不随源码发布。具体已执行检查和未验证项目见 [验证记录](docs/verification.md)，不要用一次编译成功代替桌面或安装验收。

## AI 工作台本地开发

已从最新 0.18.0 正式基线开始实现，当前开发宿主为 0.19.1，AI 独立页签为 0.4.2，随附塔防为 0.9.1，已完成本地实现与验证，尚未公开发布。支持 API 聊天、本地 Codex 与 Cursor Agent、完整模型目录与回合间切换、Codex 原有工作区目录，以及经授权读取当前插件实例的只读工具。AI 聊天布局参考 Codex，颜色和材质跟随宿主。详细使用、范围及验证边界见 [AI 工作台](docs/ai-workbench.md)。App 默认仍只随附塔防；AI 独立导入，不改变其他个人页签选择。
