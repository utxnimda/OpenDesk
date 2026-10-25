# 本地模块包契约

目标：App 包含宿主与默认页签包；用户可导入新页签/更新；不兼容时保留原包和错误；同页签多版本并存并选择。范围不包含市场、远程自动更新或安全沙箱。

**当前正式交付**：[App 0.18.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.18.0) 仅随附[塔防助手 0.8.2](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.8.2)，独立页签使用字节一致的同一包；来源为干净提交 `036ba0ace3583cbff410a0ae1155b46a44386e13`。2026-10-26 已公开发布，App 6 个附件与模块 4 个附件均已重新下载并核对 SHA-256。 0.8.2 与已验证的本地 0.8.1 功能一致，0.8.1 dirty 包保持原来源和摘要。测试 fixture、作品管理器附件、外部导入包及本机数据均不属于本次附件清单；历史 App 0.17.0／塔防 0.1.9 保持不变。

0.16.0 开发版新增可选 `hostApi: 2`，为已有 HTTP 服务的完整应用提供受控嵌套页面及安全停用协议，详见[本地 Web 应用接入](module-web-apps.md)。本文的静态模块示例继续使用 `hostApi: 1`，原包无需升级。

## 包格式

.ndpkg 是真实 ZIP，根为 manifest.json，只写普通文件，不写显式目录 entry，不允许链接、穿越或大小写冲突：

```text
manifest.json
frontend/index.html
frontend/assets/...
worker/module-worker.exe
worker/_internal/...
```

```json
{
  "schemaVersion": 1,
  "id": "test-lab",
  "label": "模块测试台",
  "version": "1.0.0",
  "hostApi": 1,
  "target": "windows-x64",
  "frontend": "frontend/index.html",
  "worker": "worker/module-worker.exe",
  "methods": ["test.echo"],
  "dataSchema": 1,
  "source": {"commit": null, "dirty": true, "repository": null}
}
```

无后台包用 worker:null、methods:[]。methods 为精确业务名称，禁止 runtime.* 或 module.*。source.repository 可省略或为净化 HTTPS URL/null；commit 为40/64位小写十六进制或null，dirty为真实布尔值。凭据、query和fragment不能进入仓库URL。

包内不放自引用摘要。外部 App 索引为 `{schemaVersion:1,hostVersion,modules:[{id,version,file,sha256,defaultEnabled:true}]}`，sha256为64位小写十六进制。索引描述默认随附包，不替代个人选择。

## 导入与选择

- 原件先保存再校验；失败、不兼容或冲突保留原件和错误，不执行代码、不影响正常页签。
- 同 ID/版本/摘要是重复内容；同 ID/版本异摘要是冲突，不能覆盖。
- 多版本可以安装；同页签只激活当前启用版本，其他页签保留自己的选择。
- 选择是精确版本与摘要，不保存会自动漂移的 latest。
- App 升级不得静默重置个人选择，不兼容应显示具体原因。
- 摘要用于内容身份，不是发布者签名。用户应只导入信任来源的代码包。

## 身份与通信

页面通过模块 SDK 受控请求，宿主重新核对当前包、消息身份与方法。每包 worker 保留 stdio v1，构建入口注册 `module.identity`，返回烘焙的 `{id,version,hostApi:1}`。不从用户参数、运行环境或可编辑配置读取身份。

test.echo严格接受 `{text:string}`，最多4096字符且不能含孤立代理码位，返回 `{version,marker,text,pid}`。前端和worker都烘焙版本/marker，用于证明真正切换了产物。

外观消息保留原有 `theme` 的浅色/深色契约，并通过可选 `appearance` 字段传递 `sakura`、`ocean`、`forest`、`sand` 或 `graphite`。支持这些值的新包可同步对应配色。基础 `theme` 始终保持兼容：`graphite` 传递 `dark`，其余彩色外观传递 `light`；旧包忽略自己不认识的外观值，使用基础主题显示。旧版已支持的 `sakura` 仍按该包自己的样式呈现。这是兼容扩展，不提升 hostApi。

