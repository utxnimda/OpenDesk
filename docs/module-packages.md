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

宿主的「设置 → 外观」以带微缩界面预览的主题卡片展示配色，选中即时预览，保存后持久化。新外观不会修改模块包版本选择或业务数据。

## 构建

先按README配置Python venv和锁定依赖。构建不联网安装、不提交、不打标签、不上传：

```powershell
node scripts/module-pack.mjs bundled
node scripts/module-pack.mjs fixtures
```

bundled输出 `dist/bundled-modules/app-manifest.json` 和：

| ID | 版本 | 用途 |
|---|---|---|
| tower-assistant | 0.1.3 | 自带塔防助手，使用 OpenDesk 命名并同步樱花/海盐蓝/松石绿/暖砂岩/石墨夜外观，不修改算法 |
| test-lab | 1.0.3 | 自带测试页签，使用 OpenDesk 命名并同步上述外观，marker=v1-baseline |

OpenDesk 命名变更涉及包内源码与通信标识，因此以新包版本交付，不覆盖旧版摘要。宿主升级不自动改变当前应用内用户固定的版本；旧应用数据及包选择不自动迁入 OpenDesk。当前 fixture 的版本切换基线为自带测试台 1.0.3，测试程序从实际索引读取基线版本。既有版本历史和验证记录保持原始事实。

fixtures输出 `dist/module-fixtures/fixtures.json` 和：

| 包文件 | 身份 | 预期 |
|---|---|---|
| test-lab-1.1.0.ndpkg | 同ID，新版本，marker=v1.1-enhanced | 导入更新，前端/worker真实变化 |
| test-lab-2.0.0-incompatible.ndpkg | hostApi=99 | 保留原件并在执行代码前拒绝 |
| test-extra-1.0.0.ndpkg | 新ID，marker=extra-module | 导入新增页签 |
| test-lab-1.2.0-ui-failure.ndpkg | API1/schema1，worker:null，合法页面不发送SDK hello | 真实页面就绪超时，保留lastError并恢复此前可用版本 |

这些是本地验证包，不是正式发布历史。API99包由API1测试代码构建，但故意声明不兼容要求，必须在加载前拒绝，不能把它当作可运行迁移方案。

1.2.0 fixture 只有固定 HTML 和 manifest，不经过 Vite/PyInstaller，不执行后台，也不向宿主发送 SDK hello。它验证真实浏览器加载后的协议就绪失败，不是手工模拟一个宿主错误。只需要补建此包时可运行 `node scripts/module-pack.mjs ui-failure-fixture`，现有 fixture 索引中的其他项会保留。

Vite独立构建使用base='./'并烘焙 __MODULE_ID__/VERSION/MARKER。PyInstaller目录包包含运行依赖；构建器直接运行冻结worker验证身份、回显、非法参数与退出，然后压缩。

## 来源与重复构建

默认Git来源为当前工程；构建时可设置绝对 OPENDESK_SOURCE_ROOT 读取正式工程基线。跨暂存目录一律保守dirty，不能把基线commit冒充全部构建代码。环境变量只参与构建。

ZIP文件按路径排序并固定时间，PyInstaller使用固定SOURCE_DATE_EPOCH和PYTHONHASHSEED。构建缓存按模块源码、SDK、通用worker、样式、规范化依赖锁、打包脚本及实际工具版本生成指纹；未变化则校验并复用原包及原source身份。宿主版本字段从依赖指纹剔除，避免纯宿主版本升级改变未更新的模块包。

缓存复用保证同一已保存产物不因重复构建而漂移；尚不承诺清空缓存或跨机器重建逐字节一致。正式分发应保留已确认的不可变模块包，不能静默放宽同版本异摘要冲突。若构建内容确有改变，应升级包版本。

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
