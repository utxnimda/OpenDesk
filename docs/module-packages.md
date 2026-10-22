# 本地模块包契约

目标：App 包含宿主与默认页签包；用户可导入新页签/更新；不兼容时保留原包和错误；同页签多版本并存并选择。范围不包含市场、远程自动更新或安全沙箱。

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

连接消息与后续外观消息还可包含 `uiStyle`：`simple`、`codex`、`cursor`、`web`、`anime` 或 `glass`。它独立于配色，控制支持该字段的模块公共表面样式；缺失或未知值在新 SDK 中回退为 `simple`，旧包可以忽略该可选字段。protocol 1、hostApi 1、基础 `theme` 和既有 `nimdadesk:hello` / `nimdadesk:connect` 消息标识均保持不变，应用改名不等于更换消息标识。

`glass` 当前显示名为“深海透明”，使用深蓝黑半透明、青色细边与低圆角紧凑分区；宿主采用顶部标签栏。显示名调整不改变存储 ID、包身份或消息协议，也不恢复背景图片和氛围调节。建议配色为 `dark`，用户仍可独立选择配色。

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

## 分发范围与构建

`config/distribution.json` 是正式分发范围的权威配置：`bundledModules` 指定 App 随附页签，`publicModules` 指定可独立公开的页签；前者必须是后者的子集。当前两项仅含 `tower-assistant`。构建定义中的 `test-lab` 标为 `testFixture`，禁止进入这两个列表。源码开发测试归属与二进制分发范围分别管理：测试台源码保留，但产物不上传。

先按 README 配置 Python venv 和锁定依赖；以下命令只构建本地文件，不提交、打标签或上传：

```powershell
npm run build:modules
npm run build:module -- tower-assistant
npm run build:module-fixtures
```

| 命令 | 输出 | 范围 |
|---|---|---|
| `build:modules` | `dist/bundled-modules/app-manifest.json` 和 `tower-assistant-0.1.7.ndpkg` | App 0.12.0 仅随附塔防助手 |
| `build:module -- tower-assistant` | `dist/modules/tower-assistant/0.1.7/` 下的包、manifest 与 SHA256SUMS | 正式页签独立产物 |
| `build:module-fixtures` | `dist/module-fixtures/fixtures.json`、测试台 baseline 及四个变体 | 仅本机开发验证，不进入 App 或独立 Release |

构建与便携复制检查实际目录，只允许清单中的文件，不从本机导入/已安装目录或缓存推导范围。旧目录中的额外包会被拒绝，应保留移出后重新构建，不能默默混入 App。现有版本不同摘要拒绝覆盖；正式塔防当前版本为 `0.1.7`。升级 App 不卸载、覆盖或擅自切换用户此前导入的测试台或其他包，个人选择仍独立。

通用宠物属于宿主能力，不是业务页签包。阿宝与阿沐的处理后资源按用户 2026-10-22 的明确公开交付请求纳入本次源码和 App，原视频仍不上传；不附加未提供的第三方许可声明。模块构建不复制角色资源或私人图片，背景能力仍已移除。资源详情见 [宠物资源说明](companion-assets.md)。

fixtures输出 `dist/module-fixtures/fixtures.json` 和：

| 包文件 | 身份 | 预期 |
|---|---|---|
| test-lab-1.0.6.ndpkg | 当前源码 baseline，marker=v1-baseline | 在独立 smoke 根显式导入并选中，再做版本切换 |
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

获得当次公开交付授权后，正式页签按 ID/版本建立独立下载，App 的随附包与独立页签包应核对为相同不可变产物。当前准备的 App 标签为 `v0.12.0`，塔防标签为 `modules/tower-assistant/v0.1.7`；下载目录索引见 [downloads.md](downloads.md)，是否已发布以真实 Release 为准。

```powershell
npm run release:stage -- app
npm run release:stage -- module tower-assistant
```

App 输出到 `dist/releases/app/0.12.0/`，模块输出到 `dist/releases/modules/tower-assistant/0.1.7/`，包含版本清单与 `SHA256SUMS.txt`。暂存只操作本地：要求当前源码已提交且干净；App 构建收据 `dist/app-build.json` 必须对应当前提交、版本和实际文件摘要，模块内部 manifest 与独立 manifest 相同，来源必须是本仓库干净提交且属于当前开发历史。目录或同版本附件已经存在时拒绝覆盖。

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