连接消息与后续外观消息还可包含 `uiStyle`：`simple`、`codex`、`cursor`、`web`、`anime`、`glass` 或 `mint`。它独立于配色，控制支持该字段的模块公共表面样式；缺失或未知值在对应 SDK 中回退为 `simple`，旧包可以忽略该可选字段。protocol 1、hostApi 1、基础 `theme` 和既有 `nimdadesk:hello` / `nimdadesk:connect` 消息标识均保持不变，应用改名不等于更换消息标识。

`glass` 当前显示名为“深海透明”，使用深蓝黑半透明、青色细边与低圆角紧凑分区；宿主采用顶部标签栏。显示名调整不改变存储 ID、包身份或消息协议，也不恢复背景图片和氛围调节。建议配色为 `dark`，用户仍可独立选择配色。

宿主 0.15.0 新增 `mint`“薄荷奶油”，建议配色 `light`，采用静态浅粉/薄荷柔雾、深青文字、圆角卡片与轻导航。深色与石墨夜继续使用各自语义色，选择风格不强制主题。共享 CSS 与 SDK 更新已对应正式塔防 manifest 0.1.9（保留本地开发 0.1.8）、测试台本地 fixture 开发版本 1.0.7。已公开的塔防 0.1.7 保持不可变；App 升级保留用户固定版本，需要在「功能管理」手动选择已安装的 0.1.9 或兼容新版本（当前为 0.8.2），模块内才具备薄荷样式。旧包可以忽略或回退未知 `mint`，不能由宿主强改其内部 DOM。

SDK 保留 0.8.0 引入的可选 `surfaceEffects` 以兼容已有包。当前宿主根据 `uiStyle` 生成固定静态表面，表面动效关闭，没有用户材质、浓度或氛围调节。协议解析范围继续保留：

| 字段 | 取值与缺省行为 |
|---|---|
| `material` | `solid` 或 `glass`，无效值回退 `solid`；当前宿主按所选风格决定 |
| `transparency` | 40–100 的面板浓度，非法数值回退 78 |
| `motion` | 保留 `off`、`subtle`、`full` 兼容解析，无效值回退 `off`；当前宿主固定发送 `off` |
| `transparentBackground` | 仅 `true` 开启，其余值回退 `false` |

旧包可忽略整个扩展；新 SDK 缺少字段时保留纯色、无动效和不透明背景的基础表面。消息不含图片 ID、原始路径、data URL、图片字节或宠物资源。透明表面只参与静态风格呈现，不提供已移除的背景图能力；本地私人图片不能随模块包分发。

宿主设置分为风格与配色、宠物陪伴、窗口行为三类。风格与配色支持即时预览，桌面点击保存后持久化，离开设置页还原未保存的预览；浏览器只作当前会话预览。新宿主为缺少风格或宠物配置的旧 schema 1 设置补充默认值；旧 `background` / `effects` 兼容忽略，旧图片不读取、不删除。外观变化不会修改模块包版本选择或业务数据。

模块外层固定占据宿主主内容区的剩余空间，移除重复的外层标题和卡片边框。iframe 占满可用区域，模块 body 负责自身纵向滚动；宿主不读取或测量 iframe 的业务 DOM，也不根据内部内容高度扩张外层。公共表面样式由 SDK 提供，模块的具体业务布局仍由模块自己负责。

## 可选模块第二视图

正式宿主 **0.18.0**（`core.module-packages` **0.7.0**）新增通用原生透明窗口 `module-surface`，通用原生窗口链路已通过验证，实际游戏视觉叠加效果尚未验收。它是 protocol 1 / `hostApi: 1` 的可选扩展；旧包可以忽略，旧宿主不会提供 `host.surface`。塔防 0.7.0 起使用这一扩展，0.8.2 在旧 0.17.0 宿主上仍可使用基本功能，只有透明窗口入口提示需升级宿主。

模块通过 `createModuleBridge()` 握手后使用以下 SDK 契约，不直接调用 Tauri：

