> 2026-10-20 起的 OpenDesk 记录为当前工程实测；2026-10-19 及之前的记录属于源码迁移前历史。旧运行编号、提交和工作区路径仅用于保留原始验证上下文。

# 验证记录

## 2026-10-23：OpenDesk 0.15.0 与塔防 0.1.9 正式发布

用户在现有改动已提交后，进一步明确要求先完善发布文档、再执行发布，并再次排除本机导入的部署工作台。本次完成源码推送、App/模块标签与独立 Release、远端附件下载核验；[发布手册](releasing.md)记录后续可复用步骤，[下载索引](downloads.md)保留新旧版本入口。以下记录不改写此前各开发阶段的未发布事实。

实现提交为 `aab87044c4c4713a7bc486fdf27bf6511260d9e8`；发布准备与实际构建来源为 `aa7bdd7346fc1544bebff939b79ed40886c215a2`，`dirty:false`。本轮维持尚未发布的宿主 0.15.0，将已使用过的 dirty 塔防 0.1.8 升为正式 0.1.9，以免同 ID/版本产生另一摘要。旧开发包、旧公开 0.1.7、缓存和用户选择均保留；没有修改旧包来源字段来伪装干净产物。

| 验证范围 | 实际结果 |
|---|---|
| 源码与授权 | 实际 Git 清单 195 文件，审查未发现私有模块、真实部署配置、运行数据、原视频、安装器或 `.ndpkg` 入库；处理后思考资源授权单独补充，不虚构第三方许可证 |
| 源码检查 | `npm run check` 通过：46 项版本/分发、140 项前端、69 项 Python，共 255 项；类型与 80 个源码文件架构边界检查通过 |
| Rust | `npm run desktop:test` 47/47 通过；与上述合计 302 项 |
| 构建 | 从 aa7bdd7 干净提交完成正式前端、worker、Tauri、NSIS、60 文件便携包及独立塔防 0.1.9；App 收据与两个包来源一致 |
| 公开范围 | `bundledModules` 和 `publicModules` 仅 `tower-assistant`；App 6 附件、独立页签 4 附件，无部署工作台、外部导入包、测试台二进制、fixture、私人配置/数据/图片、原始视频或完整动画母版 |
| 实际包内容 | 塔防 ZIP 70 entries；通用 worker 的 113 个 PYZ 模块无具体业务，塔防 worker 的业务仅 tower_assistant 下 6 项；NSIS 的 59 个资源加主程序全部与便携收据摘要一致，便携 ZIP 60 文件逐项匹配 |
| 核心原生 smoke | 新 EXE 的 WebView、IPC、设置往返及冻结 worker 启停/重启通过，worker PID 51616/31584；退出无残留 |
| 模块原生 smoke | 实际 iframe/worker 往返、既有测试 fixture 1.0.6→1.1.0→1.0.6、UI 失败恢复、禁用/过期会话拒绝、不兼容原件保留通过；初始随附仅塔防，fixture 没有公开 |
| 双宠物原生 smoke | 阿宝/阿沐各自隔离数据根、mint/light 下均通过真实面板 ready、窗口复用、菜单代次拒绝、关闭、屏幕边界、显示位置切换、大小与设置保留检查 |
| 正常桌面启动 | Explorer 启动正式 `dist/OpenDesk/OpenDesk.exe`，PID 38556；frontend.ready、companion.ready 到达，无 error，Responding=true；设置完整摘要、全部页签选择及本地部署工作台登记保持一致，塔防 0.1.9 登记成功 |
| Git 与 Release | main 及两个注解标签解析后均指向实际构建提交 aa7bdd7；App `v0.15.0` 为 Latest，页签 `modules/tower-assistant/v0.1.9` 为独立非 Latest；历史版本未覆盖 |
| 远端附件复核 | App 6 个与模块 4 个附件均从 GitHub 下载，实际名称/数量符合白名单；每个 SHA-256 与暂存文件一致，下载的 SHA256SUMS 也逐项通过；两处页签包字节相同 |
| GitHub Actions | 实际构建提交的三次运行全部 success：[main 39916819584](https://github.com/utxnimda/OpenDesk/actions/runs/39916819584)、[App 标签 39916819896](https://github.com/utxnimda/OpenDesk/actions/runs/39916819896)、[模块标签 39916819656](https://github.com/utxnimda/OpenDesk/actions/runs/39916819656)；检查与前端构建通过，两个标签版本核验通过 |

主要交付摘要：

| 文件 | SHA-256 |
|---|---|
| `OpenDesk_0.15.0_x64-setup.exe` | `9bed99568de903c71c17b11e94345a0d090895958f6bc88c23f0a4a54d7bf964` |
| `OpenDesk_0.15.0_windows-x64_portable.zip` | `e3a24b8db160dece17e9e68e765f1201de114c44f972f0a77e805483e2415e27` |
| `tower-assistant-0.1.9.ndpkg` | `bacc16c3aa03054932ccb76cad08039a1091b972ab449c8ea620a89c66df4b91` |
| 便携目录 `OpenDesk.exe` | `b29f6551f8573ade9c03ce1f2f6538a010bf3a868142b52f5cd033cc224f2348` |

中间失败与修正：受限环境先阻止测试调用 Git（EPERM），改用已授权本机权限后完整检查通过；首次核心 smoke 自身通过，但包装命令误读同进程残留 LASTEXITCODE，后续脚本改由独立 PowerShell 进程返回真实退出码。宠物辅助脚本在 Windows PowerShell 5.1 使用 `Set-Content -Encoding utf8` 生成 BOM，宿主按规则拒绝该测试设置；仅将忽略目录内的辅助脚本改为无 BOM UTF-8 后，双角色检查通过。第一次正常启动取样早于模块/宠物就绪，稍后重新只读检查通过；没有为通过测试修改产品代码、放宽设置解析或跳过失败。

旧便携目录、旧随附目录、旧安装器及旧收据完整保留于 `.local/release-0.15/previous-build/`。构建前仅停止核实路径/PID/启动时间的当前 OpenDesk 实例，没有按进程名批量结束其他应用。用户数据根不作为构建输入，启动核验只记录设置与登记的摘要，不把私人内容复制到公开附件。

本机证据：`.local/release-0.15-check.log`、`.local/release-0.15-rust-tests.log`、`.local/release-0.15-build.log`、`.local/release-0.15/{scope-audit.json,stage-app.log,stage-module.log,module-smoke.log,companion-smoke-retry.log,startup-verification.json,download-verification-app.json,download-verification-module.json}`；核心结果 `.local/smoke/20261023-151505-882/`，模块结果 `.local/module-smoke/20261023-151538-201-6e5383e5/`，双宠物 `.local/release-0.15/companion-smoke/20261023-151654-485/`。审计原文和测试产物保持本地，不加入公开附件。

本轮未重做素材制作或浏览器视觉验收，媒体与界面证据仍引用本日此前相应验证；没有原生菜单逐项人工点击/视觉验收、干净机器安装/卸载、多显示器或长期运行验收。安装器未签名。发布结果文档另作后续提交，两个 Release 标签保持绑定实际构建来源，不移动到文档提交。


## 2026-10-23：现有改动提交范围与上传流程审阅

用户要求先提交现有改动及相关资源到 Git，并明确排除本地导入的部署工作台，同时确认代码、资源、App 与正式页签的打包发布流程。本轮执行范围为本地提交和分发审计；没有执行 push、标签推送或 Release 上传。此前各开发阶段“未提交/未发布”的记录保留原始时间含义。

审阅初始候选为173个跟踪文件、21个未忽略未跟踪文件。没有部署工作台/DSWorldDesk私有模块源码、真实环境配置、运行数据、EXE、ZIP、`.ndpkg`或原始MP4；旧小鸭素材摘要及已撤路径检查为0命中。新增入库资源为阿沐透明思考WebM、静态PNG、灰度alpha种子、轮廓及来源记录；完整APNG母版仍在忽略的dist。两处本机工程绝对路径属于既有通用工程验证历史，按规范保留，未包含私有部署配置。

分发白名单仍只有tower-assistant。已核对App随附、独立输出及固定便携目录中的三份塔防0.1.8包，均70条目、摘要`800b268e8b78e9962a7d290fc97fcbf75e0911cc631671d33c5ad498ed626be2`，无部署工作台、测试包、本机imports/installed或私有数据。通用worker归档的122入口不含具体业务。Git忽略目录和实际打包入口分别检查，未把ignore或“没有密码”当作完整公开依据。

当前开发包仍为dirty来源，App构建收据是旧0.12.0，不能直接正式上传。只读验证进一步确认模块缓存会保留旧包的dirty来源，不能仅靠提交后重复build粉饰为clean；正式交付宜保留已使用的0.1.8并提升正式页签版本，再从干净提交构建、检查包内清单、按版本暂存和核对SHA。CI当前只检查，不自动构建安装器或创建Release。详细流程与资源归属已同步[版本维护](versioning.md)。

本机审计记录为`.local/public-source-audit.json`、`.local/replaced-companion-audit.json`；最终提交及文件范围以Git记录为准，不将开发包或私人审计输出加入源码历史。

## 2026-10-23：薄荷奶油风格与精致宠物浮层（本地 0.15.0）

用户提供浅粉/薄荷、圆角白卡与柔和阴影的参考图，要求增加对应风格并美化宠物右键菜单。新增第七种 `mint`“薄荷奶油”，推荐浅色但不强制更换配色；界面使用静态渐变、宽松内容列、26px 圆卡和薄荷选中态，深色/石墨夜保留可读语义色。未恢复背景图片或氛围设置，未采用参考图中的旧角色素材或语音功能。

菜单改为共享 Vue 面板：头像/状态、播放按钮、显示位置分段按钮、真实动作选项、五档尺寸、桌面置顶及收起/打开应用。应用内用定位浮层，桌面用同进程的 `companion-menu` 独立透明窗口，基准320×480逻辑像素并限制在当前显示器工作区。新代次控制上下文、ready、局部写入与关闭；菜单权限不包含角色选择、全量设置、业务 worker 或任意执行能力。

| 检查 | 实际结果 |
|---|---|
| 类型/版本/边界/自动化 | 最终 `npm run check` 通过：46 项版本/分发、140 项前端、69 项 Python，共255项；79个源文件边界检查通过 |
| Rust | `npm run desktop:test` 47/47、fmt通过，包含新代次拒绝、角色/模式/旧默认模型、负坐标/分数DPI/小工作区与权限边界；合计302项测试 |
| 样式和组件 | CSS解析、Vue scoped CSS编译与菜单SSR行为检查通过；薄荷浅色主要文字/说明文字对比约7.85/4.87，菜单说明文字约4.85 |
| 实际浏览器画面 | 工具恢复后在内置浏览器完成实际UI验收：1280×720浅色、860×620最小窗口、石墨夜配色。没有body横向溢出，页面由main-content单独滚动；菜单320×373.1875完整限制在视口内 |
| 菜单交互 | 右击打开、思考选项、暂停/继续、256px选择、方向键256→200、Escape关闭和焦点回到宠物通过；未提供的桌面能力在浏览器正确禁用，无console warn/error |
| 模块产物 | 新塔防0.1.8包含mint SDK与共享样式，70文件、冻结worker检查通过；独立包与App随附包SHA-256均为`800b268e8b78e9962a7d290fc97fcbf75e0911cc631671d33c5ad498ed626be2`。旧公开0.1.7原包保留，测试台/fixture未进入App |
| 最终构建 | Tauri release、NSIS及60文件便携包通过；当前源码基线仍是de5c9c0，dirty。独立候选`dist/OpenDesk-0.15.0-20261023-130550/`，固定启动目录同步更新 |
| 双角色真实菜单 smoke | 阿宝与阿沐在独立数据根、mint/light设置下均通过实际Vue面板ready、同窗口复用、新代次与过期操作拒绝、关闭、模式切换/隐藏同步关闭、屏幕范围及原有宠物设置检查。150%缩放下菜单实际480×720物理像素 |
| 原生核心 smoke | WebView、IPC、设置往返及冻结worker启停/重启通过，worker PID29804/20292，退出无残留 |
| 原生模块 smoke | 实际iframe/worker往返、1.0.6→1.1.0→1.0.6、不兼容原件保留、UI失败恢复、禁用与过期会话拒绝通过；测试使用既有本地fixture，未声称重新构建了当前测试台1.0.7 |
| 正常启动 | 固定`dist/OpenDesk/OpenDesk.exe`为0.15.0，Explorer启动PID20948；frontend.ready、阿沐companion.ready、Responding=true、0error。原web/ocean、阿沐256px桌面模式和全部模块选择保留；塔防0.1.8已登记 |

首次候选的快速模式/禁用/恢复测试在第4次菜单打开等待ready超时，日志只证明前3代ready，未记录关闭原因。源码确认主界面设置镜像、宠物设置刷新和可见性通知会重复发送无代次hide；修正为Rust偏好命令独占后续原生窗口效果，前端通知只刷新显示，主界面仅初始读取/显式重试协调窗口，并忽略过期设置读取。没有给测试加盲等或跳过快速切换；最终相同双角色smoke通过。首次候选保留在`.local/previous-builds/mint-before-window-owner-fix/`，失败证据仍留存。

最终EXE SHA-256：`5d8d260e636422c0fa61b21b03491a5bf12759b91569d3fe53a7f6a71399e073`；安装器SHA-256：`f543b28dee537e688940f7b6934c24a51e708bac1242457b5a42f24db69eb3be`。旧固定目录0.14.0完整备份至`.local/mint-launch-20261023-130550/previous-stable-portable/`。切换时未发现仍运行的旧主实例，没有按进程名批量停止任何程序。

真实用户原先固定塔防0.1.4，保持该选择；需要新模块内部样式时由用户在功能管理选择0.1.8。本次没有Git提交、推送、标签或公开发布；新独立包只在本机。未执行原生菜单逐项人工点击/截图、干净机器安装卸载或长期运行，浏览器交互与原生自动化证据分别记录。

证据：`.local/mint-check-final.log`、`.local/mint-build-final.log`、`.local/mint-modules-build.log`、`.local/mint-preview/{browser-results.json,mint-light-menu.png,mint-860-menu.png,mint-graphite-menu.png}`、`.local/mint-native/20261023-130550-807/`、`.local/smoke/20261023-130637-627/`、`.local/module-smoke/20261023-130636-269-3524f2c7/`、`.local/mint-launch-20261023-130550/{package.json,launch.json,verification.json}`。失败目录为`.local/mint-native/20261023-130113-578/`。临时浏览器标签、视口覆盖和本任务预览服务均已清理。

## 2026-10-23：独立宠物右键菜单与即时角色选择（本地 0.14.0）

用户要求应用内只选宠物，其余控制放在宠物右键菜单，并确认同时保留桌面/应用内显示位置与动作模式。应用内改为角色卡和“不启用宠物”，立即保存，点击当前角色可以召回。宠物工具条保留播放/暂停与更多；右键、Shift+F10、菜单键或更多打开菜单。桌面使用 Tauri 原生弹出菜单，避免小透明窗口裁切；浏览器使用明确的本地预览菜单，不冒充原生桌面能力。

菜单提供显示位置、实际存在的循环动作、96/128/160/200/256px 大小、桌面置顶、暂停/继续、暂时收起和打开 OpenDesk。新增持久化 `actionMode`，旧数据默认 `idle`；选择思考模式后按视频 `ended` 开始下一轮，主点击的临时动作结束后回到当前所选模式。`animated` 是唯一手动播放开关，取消原局部暂停状态；暂停后继续按钮可用，系统减少动态效果仍优先。

原生增加只选择角色、只更新单个宠物字段、只保存外观、打开主界面的受限命令，校验调用窗口、精确参数键、枚举和范围。宠物窗口不取得全量设置、角色选择或外观保存权限。局部修改在同一锁内读取、更新、校验和原子保存；设置页三方合并保留尚未保存的外观草稿。托盘新增“显示宠物”，根据已保存位置召回；没有启用角色时打开主界面。主界面空白处取消 WebView 默认菜单，输入框与选中文字保留标准文本操作；未改模块 iframe 自己的菜单。

| 验证 | 本次实际结果 |
|---|---|
| 版本/类型/边界/自动化 | `npm run check` 通过：46 项版本/分发、130 项前端、69 项 Python，共 245 项；77 个源文件架构检查通过。最后的失效菜单回调守卫补充后，`vue-tsc` 与生产构建通过 |
| Rust | 43/43 lib tests、fmt 检查通过；覆盖权限矩阵、未知/缺失参数、非法局部更新不改原文件、旧字段缺省、角色切换和锁内并发保存。与上述合计 288 项测试 |
| 浏览器正式 UI | 实际选择阿沐，右击、更多、Shift+F10打开菜单；思考持续循环从代次 2 到 16 仍为 thinking，暂停切静态且可继续；调整256px后avatar/video均256×256；切默认模式返回原骑行 |
| 菜单与召回边界 | 收起后点同角色已召回；阿宝动作菜单只提供默认循环；不启用移除宠物/召回/菜单；Escape关闭菜单；菜单与角色处于视口内，无warn/error。主界面空白实际右击无菜单。文本编辑例外做源码检查，未作实际剪贴板验收 |
| 打包 | Tauri release与NSIS成功，独立候选和固定便携目录均60文件。角色卡CSS放在组件内，所有参与模块指纹的共享样式未改变；塔防0.1.7继续复用原SHA-256，没有同版换包 |
| 双角色原生 smoke | `traveler-01`与`amu-01`分别在独立数据根启动，真实图像ready、设置往返、局部更新、外观不覆盖宠物、同角色偏好保留、256px原生窗口尺寸、desktop/in-app切换、禁用后更新不启用、隐藏和重开均通过 |
| 原生核心 smoke | WebView挂载、IPC、设置往返及冻结worker启动/重启/停止通过，worker PID52860/47960，退出无残留 |
| 正常启动 | 固定`dist/OpenDesk/OpenDesk.exe`为0.14.0，经Explorer启动PID23588；frontend.ready和阿沐companion.ready到达，Responding=true、0error。原Codex/浅色、桌面宠物选项与全部模块选择保留 |

EXE SHA-256为`eb89cecda4e1c9e414939772e4a2049ac849bf0d2992fb6a6363e2cda5b0d4af`；安装器为`9ec0ea5c0854e1c25c548db9686f4956d2a4df1113e43cb6b04a6d1a819be196`。源码基线`de5c9c0e040f5a882b848d5145af435827dd2a29`、工作树dirty。独立候选`dist/OpenDesk-0.14.0-20261023-121012/`保留；固定目录的原0.13.1完整备份到`.local/pet-menu-launch-20261023-121012/previous-stable-portable/`，只停止已核对路径与启动时间的旧PID55588。

本轮未做原生系统弹出菜单的人工点选/截图验收；原生smoke直接检验持久化与窗口行为，浏览器验收检验菜单选择及组件交互，两者不混同。未做安装器安装/卸载、多屏DPI或长期运行测试。宠物媒体摘要均未变；本次没有Git提交、推送、标签或公开发布。

证据：`.local/pet-menu-check.log`、`.local/companion-menu-native-validation.json`、`.local/pet-menu-build.log`、`.local/pet-menu-preview/{remaining-boundaries.json,final-role-selection-and-menu.jpg,blank-context-suppressed.jpg}`、`.local/pet-menu-native/20261023-121017-843/`、`.local/smoke/20261023-121037-515/`、`.local/pet-menu-launch-20261023-121012/{package.json,launch.json,verification.json}`。本次创建的浏览器预览标签与精确身份匹配的Vite服务已清理；用户新版实例继续运行。

## 2026-10-23：阿沐托腮思考透明修正（本地 0.13.1）

用户指出 0.13.0 的思考动作显示了整幅房间，需要像原骑行一样透出桌面。修正为保留阿沐、显示器、键盘及桌椅前景，去除墙面、天花板和物体间背景空隙。接受半身构图，不补画身体；最终颜色取自原视频，生成首帧仅提供 alpha 轮廓参考，未将生成 RGB 用作视频帧。原阿宝与阿沐骑行的四份运行资源摘要保持不变。

运行时替换为 768×768 VP9 alpha `thinking.webm`，保留全部 145 帧、24 FPS，容器时长 6.042 秒；源时长为精确 145/24 秒。所有帧使用共同源边界 `[0,167,720,1280]`，等比缩放到 453×700，位于画布 `[157,34]`、底线 734，不逐帧居中。透明 APNG 母版单次播放，独立保留；默认骑行和单次 `ended` 返回策略不变。

| 检查 | 本次实际结果 |
|---|---|
| 逐帧前景 | 145 帧源 RGB 在归一化前保持原值；固定背景/前景样点、首中尾黑白/棋盘对照检查通过，纠正桌下蓝墙残留和头顶外连墙边 |
| 视频解码 | 强制 `libvpx-vp9` 解码全部 145 帧：所有源 alpha 0/255 分别保持完全透明/完全不透明，中间 alpha 最大差 1 级；不宣称 RGBA 逐字节无损。帧序/帧数保留，PTS 与 24 FPS 理论值误差不超过 1 ms；RGB 转 YUV420 存在色差 |
| 透明 APNG | 145 帧、plays=1，Pillow 解码与处理帧逐像素相同，作为独立母版保留 |
| 实际透明播放 | CUA 可见 Chrome 加载正式 `CompanionPet` 和同摘要 WebM，黑/白/棋盘三底实际播放。外围与原墙面黑底 0、白底 255；物间/桌下空隙黑底不高于 2、白底不低于 254；256px 下无整幅房间矩形或明显块状光晕 |
| 完成和重播 | 正式视频 duration=6.042，5.902 秒仍在播放，之后回到原骑行；重播 1.968→0.165 秒，新建视频节点，旧节点已断开、暂停并清空 src；无浏览器 warn/error |
| 版本、类型、边界及自动化 | `npm run check` 通过：46 项版本/分发、119 项前端、69 项 Python，共 234 项；73 个源文件架构检查通过。Rust 逻辑未改，本轮未重复其单元测试 |
| 本地构建与内容 | Tauri release、NSIS 安装器和 60 文件便携目录完成。Vite 的 `thinking-*.webm` 与生产文件 SHA-256 相同；唯一随附页签仍是塔防 0.1.7 原包，未重新发布或改变摘要 |
| 原生核心 smoke | 独立数据根中 WebView、IPC、设置往返、冻结 worker 启动/重启/停止通过，worker PID 56336 / 55568，无残留 |
| 正常桌面启动 | 固定 `dist/OpenDesk/OpenDesk.exe` 已更新为 0.13.1，经 Explorer 启动 PID 55588；frontend/companion.ready、响应正常、0 error。原 Codex/浅色、宠物选项与全部模块选择保持不变，桌面宠物仍启用 |

运行视频 14,061,447 字节，SHA-256 `9a0d6c9cf31a40458ed4d15d62557ed4da841042df705da1dfada3e8b3812449`；透明母版 71,216,736 字节，SHA-256 `69cc304b94b5aedbecb31c9dfe8e57d1fd2963a93b2b4ffe43c2214d1a8fe6eb`；透明静态图 SHA-256 `7568a0d13eea539c28eb72b51d882b141a5d04e7e90bd8a34654e81690de59b4`。

EXE SHA-256 `b8a8f40d104dd31f015a0c7cd4edc7630855b49d1c97ad2a2888f33ff04bd87c`，安装器 SHA-256 `20108cf38f6739954e58f6dae3c62f217fb8bbf9601a4a73b2c70e34811f11e1`。源码基线仍是 `de5c9c0e040f5a882b848d5145af435827dd2a29`、dirty；独立候选目录 `dist/OpenDesk-0.13.1-20261023-114055/` 保留，固定目录中的原 0.12.0 已备份到 `.local/transparent-launch-20261023-114055/previous-stable-portable/`。切换只停止已核对路径、产品名和启动时间的原 OpenDesk PID 24008。

这次没有原生窗口内新动作的实际点击/透明视觉截图、安装器安装卸载、多显示器/DPI或长期运行验收；浏览器透明播放与原生启动分别记录。没有 Git 提交、推送或公开发布。此前用户请求的 0.13.0 重打包也曾生成安装器并启动，属于旧不透明素材的本地执行记录；下节初次 0.13.0 验证仍保留原始时间范围，不改写为透明通过。

本机证据：`.local/amu-thinking-transparent-check.log`、`.local/amu-thinking-transparent-build.log`、`.local/new-companion-video/amu-action-04/transparent-candidate/`、`.local/amu-thinking-alpha-real/{result.json,browser-playback.json,screenshot-pixel-evidence.json,real-alpha-playing-stable.jpg}`、`.local/smoke/20261023-114106-876/`、`.local/transparent-launch-20261023-114055/{package.json,launch.json,verification.json}`。预览服务和标签已清理，用户运行的新版保留。

## 2026-10-23：阿沐托腮思考动作（本地 0.13.0）

用户确认视频 (4) 属于现有阿沐，并接受半身、保留电脑桌场景。新增 `thinking` 动作，不创建新角色、不改写阿宝或阿沐原骑行 `loop.png` / `still.png`。原视频 720×1280、145 帧、24 FPS、精确 145/24 秒；运行时使用去除音轨、字幕/数据轨、章节及用户 metadata 的 H.264 派生视频，通过视频流复制保留每帧画面和时序，不重新编码、插帧、变速、扣像或补身体。

运行时 `thinking.mp4` 为 1,166,241 字节，SHA-256 `07f3a1a7a33c62d11ad971b8c872efd003041c69bf6a051d4533141a96f687c4`。768×768、单次播放的 62,030,218 字节 APNG 另存 `dist/companion-assets/amu-01/thinking/thinking.png`，不加入运行时；其完整场景等比缩放至 394×700，只在画布边距透明。静态首帧单独保留，不替换骑行静态图。

模型以 `primaryAction: thinking` 和独立按钮提供动作。视频在 `loadeddata` 后实际 `play()` 成功才报告就绪，`ended` 是自动返回骑行的依据，6042ms 仅为时长元数据。重复点击建立新视频节点，过期 load/play/error/ended 回调被隔离；暂停、隐藏、减少动效、换角色及卸载清理当前播放。旧图片动作仍兼容原有默认时长，计时从图片加载成功后开始，加载失败不冒充动作完成。

| 验证 | 本次实际结果 |
|---|---|
| 视频素材 | 原视频与运行时 MP4 的全部 145 帧解码 SHA-256、时间戳逐项相同；仅一个 H.264 视频轨，原文件摘要未变 |
| 逐帧导出 | APNG 全 145 帧经 Pillow/FFmpeg 双解码与处理帧完全一致，play count 为 1；完整源场景缩放与透明边距核对通过 |
| 转换工具 | `scene`、`--plays 0/1`、`--export-scene-video` 正向 CLI 检查通过；错误 profile、预览模式、非 H.264、截取范围及非法次数明确拒绝。旧抠像和归一化函数保持不变，默认 APNG 封装复现已发布的旧资源摘要 |
| 版本、类型、架构、自动化 | `npm run check` 通过：46 项版本/分发、119 项前端、69 项 Python，共 234 项；73 个源文件架构检查通过。Rust 实现未改，本轮未重复其单元测试 |
| 浏览器动作 | CUA 可见 Chrome 实测视频自然尺寸 720×1280、duration 6.041667；5.785 秒仍播放，随后回到骑行。重复点击从 2.792 秒返回 0.227 秒，新旧节点不同，旧节点已脱离 DOM、暂停并移除 src |
| 浏览器取消与尺寸 | 暂停、换角色、收起均移除视频且没有残留错误；初次截图发现视频自然比例撑大网格，改为明确方形尺寸与 `object-fit: contain` 后，160/256px 两档均完整包含在角色框和视口内；修复后结束返回骑行仍通过，无 warn/error |
| 最终本地构建 | Tauri `build --no-bundle` 成功，最终 60 文件候选位于 `dist/OpenDesk-0.13.0/`；前端和 Tauri 嵌入视频摘要均等于生产 MP4。随附塔防仍为原 0.1.7 字节，没有变更或重发模块 |
| 最终原生启动 | 隔离数据下阿宝/阿沐初始图像 ready、设置往返、显示/隐藏/重开均通过；宿主 WebView/IPC 与冻结 worker 启动、重启、停止通过，worker PID 30012 / 71652 |

最终 EXE SHA-256 为 `07b08e344dcaa6d6669585f4769b41b3133d5c23d837ab306209d8eab7dd540c`；来源基线 `de5c9c0e040f5a882b848d5145af435827dd2a29`，工作树如实标记 dirty。初次存在视频尺寸问题的候选仅保留在 `.local/previous-builds/amu-thinking-before-video-size-fix/` 作诊断，交付路径已替换为最终重建。

浏览器实际动作验证与原生窗口启动是不同证据：本轮没有执行原生窗口内的新动作点击、实际多显示器/DPI或长期运行验收。未生成 0.13.0 安装器，未提交、推送或公开发布；此前公开版本及其产物保持原记录，新素材不继承 2026-10-22 的发布授权。

本机证据：`.local/new-companion-video/amu-action-04/processed/{verification.json,scene-video-verification.json}`、同级 `video-export-tests/results.json`、`regression/results.json`、`.local/amu-thinking-check.log`、`.local/amu-thinking-build-final.log`、`.local/amu-thinking-local-build.json`、`.local/amu-thinking-review/{browser-evidence.json,sizing-evidence.json,thinking-256-contained.jpg,thinking-160-contained.jpg}`、`.local/amu-thinking-native/20261023-104541-336/`、`.local/smoke/20261023-104546-327/`。浏览器验证使用独立预览，不修改真实设置，两次专用预览进程和标签已清理。

## 2026-10-22：App 与正式页签分别发布（0.12.0 / tower-assistant 0.1.7）

用户明确要求提交当前代码，并按 App、正式页签各自的版本提供下载；进一步限定主 App 仅随附塔防山贼助手，模块测试台及其他额外导入、非当前开发归属的包不上传。新增显式 `config/distribution.json`；打包及发布准备入口检查精确模块清单、实际目录内容、摘要和来源分支。`test-lab` 源码只用于开发验证，其基线与其他 fixture 由隔离 smoke 显式导入，不进入正式 App 或独立发布附件。

源码检查阶段 `npm run check` 已通过 46 项版本/分发边界测试、108 项前端和 69 项 Python 测试，共 223 项；类型、72 个源文件架构检查通过。新增用例覆盖未知/重复/测试模块公开拒绝、残留导入包拒绝、同版本异内容保护、缓存身份和发布产物缺失/篡改。旧构建目录已完整保留到 `.local/previous-builds/distribution-20261022/`，没有删除本机已导入包或用户选择。

源码提交并推送为 `5f56e901dd07a87663e8a8f77d0167e0fc5304bb`。App 与模块均从该干净提交构建，来源记录为 `dirty: false`，再准备 `dist/releases/app/0.12.0/`、`dist/releases/modules/tower-assistant/0.1.7/`。以下为本次实际执行结果：

| 验证 | 结果 |
|---|---|
| Rust 与格式 | 全部 34 项测试、fmt 检查通过；包含 fixture 索引缺失/重复、路径边界、身份和摘要拒绝。与上述 223 项合计 257 项自动化测试通过 |
| 测试启动前检查 | PowerShell 语法与 6 项无效 fixture 前置拒绝检查通过，拒绝时未启动宿主 |
| 构建 | Tauri release、NSIS、60 文件便携目录构建成功，`dist/app-build.json` 绑定实际提交与每个文件摘要；正式 bundle 仅塔防助手 0.1.7 |
| 原生宿主 smoke | WebView、IPC、设置往返、冻结 worker 启动/重启/停止通过；worker PID 6660 / 74280，退出无残留 |
| 原生页签 smoke | 确认初始目录仅塔防；从独立 fixture 导入测试台基线后，真实 iframe/worker 完成 `1.0.6 → 1.1.0 → 1.0.6`，UI 加载失败恢复、禁用、过期会话拒绝、新 ID 导入及选择持久化通过 |
| 原生宠物 smoke | 正式 0.12.0 分别显式选择阿宝与阿沐，图片 ready、设置往返、显示/隐藏/重开通过 |
| 内容审计 | 塔防 ZIP 70 文件，冻结业务仅 tower-assistant；宿主 worker 仅框架。App ZIP 60 文件逐项与目录及构建收据相同，唯一 `.ndpkg` 为塔防 0.1.7；没有 test-lab、fixture、原视频、imports/installed、私有模块或运行配置 |
| 安装器边界 | 核对 NSIS 实际编译输入清单，唯一页签同为塔防 0.1.7；安装器摘要与构建收据一致。未执行安装/卸载，不将清单核对当作安装测试 |
| 发布入口失败行为 | 实际调用独立发布入口时拒绝 `test-lab`；重复暂存已存在的模块版本目录明确失败，原包摘要不变 |
| GitHub CI | [main 提交](https://github.com/utxnimda/OpenDesk/actions/runs/39712678062)、[App 标签](https://github.com/utxnimda/OpenDesk/actions/runs/39712898819)、[页签标签](https://github.com/utxnimda/OpenDesk/actions/runs/39712898567) 均通过；页签标签额外核对公开清单与源 manifest 版本 |
| 远端附件 | App 6 个附件、独立页签 4 个附件均已从 GitHub 下载，逐个 SHA-256 与本地产物一致；App 随附及单独下载的塔防包字节相同 |

[OpenDesk v0.12.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.12.0) 与 [塔防山贼助手 v0.1.7](https://github.com/utxnimda/OpenDesk/releases/tag/modules/tower-assistant/v0.1.7) 已于北京时间 2026-10-22 10:50 公开发布，均非草稿；App 被设置为 Latest，插件独立发布不抢占 App 最新版入口。公开下载链接无登录请求返回 HTTP 200。旧 v0.6.0 Release 保留，未覆盖历史标签或附件；测试台未发布。

| 发布文件 | 字节数 | SHA-256 |
|---|---:|---|
| `OpenDesk_0.12.0_windows-x64_portable.zip` | 78,174,984 | `56666d7edb2cbf214368270d848285bd979e9ab85b0f525ba3fe22c60ac303b3` |
| `OpenDesk_0.12.0_x64-setup.exe` | 75,594,030 | `19b8d395e9b09f24b94e021d1e9ab4b40a96f7b63d3ed8cad3d9d885610c8960` |
| `tower-assistant-0.1.7.ndpkg` | 15,433,091 | `132e67dfaf67edfdda35245ec44ac6fb36fbea93370cb11276e173461e99a151` |

本次已执行 Git 提交、main 推送、两个版本标签推送及上述两个 Release 发布，范围依据用户 2026-10-22 的明确请求。未执行干净机器安装/卸载、多显示器/DPI 或长期运行验收，安装器未签名。远端依赖安装曾较慢，最终三个 CI 均成功，没有把等待期间的状态当作通过。

本机证据：`.local/distribution-check.log`、`.local/distribution-build.log`、`.local/distribution-rust-tests.log`、`.local/module-smoke-preflight-checks.json`、`.local/smoke/20261022-104120-946/`、`.local/module-smoke/20261022-104119-413-61f70e5f/`、`.local/distribution-companions-native/20261022-104513-089/`、`.local/app-release-download-verification.json`、`.local/module-release-download-verification.json`、`.local/release-downloads/20261022/`、`.local/release-ci-main.log`、`.local/release-ci-app-tag.log`、`.local/release-ci-module-tag.log`。这些本机文件、原始视频、fixture 和生成包没有进入 Git。

## 2026-10-21：阿宝命名与阿沐全身骑行素材（0.11.0）

第一只跑步角色正式显示为“阿宝”，保留稳定 ID `traveler-01`，97 帧动画及静态图的摘要没有变化。第二只“阿沐”使用本次补充的完整人物与自行车视频，ID 为 `amu-01`；此前裁切身体和车轮的近景视频未用于生产资源。

阿沐源视频为 145 帧、24 FPS、6.041667 秒。当前默认连续选取源帧 51–130（从 1 开始、含首尾），共 80 帧、3.333333 秒，避开片尾闭眼后回到开头的较大跳变；这是已向用户说明的可逆默认选择，不将未答复偏好视为明确选择。全 145 帧仍共同决定固定画布、缩放和底线，静态图仍取源帧 1。完整动作另存 `dist/companion-assets/amu-01/full-cycle.png`，不重复加入 App。两者都是 768×768 RGBA APNG，没有 AI 重画、插帧、逐帧居中或变速，不宣称严格无缝。

| 验证 | 本次实际结果 |
|---|---|
| 素材解码 | 阿沐默认 80 帧及完整 145 帧均经 Pillow、FFmpeg 独立解码，每帧像素与对应处理 PNG 完全一致；画布边界 alpha 全为 0，精确 1/24 秒时序及无限循环通过 |
| 可复现与回归 | 从原视频重新处理后得到默认短循环；完整动作与已检查候选逐字节相同。默认 purple 分支完整重建阿宝后，原两份资源摘要保持不变。三段原视频摘要均未变化 |
| 素材工具边界 | 8 类无效区间/profile 参数明确退出 2、未写处理帧；独立检查确认非空输出目录不被覆盖。FFprobe/FFmpeg 设置阶段超时，失败不返回成功；最终增加超时控制不改变像素算法，执行与最终工具摘要分别记录 |
| 版本、类型、架构与自动化 | `npm run check` 通过：33 项版本、108 项前端、69 项 Python 测试，共 210 项；72 个源文件架构检查通过。原生实现未变，本轮未重复 Rust 单元测试，31 项结果仍属于前一节 0.10.0 证据 |
| 浏览器交互 | CUA 控制的独立 Chrome 可见标签验证阿宝/阿沐选择、暂停由 loop 切 still、恢复切回 loop；仅有实际暂停与收起控件；两图自然尺寸 768×768、CSS filter 为 none，无背景入口，无 warn/error |
| 原生双角色 smoke | 0.11.0 真实 EXE 在两个独立数据根明确选择 `traveler-01`、`amu-01`，均通过图片 ready、设置往返、显示/隐藏/重开；测试完成退出 |
| 原生宿主 smoke | WebView、IPC、设置往返与冻结 worker 启动/重启/停止通过，worker PID 36160 / 54996；测试完成无该候选 worker 残留 |
| 构建与包 | Tauri release 与 NSIS 成功；独立便携目录 61 文件，ZIP 所有文件摘要与原目录一致。随附塔防 0.1.6、测试台 1.0.6 摘要与 0.10.0 完全一致，未重复模块版本切换 smoke |
| 正常桌面启动 | 替换本任务拥有的 0.10.0 实例，经 Explorer 启动 0.11.0，PID 10160；主界面与默认阿宝图片 ready、无 error。保留 Codex/浅色、152px、桌面宠物启用状态及全部模块选择 |
| 退役素材复核 | 当前源图、前端构建及 Tauri 编码资源共检查 14 个 PNG 文件，仅阿宝/阿沐四种摘要，无旧五份素材匹配，已撤回路径保持不存在 |

正常桌面核验第一次因 Windows PowerShell 默认编码误读含中文的 UTF-8 模块清单而失败；修正本机只读核验脚本的显式 UTF-8 读取后通过，没有改写应用数据或放宽模块路径校验。初次失败报告保留在 `.local/launch-0.11.0/verification-first-encoding-error.json`，不当作应用启动失败。

生产资源见 `apps/features/companion/assets/{traveler-01,amu-01}/`；阿沐默认循环 SHA-256 为 `1019ad07b9ef2421e409b7e8417ec99ec0bb8f4493fe265ad78f2c7c8cc03fc9`，完整动作 `d9ebec6db1999c6d08184f506b87efe6982e0d1e5cd188c59ac8876facdf8d00`。来源、选帧与工具信息在各自 `SOURCES.md` / `provenance.json` 中。原始视频和音轨未进入源码或应用包。

本地便携目录为 `dist/OpenDesk-0.11.0/`；ZIP、安装器及摘要位于 `dist/releases/0.11.0/`。ZIP 为 86,956,239 字节，SHA-256 `4f300c7c8ba84ce75795e1717206120826a7bdf979286d2921f27e0171d8cddd`；NSIS 为 83,772,512 字节，SHA-256 `c08815665a218f0fafec4b11d9d96cc810fa3597596c923efeee3d252fda7e0b`。未执行干净机器安装/卸载、多显示器/DPI、原生窗口手动拖动或长期常驻验收；浏览器截图和原生图片 ready 不冒充这些结果。未提交、推送或公开发布。

本机证据：`.local/new-companion-video/amu/candidate/selected-reproduction/{report.json,selection-verification.json,installation.json}`、同级 `final-purple-regression/selection-verification.json` 与 `range-validation/results.json`、`.local/companion-video-readonly-review.json`、`.local/two-companions-check.log`、`.local/two-companions-build.log`、`.local/two-companions-review/`、`.local/two-companions-native/20261021-180415-159/`、`.local/smoke/20261021-180420-800/smoke-result.json`、`.local/launch-0.11.0/verification.json`、`.local/two-companions-source-hashes.json`、`.local/replaced-companion-audit.json`。

## 2026-10-21：撤下未授权旧素材并接入用户视频宠物（0.9.1 / 0.10.0）

用户明确要求删除“小鸭薄荷”后，删除了五个原始 PNG/APNG、角色注册与旧来源说明；撤回本工程中含它的 0.8/0.9 宿主 EXE、便携 ZIP、安装器、四张预览截图和本任务参考克隆。使用 `cargo clean --package opendesk --release` 清理已确认嵌入素材的本 crate 构建产物，未清理第三方依赖或原 Pet 工程。旧版本的文字记录和摘要保留，旧 release manifest 标记为 withdrawn；下文历史截图或产物链接可能已失效，不代表这些已撤回文件仍可分发。

0.9.1 记录先撤下素材与空目录保护；用户随后提供第一段全身跑步视频并要求继续制作，0.10.0 接入新模型 `traveler-01`，显示名“旅途伙伴”。第二段骑行视频下半身和车轮被裁切，用户明确选择等待补充全身视频，故第二只仅作本机检查，没有进入生产角色目录。

新资源由用户第一段视频逐帧处理：去除紫底和地面投影，生成连续透明边缘，保留灰衣、鞋、背包及头发；全帧使用共同包围盒和固定基线，没有逐帧重居中、AI 重画或动作插值。输出为 768×768、97 帧、精确 24 FPS、4.041667 秒的 RGBA APNG 及同画布静态首帧。保留源视频站立—跑步—站立节奏，源首尾不是相同帧，因此不将其声称为完全无缝的新生成循环。

模型目前仅注册实际提供的 `idle` 与 `still`。`walk`、`wave`、`sleep` 改为可选，缺失或复用同一资源时不提供额外动作按钮；暂停与系统减少动效优先静态形象。显式空目录、失效模型与旧启用设置仍有守卫，避免创建无资源窗口或假成功动作。

| 验证 | 本次实际结果 |
|---|---|
| 素材完整性 | Pillow 与 FFmpeg 独立读回全部 97 帧，每帧像素与处理 PNG 完全一致；画布边界 alpha 全为 0；帧数、无限循环、精确时长均通过 |
| 可复现性 | 使用正式 `scripts/prepare-companion-video.py` 从原视频重新完整抽帧、处理、封装，APNG 与静态图均与生产资源逐字节一致；原始两段视频 SHA-256 前后不变 |
| 编码兼容处理 | 最初优化 OVER 封装在两个解码器之间出现差异，最终改为完整 SOURCE 帧并通过双解码检查；保留通过画面的像素和时序 |
| 版本、类型、架构、自动化 | `npm run check` 通过：33 项版本、108 项前端、69 项 Python 测试通过，72 个源文件的架构检查通过；Rust 0.10.0 的 31 项测试与最终 fmt 检查通过，共 241 项 |
| 浏览器交互 | 正式 App 读取 768×768 循环资源，暂停后明确切到 `still.png`；只显示暂停与收起等实际可用控件，没有虚构挥手/休息素材；浏览器检查与原生窗口实际点击是不同证据 |
| 原生宠物 smoke | 真实 EXE 以明确 `modelId=traveler-01` 启动，图片 ready、设置往返、显示/隐藏/重开全部通过 |
| 原生宿主 smoke | WebView、IPC、设置往返及冻结 worker 启动/重启/停止通过，worker PID 67264 / 24188，退出后无测试 worker 残留 |
| 模块与打包 | 随附塔防 0.1.6、测试台 1.0.6 的摘要与 0.9.0 已验证包完全一致，本轮未重复模块版本切换测试；生成 NSIS 与 61 文件便携包，ZIP逐文件摘要与目录一致 |
| 正常桌面启动 | 经 Explorer 启动 0.10.0，PID 20196；主前端与新模型图片 ready，启动无 error；保留用户 Codex/浅色、152px 宠物大小和模块选择 |
| 撤回核验 | 当前源素材、前端产物和 Tauri Brotli 编码资源共检查 6 个 PNG 文件，仅两种新摘要，无五种旧素材摘要；确认旧待撤回路径均已移除 |

新原始视频未放入源码或分发包，来源说明只记录用户提供并委托本地处理的事实，不推定额外公开分发许可。安装器已生成，未做干净机器安装/卸载、原生多显示器/DPI及长期运行验收。没有提交、推送或公开发布。

新素材位于 `apps/features/companion/assets/traveler-01/`，来源与参数见其 `SOURCES.md` / `provenance.json`；应用及本地包分别位于 `dist/OpenDesk-0.10.0/`、`dist/releases/0.10.0/`。上下文整理见[宠物资源重建上下文](companion-assets.md)。

本机证据：`.local/new-companion-video/reproduction/{report.json,verification.json,production-comparison.json}`、`.local/video-companion-check.log`、`.local/video-companion-native-tests.log`、`.local/video-companion-build.log`、`.local/video-companion-review/`、`.local/video-companion-native/20261021-171719-538/companion-smoke-result.json`、`.local/smoke/20261021-171759-403/smoke-result.json`、`.local/launch-0.10.0/verification.json`、`.local/removed-mint-assets.json`、`.local/withdrawn-companion-artifacts.json`、`.local/replaced-companion-audit.json`。

## 2026-10-21：OpenDesk 0.9.0 移除背景与氛围、重做深海透明风格

按用户后续要求移除「背景与氛围」整项功能：设置页分类、背景导入/读取 IPC、图片显示与缓存、场景/粒子、用户材质及动效调节均删除。`Personalization` 只保留宠物配置；旧 schema 1 的已知 `background` / `effects` 字段兼容读取后忽略，读取本身不改文件，显式保存时不再输出退役字段。未知字段、坏宠物配置和未来 schema 仍拒绝。已有图片文件不读取、不删除；图片专用 JPEG/WebP 依赖及本次为 Blob 预览增加的 CSP 能力一并撤回。

根据用户提供的界面参考图，将原 `glass` 布局重做并命名为「深海透明」，保留存储/协议 ID。采用 50px 顶部标签、190px 次级侧栏（最小窗口为 170px）、青色细边、低圆角和紧凑分区；建议深色配色使用蓝黑半透明分组和静态底色，浅色仍遵循主题变量。这里是应用内部的表面质感，不把主窗口改成透出其他桌面应用的透明窗口。其他五种风格和两种宠物模式保留。

| 验证 | 本次实际结果 |
|---|---|
| 版本、类型、架构与自动化 | `npm run check` 通过；33 项版本、95 项前端、69 项 Python 测试全部通过；72 个源文件的架构检查通过 |
| Rust 与格式 | `cargo test --release --locked --offline` 在 0.9.0 上 31/31 通过，`cargo fmt --check` 通过；覆盖旧背景字段兼容丢弃、宠物偏好保留、未来 schema/未知字段保护及退役 CLI 参数提前拒绝 |
| 浏览器外观 | 检查深海透明深色、浅色；设置仅三类、无背景/场景 DOM；1280×720 与 860×620 无宿主横向或 body 纵向溢出；最小窗口顶部标签完整，主内容无需额外滚动；检查时无 warn/error |
| 模块滚动与协议 | 显式模拟适配器配合真实跨来源 iframe/SDK；1280×720 下宿主 main 为 646/646、overflow hidden，iframe body 为 619/2432、overflow auto；收到 glass 材质、motion off，只有模块内部长内容滚动 |
| 原生宠物与升级设置 | 使用真实旧 0.8 设置但不提供旧背景图片，`--companion-smoke --enable-companion` 通过；宠物图片加载、显示/隐藏/重开和设置往返均成功，保存后的个性化只含 pet，未创建壁纸目录 |
| 原生宿主 smoke | WebView、IPC、设置往返、打包 worker 启动/重启/停止通过；worker PID 81852、50004，测试退出后无候选 worker 残留 |
| 原生模块 smoke | 真实 iframe/worker 完成 `1.0.6 → 1.1.0 → 1.0.6`，新增页签、禁用拒绝、不兼容原件保留、旧会话拒绝和 UI 失败恢复通过 |
| 本地出包 | Tauri release、NSIS 与 61 文件便携包生成成功；ZIP 逐文件 SHA-256 与原件一致；塔防助手 0.1.6、测试台 1.0.6；来源仍如实标记 dirty |
| 实际切换启动 | 经 Explorer 正常启动 0.9.0，PID 30152，前端和桌面宠物 ready，启动无 error；普通桌面数据视图下核对退役设置已移除、其他偏好与模块选择保持、原背景文件 SHA-256 未变；现有 Codex 风格与跟随系统配色保留 |

当前程序为 `dist/OpenDesk-0.9.0/OpenDesk.exe`；安装器、ZIP 和校验清单位于 `dist/releases/0.9.0/`。浏览器截图是实际前端预览，模块布局截图来自明确标识的模拟夹具，不冒充原生窗口点击。未进行干净机器安装/卸载、原生窗口拖动或多显示器/DPI 实机验收。宠物透明矩形鼠标命中的已有边界保持。没有 Git 提交、推送或公开发布。

本机证据：`.local/remove-background-check.log`、`.local/companion-native-tests-final.log`、`.local/remove-background-build.log`、`.local/transparent-style-review/`、`.local/companion-native/20261021-163034-990/companion-smoke-result.json`、`.local/smoke/20261021-163048-079/smoke-result.json`、`.local/module-smoke/20261021-163142-878-0b048be4/module-smoke-result.json`、`.local/launch-0.9.0/verification.json`。

## 2026-10-21：OpenDesk 0.8.0 背景、流光玻璃与宠物陪伴

设置新增「背景与氛围」「宠物陪伴」分类，增加第六种流光玻璃布局、三处背景位置、磨砂材质、星尘/樱花及可关闭动效。壁纸由 Rust 有界导入并保存到应用数据目录，前端仅持有图片标识和受控显示数据；示例原图未加入源码或分发包。前端使用可释放的 Blob URL 显示大图，避免大型 data URL 写入 CSS 变量后失效。

宠物提供应用内与桌面两种模式。桌面模式由主应用进程创建独立透明窗口，关闭宠物为临时隐藏，退出应用销毁宠物；应用内模式可拖动、收起和唤回。角色资源来自用户指定 Pet 仓库的固定提交，经过显式组合根注入框架，未引入原工程的网络、音频和 Electron 行为。资产来源和实际授权信息见 `apps/features/companion/assets/SOURCES.md`。

| 验证 | 本次实际结果 |
|---|---|
| 版本、类型、架构与自动化 | `npm run check` 通过；33 项版本、94 项前端、69 项 Python 测试全部通过；76 个源文件的架构检查通过 |
| 原生测试与格式 | `cargo test --release --locked` 30/30 通过；`cargo fmt --check` 通过。覆盖旧设置补全、壁纸尺寸/格式/路径边界、窗口缩放后坐标约束、验证入口参数及普通单实例保留 |
| 浏览器背景与氛围 | 使用指定的 6015×4093 示例图片实际导入预览；整窗、工作台封面、导航区切换，模糊滑条和关闭动效均检查；流光玻璃设置页只有 `main-content` 一个纵向滚动容器；检查时无浏览器 warn/error |
| 浏览器宠物交互 | 实际点击挥手显示气泡、暂停切换静态资源、拖动改变位置、收起后唤回，图片加载成功；此处使用应用内模式，不冒充原生桌面拖动验收 |
| 新风格模块滚动与同步 | 显式模拟适配器配合真实跨来源 iframe/SDK；1280×720 下 glass 宿主 main 高度/内容均 619，iframe body 为 592/4303；anime 宿主均 575，iframe 为 548/3612。宿主为 hidden，iframe 为 auto；风格、材质和动效同步正确，切换后仍为同一实例 `browser-fixture-1` |
| 原生个性化 smoke | 发布 EXE 导入示例图，得到 2560×1742 规范化背景；设置落盘往返、壁纸读取、宠物真实图片加载、显示/隐藏/重新显示全部通过，角色为 `mint-duck`，宿主退出码 0 |
| 原生宿主 smoke | WebView 挂载、IPC、设置往返及冻结 worker 启动/重启/停止通过；worker PID 为 62408、69816，测试后无候选目录下进程残留 |
| 原生模块 smoke | 真实 iframe/worker 完成 `1.0.5 → 1.1.0 → 1.0.5`；新增模块、禁用拒绝、不兼容原件保留、过期会话拒绝及 UI 失败恢复通过 |
| 本地构建与便携包 | Tauri release 与 NSIS 生成成功；便携目录 61 文件，ZIP 逐项 SHA-256 与目录原件一致；产品元数据为 OpenDesk 0.8.0；随附塔防助手 0.1.5、测试台 1.0.5，包来源如实标记 dirty |

候选位于 `dist/OpenDesk-0.8.0/`，ZIP、安装器、摘要及来源清单位于 `dist/releases/0.8.0/`。验证期间保留用户自行运行的 `dist/OpenDesk/` 旧实例，不覆盖其文件。首次候选提前退出且没有生成报告，未计为通过；包含最终验证入口的重新构建通过上述三种原生 smoke。只有显式绝对测试数据根下的验证模式跳过单实例，普通启动仍保持单实例；WebView2 缓存不因此完全隔离。并行保留旧实例时，日志如实记录原有全局快捷键已被注册的 warning，不把该次 smoke 当作快捷键验收。

当前限制：桌面透明窗口的矩形范围仍参与鼠标命中，没有实现按图片透明像素穿透；原生窗口拖动、实际多显示器/DPI、长时间常驻与干净机器安装/卸载未验证。安装器已生成，不代表已安装或签名。当前改动未提交、推送或发布 GitHub Release。

用户确认切换后，0.8.0 于当日 16:16 通过 Explorer 正常启动，进程 PID 78184；主前端、宠物图片就绪，示例背景导入，普通桌面数据视图中的随附新模块安装完成，本次启动无 error。直接从 Codex 的打包运行环境启动时，Windows 将新建 AppData 目录重定向至该包的 LocalCache，导致路径包含检查拒绝；普通 `Shell.Application` 调用也未改变这一上下文。通过 Explorer 打开仅含指定启动参数的本地快捷方式后，查询进程返回 `APPMODEL_ERROR_NO_PACKAGE`，问题消失；没有改动模块存储校验或安全策略。该重定向行为可参考 [Microsoft 的 MSIX 文件系统说明](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes)。此前在打包环境取得的配置快照属于另一合并视图，不能当作普通桌面数据的逐字节升级前快照；后续验证已从正常桌面上下文单独保存基线。该过程的证据位于 `.local/launch-0.8.0/20261021-161116-199/actual-startup-verification.json` 与 `.local/explorer-launch-package-identity.json`。

本机证据：`.local/personalization-check-final.log`、`.local/personalization-native-tests-final.log`、`.local/personalization-build-final.log`、`.local/personalization-review/`、`.local/personalization-native/20261021-154503-499/personalization-smoke-result.json`、`.local/smoke/20261021-154524-973/smoke-result.json`、`.local/module-smoke/20261021-154559-922-a00edc09/module-smoke-result.json`。

## 2026-10-20：OpenDesk 0.7.0 五种界面风格与模块滚动

在原简洁风格上增加 Codex、Cursor IDE、现代 Web 与二次元布局，风格与已有配色分别选择。设置支持布局缩略图、显式建议配色、即时预览与保存；重复点击当前设置入口保持预览，保存期间锁定草稿控件。旧 schema 1 文件缺少 `uiStyle` 时只在内存中补为 `simple`，未知值拒绝并保护原文件。

模块外壳改为窗口剩余空间中的 flex 布局，去除固定 650px iframe 高度、重复卡片边框和重复模块标题。宿主不测量或操作跨来源模块 DOM；业务文档的长内容由 iframe body 滚动。协议 1 新增可选 `uiStyle`，旧消息与旧包保持兼容。塔防助手 0.1.4、测试台 1.0.4 包含新样式，用户原有包选择不自动替换。

| 验证 | 本次实际结果 |
|---|---|
| 版本、类型、架构与自动化 | `npm run check` 通过；33 项版本、77 项前端、69 项 Python 测试通过，61 个源文件的架构检查通过 |
| 原生测试 | `cargo test --release --locked`：15 项通过，包含五种风格落盘重读、旧设置兼容和未知值保护 |
| 浏览器外观 | 查看五种风格工作台与设置缩略图，检查默认窗口和最小桌面尺寸 |
| 浏览器模块布局 | 显式模拟 HostAdapter，使用真实 App、PackageFrame、跨来源 iframe、MessageChannel、SDK 和共享 CSS；五种风格在 1180×800、860×620 下，宿主 html/body/main 均无纵向溢出，main overflow 为 hidden，iframe body 为 auto 且长内容可溢出；无宿主横向溢出 |
| 浏览器设置与协议 | 界面操作保存五种风格后模块收到对应值；切换期间实例 generation 保持；同页导航不取消预览，未保存离开恢复，保存后重载保持 |
| 五种原生启动 | 五份独立数据根分别预置五种风格，真实发布 EXE 均报告 frontendMounted、IPC、设置往返通过，保存后的 uiStyle 与预置值一致 |
| 模块运行 | 真实桌面模块 smoke 通过 `1.0.4 → 1.1.0 → 1.0.4`，新增页签、禁用拒绝、不兼容原件保留、过期会话拒绝与 UI 失败恢复通过 |
| 构建与正常启动 | 0.7.0 NSIS、61 文件便携目录和 ZIP 生成；默认用户数据根启动成功，窗口响应且日志记录 frontend.ready，没有本次启动 error |

浏览器回归夹具仅提供测试设置与目录，不使用真实 worker；其 DOM 尺寸和协议证据不冒充原生执行。当前浏览器工具无法向该跨来源 iframe 发送点击/键盘事件，因此未用其证明滚轮操作或点击计数器保留；原生模块执行由独立的桌面 smoke 验证。没有进行完整可访问性审计或干净机器安装验收。

证据位于忽略目录：`.local/ui-style-check.log`、`.local/ui-style-rust-tests.log`、`.local/ui-style-build.log`、`.local/ui-style-review/scroll-metrics.json`、`.local/ui-style-review/settings-flow.json`、`.local/ui-style-native/results.json`、`.local/module-smoke/20261020-204641-556-ac6cb709/module-smoke-result.json`。布局测试夹具及其操作步骤位于 `.local/ui-style-fixture/`，未引入生产组合根。

当前改动未提交或推送，没有发布 0.7.0 GitHub Release；本地产物如实标记工作区 dirty。旧 0.6.0 生成目录在 `.local/previous-builds/20261020-ui-styles/` 中保留。

## 2026-10-20：OpenDesk 0.6.0 改名、清理及打包启动

按用户要求，将当前应用名称、O 字母图标、窗口/托盘、应用标识、Python 包、worker、构建变量和发布产物统一为 OpenDesk。标识为 `net.nimda.opendesk`；运行与构建变量使用 `OPENDESK_*`。既有版本历史不重写，宿主追加 `0.6.0`、`core.workbench` 追加 `0.4.0`，自带塔防助手与测试台分别发布 `0.1.3`、`1.0.3`。

模块 protocol 1 保留既有消息标识；hostApi 1 的旧模块数据目录环境变量作为兼容别名指向同一个新目录，不创建旧目录。修正 PowerShell smoke 脚本清除及恢复环境变量的方法，避免将原本不存在的变量恢复为空字符串。

| 验证 | 本次实际结果 |
|---|---|
| 工具链 | Windows x64；Node 24.14.1、Python 3.12.10、Rust/Cargo 1.99.0、VS 2022/MSVC 14.44、Windows SDK 10.0.26100.0、WebView2 155.0.4283.62 |
| 版本、类型及边界 | `npm run check` 通过；33 项版本测试、63 项前端测试、69 项 Python 测试通过，60 个源文件的架构边界检查通过 |
| 原生测试 | `cargo test --release --locked`：12 项 Rust 测试通过 |
| 发布构建 | `npm run build` 通过，生成 `OpenDesk.exe`、`opendesk-worker.exe`、NSIS 安装器及完整便携目录 |
| 桌面 smoke | `frontendMounted`、`ipc`、`settingsRoundTrip` 为 true；后台启动、重启及停止通过，无残留测试 worker；脚本退出后数据目录覆盖变量不存在 |
| 模块 smoke | 真实页面及 worker 完成 `1.0.3 → 1.1.0 → 1.0.3`，验证新增页签、禁用拒绝执行、不兼容原件保留、旧会话拒绝、UI 失败恢复及独立模块不受影响 |
| 正常启动 | 实际打开 `dist/OpenDesk/OpenDesk.exe`，窗口标题 OpenDesk、窗口可响应；默认目录为 `%APPDATA%\net.nimda.opendesk`，日志记录 `frontend.ready`，本次启动无 error |
| 旧应用清理 | 已核对并删除旧 `net.nimda.nimdadesk` 的 Roaming 数据与 Local/WebView 缓存，以及本工程旧品牌的生成包、临时运行数据和原生构建产物；未发现旧品牌安装记录或持久环境变量 |

便携 ZIP 包含 61 个文件，文件名检查没有旧品牌、私有部署模块、虚拟环境或本机配置。Windows EXE 的 ProductName/FileDescription 为 OpenDesk，ProductVersion 为 `0.6.0`。本次生成产物位于 `dist/releases/0.6.0/`，附 `SHA256SUMS.txt` 和 `release-manifest.json`。

本机证据：`.local/check-opendesk-20261020.log`、`.local/rust-tests-opendesk-20261020.log`、`.local/build-opendesk-20261020.log`、`.local/smoke/20261020-195756-520/smoke-result.json`、`.local/module-smoke/20261020-195831-826-1db6901c/module-smoke-result.json`、`.local/launch-opendesk-20261020.json`、`.local/legacy-cleanup-20261020.json`。

上述验证与出包在源码提交前完成，产物来源如实保留构建时的工作区 dirty 状态。安装器已生成但未执行安装，未签名、上传或发布；后续源码提交和推送状态以 Git 历史与远端核验为准，不反向改写已生成产物的来源身份。

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