| SDK 接口 | 用途与边界 |
|---|---|
| `host.surface?.context` | 主页面为 `null`；第二视图为 `{view: string, data: Record<string, unknown>}`，来自宿主绑定的打开上下文 |
| `host.surface.open({view, title, width, height, alwaysOnTop, context})` | 仅从主页面申请视图；`view` 是短标识，`context` 是最多 8 KiB 的 JSON 对象，不能传任意入口 URL、HTML 或窗口句柄 |
| `host.surface.close()` | 仅关闭调用方绑定的第二视图，不关闭主包或 worker |
| `host.surface.setAlwaysOnTop(enabled)` | 调整该第二视图的置顶状态 |
| `host.surface.startDrag()` | 请求拖动该第二视图，须由模块的拖动控件触发 |

连接消息通过可选 `moduleSurface: {context}` 提供能力和上下文；`surfaceEffects` 仍只负责外观描述，两者不互相替代。SDK 用同一 MessagePort 发送 `surface` 消息，操作名为 `open`、`close`、`always-on-top` 和 `start-drag`；宿主 broker 将操作绑定到已经核验的包实例。Rust 命名命令为 `module_surface_open`、`module_surface_context`、`module_surface_call`、`module_surface_close`、`module_surface_start_drag` 和 `module_surface_set_always_on_top`。`open` 只允许主窗口调用，其余命令限定 `module-surface` 自身窗口，并校验当前 generation；模块业务调用仍只允许自身 manifest 中的方法。

`ModuleSurfaceApp` 是框架内通用 iframe 容器，加载宿主当前包实例的既有入口，由该包根据 `context.view` 选择第二视图；框架不导入具体业务组件或读取业务 DOM。第二视图借用同一 worker，不重新打开、激活、恢复或停止包，因此独立关闭不重置业务会话。主包关闭、停用或换包时撤销第二视图；旧 generation 的迟到请求不能控制新窗口。当前宿主同时保留一个第二视图，相同绑定与参数再次打开会聚焦，其他绑定或参数会替换窗口。窗口透明、无装饰、可调整大小，模块提供拖动/关闭控件，原生尺寸与位置受屏幕可用区域限制；不提供鼠标穿透。

## 分发范围与构建

`config/distribution.json` 是正式分发范围的权威配置：`bundledModules` 指定 App 随附页签，`publicModules` 指定可独立公开的页签；前者必须是后者的子集。当前默认随附仅含 `tower-assistant`，可独立公开清单另含作品管理器 `nimda-workspace`。构建定义中的 `test-lab` 标为 `testFixture`，禁止进入这两个列表。源码开发测试归属与二进制分发范围分别管理：测试台源码保留，但产物不上传。

先按 README 配置 Python venv 和锁定依赖；以下命令只构建本地文件，不提交、打标签或上传：

```powershell
npm run build:modules
npm run build:module -- tower-assistant
npm run build:module-fixtures
```

| 命令 | 输出 | 范围 |
|---|---|---|
| `build:modules` | `dist/bundled-modules/app-manifest.json` 和当前源码 manifest 对应页签包 | 本次 App 0.18.0 仅随附塔防 0.8.2；公开 App 0.17.0 的 0.1.9 原包及旧本地构建须保留 |
| `build:module -- tower-assistant` | `dist/modules/tower-assistant/0.8.2/` 下的包、manifest 与 SHA256SUMS | 本次独立正式页签，已干净构建并验证；与 App 随附包字节一致 |
| `build:module-fixtures` | `dist/module-fixtures/fixtures.json`、测试台 baseline 及四个变体 | 仅本机开发验证，不进入 App 或独立 Release |

构建与便携复制检查实际目录，只允许清单中的文件，不从本机导入/已安装目录或缓存推导范围。旧目录中的额外包会被拒绝，应保留移出后重新构建，不能默默混入 App。现有版本不同摘要拒绝覆盖；当前公开塔防为 `0.8.2`，宿主为 `0.18.0`，从 Git 主线开发且未合入本机 `0.3.0`。本次发布不改变个人选择，实际游戏透明视觉尚待验收。此前 `0.6.0`、`0.6.1`、`0.7.0`、`0.7.1`、`0.8.0`、`0.8.1` 本地包保留，不替换为不同摘要。既有公开与本地包均保留原身份和字节，0.8.1 的 dirty 来源不能冒充 0.8.2 的正式构建来源。升级 App 不卸载、覆盖或擅自切换用户此前导入的测试台或其他包，个人选择仍独立。

塔防 0.7.0 延续 0.6.1 的采集和统计实现，保留 0.5.2 修复后的包根 Worker 入口布局；新增窗口只使用宿主可选能力，业务仍保持 `hostApi: 1`、`dataSchema: 1`。当前观察脚本采集工作公式、人物资料和攻击分类；构建定义显式列出 `capture/inject.js`、`capture/observer.js` 与 `capture/runtime-worker.js`，作为固定原创采集素材放入模块 worker 运行资源。包不携带游戏原包、图集、存档、安装备份、身份令牌或事件流；不从游戏目录或 `.local` 实验目录收集素材。命名采集/统计方法由宿主按 manifest 授权，前端没有任意脚本执行入口。具体 RPC、安装副作用与版本范围见[塔防模块说明](tower-assistant.md)。构建、发布或导入该包不等于成功激活，也不会自动安装游戏采集入口；`updateRequired` 为真时，需先退出游戏、移除并恢复原包，再安装当前入口并重启，才能获得新字段。

通用宠物属于宿主能力，不是业务页签包。阿宝与阿沐的既有默认资源按用户 2026-10-22 的明确请求纳入当时 0.12.0 交付；该历史授权不自动覆盖后续动作；用户于 2026-10-23 另行明确授权执行当前 0.15.0 发布，包含已审阅的思考动作运行资源。原视频仍不上传，不附加未提供的第三方许可声明。模块构建不复制角色资源或私人图片，背景能力仍已移除。资源详情见 [宠物资源说明](companion-assets.md)。

fixtures输出 `dist/module-fixtures/fixtures.json` 和：

| 包文件 | 身份 | 预期 |
|---|---|---|
| test-lab-1.0.7.ndpkg | 当前源码 baseline，marker=v1-baseline | 在独立 smoke 根显式导入并选中，再做版本切换 |
| test-lab-1.1.0.ndpkg | 同ID，新版本，marker=v1.1-enhanced | 导入更新，前端/worker真实变化 |
| test-lab-2.0.0-incompatible.ndpkg | hostApi=99 | 保留原件并在执行代码前拒绝 |
| test-extra-1.0.0.ndpkg | 新ID，marker=extra-module | 导入新增页签 |
| test-lab-1.2.0-ui-failure.ndpkg | API1/schema1，worker:null，合法页面不发送SDK hello | 真实页面就绪超时，保留lastError并恢复此前可用版本 |

这五项仅为本地验证包，不随 App 或独立 Release 上传。smoke 从 fixture 索引读取并导入 baseline，不再把测试台当作 App 自带包。API99包由API1测试代码构建，但故意声明不兼容要求，必须在加载前拒绝，不能把它当作可运行迁移方案。

1.2.0 fixture 只有固定 HTML 和 manifest，不经过 Vite/PyInstaller，不执行后台，也不向宿主发送 SDK hello。它验证真实浏览器加载后的协议就绪失败，不是手工模拟一个宿主错误。只需要补建此包时可运行 `node scripts/module-pack.mjs ui-failure-fixture`，现有 fixture 索引中的其他项会保留。

Vite独立构建使用base='./'并烘焙 __MODULE_ID__/VERSION/MARKER。PyInstaller目录包包含运行依赖；构建器直接运行冻结worker验证身份、回显、非法参数与退出，然后压缩。

## 来源与重复构建

默认Git来源为当前工程；构建时可设置绝对 OPENDESK_SOURCE_ROOT 读取正式工程基线。跨暂存目录一律保守dirty，不能把基线commit冒充全部构建代码。环境变量只参与构建。

ZIP文件按路径排序并固定时间，PyInstaller使用固定SOURCE_DATE_EPOCH和PYTHONHASHSEED。构建缓存按模块源码、SDK、通用worker、静态风格与配色样式、规范化依赖锁、打包脚本及实际工具版本生成指纹，不能复用缺少当前表面改动的旧缓存。未变化则校验并复用原包及原source身份。宿主版本字段从依赖指纹剔除，避免纯宿主版本升级改变未更新的模块包。

缓存复用保证同一已保存产物不因重复构建而漂移；尚不承诺清空缓存或跨机器重建逐字节一致。正式分发应保留已确认的不可变模块包，不能静默放宽同版本异摘要冲突。若构建内容确有改变，应升级包版本。

## 公开暂存与独立下载

获得当次公开交付授权后，正式页签按 ID/版本建立独立下载，App 的随附包与独立页签包应核对为相同不可变产物。2026-10-23 已发布 [App `v0.15.0`](https://github.com/utxnimda/OpenDesk/releases/tag/v0.15.0) 与[塔防 `modules/tower-assistant/v0.1.9`](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.9)，均来源于干净提交 `aa7bdd7346fc1544bebff939b79ed40886c215a2`；6 个 App 附件与 4 个模块附件全部下载复核，随附和独立 `.ndpkg` 相同。本机导入的部署工作台、测试 fixture 二进制和其他私有模块均未发布。此前 0.12.0 / 0.1.7 保留，执行步骤见[发布手册](releasing.md)，直接下载见 [downloads.md](downloads.md)。

```powershell
npm run release:stage -- app
npm run release:stage -- module tower-assistant
```

暂存目录由实际版本决定；2026-10-26 使用 `dist/releases/app/0.18.0/` 和 `dist/releases/modules/tower-assistant/0.8.2/`。2026-10-23 的历史发布从 `dist/releases/app/0.15.0/` 和 `dist/releases/modules/tower-assistant/0.1.9/` 审核并上传附件，包含版本清单与 `SHA256SUMS.txt`。暂存命令本身只操作本地：要求当前源码已提交且干净；App 构建收据 `dist/app-build.json` 必须对应当前提交、版本和实际文件摘要，模块内部 manifest 与独立 manifest 相同，来源必须是本仓库干净提交且属于当前开发历史。目录或同版本附件已经存在时拒绝覆盖，后续结果文档提交不移动此次构建标签。

发布只取这些审核后的目录，禁止上传模块测试台、fixture、本机 imports/installed、私有模块或其他分支的未授权产物。构建缓存只负责复用已验证字节，不能扩大公开白名单；完整文件检查和新包审计仍应在最终构建后执行。

## 数据与验证

包目录与业务数据分离。当前只接受兼容dataSchema，不自动迁移或回退数据。旧代码不能读取当前格式时拒绝激活，不恢复旧快照覆盖新记录。

新版本的页面在当前宿主会话内未完成身份确认、超时或加载失败时，宿主尝试恢复此前选择，并在功能管理保留本次失败原因；已确认就绪后的普通业务错误不会自动回退。这个保护不等于跨崩溃事务恢复：若宿主在新选择落盘后、页面确认前被强制终止，下次启动仍尝试保存的选择，用户可以在功能管理手动改回已安装旧版本。尚未实现跨崩溃的待确认选择日志或数据迁移。

冻结worker验证不能代替桌面导入和切换验证。分别记录包结构/摘要、冻结worker、桌面导入、新ID、不兼容原件保留、同ID两版本切换与其他页签继续运行。

真实桌面模块测试入口：

```powershell
.\scripts\module-smoke.ps1
# 或指定待测宿主与fixture目录：
.\scripts\module-smoke.ps1 -Executable .\dist\OpenDesk\OpenDesk.exe -Fixtures .\dist\module-fixtures
```

脚本创建全新的 `.local/module-smoke/<时间戳-唯一后缀>` 框架数据根，清理子进程的源码/Python覆盖，以 Hidden 方式启动 `--module-smoke`，最多等待140秒，读取 `module-smoke-result.json` 并检查退出码以及本次数据根下无残留 module-worker。最后恢复调用者环境。超时只停止持有的本次进程对象，不停止其他正常应用或按名字杀进程。WebView2缓存不包含在框架数据根隔离保证中；只有实际运行后生成的结果才能证明桌面链路通过。

直接调用 EXE 时，`--module-smoke` 与另外两种 `--smoke-test`、`--companion-smoke` 一样，必须显式设置绝对路径的 `OPENDESK_DATA_DIR`，才允许跳过单实例并与正常用户实例并存。缺失、空值或相对路径，以及重复/混用 smoke 模式，在启动阶段退出 2。调用方仍需选择独立目录；路径检查不会自动判定它是否与用户数据重叠，也不隔离默认 WebView2 浏览器缓存。
