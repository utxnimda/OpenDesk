# OpenDesk 发布手册

本手册描述可重复执行的公开交付流程，命令块供后续发布参考，不应原样重复创建已发布版本。实际发布版本、下载地址及校验结果分别记录在 [下载索引](downloads.md) 和 [验证记录](verification.md)。版本规则见 [版本维护](versioning.md)，包格式见 [模块包契约](module-packages.md)。

## 2026-10-26 发布结果：App 0.18.0 / 塔防 0.8.2

用户已授权审阅后的源码提交、推送，以及 App 与塔防页签两个 Release。本次从干净提交 `036ba0ace3583cbff410a0ae1155b46a44386e13` 构建 [App 0.18.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.18.0) 与[塔防助手 0.8.2](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.8.2)，源码已推送；标签 `v0.18.0` 与 `modules/tower-assistant/v0.8.2` 的远端内容和本地一致。2026-10-26 已公开发布，App 6 个附件与模块 4 个附件均已重新下载并核对 SHA-256。 历史 App 0.17.0 与塔防 0.1.9 继续保留，下方旧发布结果和旧版本命令保持历史含义，不原样重跑。

- **版本与来源**：0.8.2 与已验证本地 0.8.1 功能一致，新版本独立完成干净构建与验证；0.8.1 dirty 包、缓存与验证记录保持原样，不手改来源，也不覆盖同版本摘要。构建标签固定在上述提交，后续结果文档提交不移动标签。
- **交付范围**：App 安装器和便携包只随附塔防 0.8.2；独立塔防 Release 使用字节一致的同一包，SHA-256 为 `6ac145c18c7caf5de865894a50b50866322ef544bfb935f55d171c79ac3a2bad`。App 6 个附件、模块 4 个附件来自各自审核暂存目录；排除模块测试台、fixture、作品管理器附件、外部导入包、私人模块、游戏包、存档、备份、采集流及全部本机状态。作品管理器 0.3.1 的既有独立 Release 不变。
- **检查与构建**：`npm run check` 通过（492 项通过、1 项跳过），Rust 66 项通过；主线与两个发布标签的 Check CI 均成功。App、独立页签、核心/模块原生 smoke、冻结 worker 35 次 RPC，以及实际便携包和 NSIS 载荷审计均通过。详情和证据边界见[验证记录](verification.md)。生成物只留在 Git 忽略目录和 Release 附件，不进入源码历史。
- **推送与发布方式**：源码通过 `git push` 推送；两个附注标签因 Git 端点连接失败改由 GitHub API 创建，远端标签对象 SHA 与本地一致。没有移动已发布标签或覆盖同版本产物。App 与塔防两个 Release 均已公开（非草稿、非预发布），App 保持最新入口。App 6 个附件和模块 4 个附件均已重新下载，大小及 SHA-256 与本地审核暂存一致；8 条校验清单、便携包内部 62 个文件、模块内部 76 个条目均通过，内外 manifest 及随附/独立包一致。证据：`.local/release-20261026/remote-verification-018-082-1792955454300606700.json`；下载来源另由 `.local/release-20261026/remote-app-metadata.json` 与 `remote-module-metadata.json` 记录。
- **本机边界**：本轮没有安装/卸载安装器、修改个人选择或游戏与存档，没有执行 SVN 提交。原生第二视图的生命周期通过不代表真实游戏的透明视觉效果或长时间性能已验收。

## 2026-10-25 发布结果

用户本轮明确授权提交 Git/SVN 并执行打包发布。已从干净提交 `95e1107761710d7f2b61cb575a802c6507831b66` 交付 [OpenDesk 0.17.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.17.0) 与[作品管理器 nimda-workspace 0.3.1](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Fnimda-workspace%2Fv0.3.1)；main 与标签 `v0.17.0`、`modules/nimda-workspace/v0.3.1` 已推送，三项 Check CI 均成功。App 标为最新，独立页签不覆盖 App 的最新入口。

App 仍只随附已公开且字节不变的塔防 0.1.9，保留其原来源提交；作品管理器 0.3.0 本地开发原包保持不变，0.3.1 从本次干净源码构建。真实资料迁移已在 SVN r196 提交，本轮复查工作副本没有新变更，没有创建空提交。

本轮 App 6 个附件、作品管理器 4 个附件均已上传、重新下载并逐项核对 SHA-256。公开作品管理器附件为标准 .ndpkg、manifest、发布清单和校验和；带本机工作区启动配置的 MediaManager.exe ZIP、真实资料、部署工作台和 fixture 不在公开附件中。

App 构建前核对现有塔防包与公开原包摘要相同，通过本地临时 Tauri 配置只运行 build:web / build:worker，之后执行标准便携打包、App 收据与 release:stage 校验；没有重写模块来源或以新摘要覆盖旧版本。构建及远端核验结果见[验证记录](verification.md)，直接链接见[下载索引](downloads.md)。发布结果文档作为后续提交，不移动实际构建标签。

## 1. 当次目标与授权

每次先确定 App 版本、更新页签的 ID/版本、随附范围、源码提交以及 Git/远端操作授权。历史授权不自动覆盖下次发布。

2026-10-23，用户在现有改动已完成本地提交后，明确要求先完善发布文档，再执行发布，并再次排除本机导入的“部署工作台”。本轮已交付 **[App 0.15.0](https://github.com/utxnimda/OpenDesk/releases/tag/v0.15.0) + [tower-assistant 0.1.9](https://github.com/utxnimda/OpenDesk/releases/tag/modules%2Ftower-assistant%2Fv0.1.9)**；范围包括已审阅的源码、处理后运行资源、App 安装器/便携包，以及该页签的独立包。文档和正式页签版本先提交，产物从干净提交 `aa7bdd7346fc1544bebff939b79ed40886c215a2` 构建；App 6 个附件与页签 4 个附件均已下载复核。部署工作台等本机导入内容未发布。下文的版本号是本轮示例，后续必须替换为当次已确认的新版本，不覆盖这次交付。

当前默认平台为 Windows x64。Git 源码提交、标签推送、二进制打包、Release 发布和远端下载核对分别记录；只有完成对应操作才报告成功。本轮授权允许依次完成这些步骤，不需要对已经明确授权的常规步骤重复询问。

## 2. 公开范围以白名单为准

`config/distribution.json` 是正式分发权威：`bundledModules` 表示 App 随附页签，`publicModules` 表示可独立公开页签，前者必须为后者子集。当前 `bundledModules` 只有 `tower-assistant`，`publicModules` 另含作品管理器 `nimda-workspace`。实际文件必须与该清单相符，不能只检查 manifest 中声明的名称。

| 内容 | Git 源码 | App Release | 独立页签 Release |
|---|---|---|---|
| 宿主、正式模块代码、锁文件、测试、文档 | 审阅后提交 | 由版本标签追溯 | 由模块标签追溯 |
| 宠物运行 WebM/PNG、来源记录、制作工具与作者种子/轮廓 | 审阅后提交 | 仅前端引用的运行媒体 | 不混入业务包 |
| 正式模块的前端、worker、运行资源 | 提交源文件 | 白名单中的 `.ndpkg` | 按模块 ID/版本发布 `.ndpkg` |
| 安装器、便携 ZIP、冻结程序、`.ndpkg` | 不写入 Git | 审核后的明确附件 | 审核后的明确附件 |
| 部署工作台、其他私有模块/外部导入包 | 排除 | 排除 | 排除 |
| 本机 `imports`、`installed`、个人选择、真实配置、日志、存档及导出数据 | 排除 | 排除 | 排除 |
| 模块测试台、其他 fixture 的二进制 | 排除 | 排除 | 排除 |
| 原始参考视频、完整动画母版、私人图片、缓存、虚拟环境 | 排除 | 排除 | 排除 |

模块测试台的**源码**属于本仓库开发测试代码，可以保留；它的二进制没有公开分发资格。部署工作台的源码、真实环境配置和包均不属于本仓库的公开交付范围。

构建入口、便携复制和发布暂存均不得从用户数据目录扫描或收集包。不得因为“用户已导入”“在本机可运行”或“未发现密码”而扩大上传范围。来源记录不等于第三方许可证，不编造未提供的许可声明。

## 3. 发布前检查与版本准备

在工程根目录使用 PowerShell，先阅读 `AGENTS.md`、README 当前状态及任务相关规则，再执行：

```powershell
git status --short --branch
git diff --stat
git diff --cached --stat
git remote -v
Get-Content config/distribution.json
npm run doctor
```

核对远端确为 `utxnimda/OpenDesk`，查看待提交路径及内容，保留用户无关改动。使用 `git ls-files` 审查实际受控文件；对 `.ndpkg`/ZIP 查看全部 entry 和关键 manifest；检查冻结 worker 的构建入口及打包输入。关键词查找只能辅助发现可疑内容，不能代替清单、依赖和包内容审查。不要将包含私有模块详情的本机审计原文作为公开附件。

版本按以下规则准备：

- 宿主/core 变化使用 `npm run version:prepare -- --file .local/next-release.json`，并执行 `npm run version:check`；不只手改 package/Tauri/Cargo 版本。
- 独立页签在自己的 manifest 升版本，hostApi 和 dataSchema 单独判断兼容性；仅更新页签不强制升宿主版本。
- 已经安装、使用或发布过的同 ID/版本包，不以不同摘要覆盖。
- 本轮实际保留 App 0.15.0，塔防从本地开发 0.1.8 升到当时尚未占用的 0.1.9；旧 0.1.8 和公开 0.1.7 保留。0.15.0 / 0.1.9 现已发布，不能再次分配给不同产物。

**不能提交后直接把旧 dirty 包当成正式包。**模块缓存根据内容指纹复用，Git 来源本身不是输入指纹；提交源码后仍可能拿到原来的 dirty 包。应提升已使用过的页签版本，在干净提交上构建新产物。不得修改包内 `source`、重写摘要或删除来源信息来伪装干净构建。

先运行相关检查，审阅并提交本轮文档、版本和必要源码，提交后再次确认工作区干净。`git add` 使用实际审阅过的路径，不因为发布而顺带提交无关内容。之后记录完整源码身份：

```powershell
$releaseCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot read source commit.' }
if (git status --porcelain) { throw 'Commit reviewed changes before the release build.' }
```

不要在构建与暂存之间再编辑或提交文档。App 构建收据绑定当前提交；若源码提交改变，应按新提交重建并重新核验。已经构建、暂存并核验完成后，发布结果文档可以另作后续提交，标签仍指向实际构建来源。

## 4. 保护运行实例与旧产物

`npm run build` 的便携输出固定为 `dist/OpenDesk/`，还会写入当前版本的 NSIS 安装器、`dist/bundled-modules/` 和 `dist/app-build.json`。开始前检查这些位置是否存在旧包或被正在运行的实例占用。

- 记录需保护的旧包清单、版本、摘要和正在运行的本应用进程路径/PID/启动时间。
- 若需让出固定便携输出，先通过正常方式退出已核实的本应用实例，再把旧输出保留到本地带时间戳的备份目录。不得仅凭进程名批量结束进程，也不停止 IDE、游戏或其他应用。
- 移动前解析并检查源、目标绝对路径都在本工程预定目录内；使用 PowerShell `Move-Item -LiteralPath`，不使用跨 shell 拼接的删除/移动命令。不要递归清理未知内容。
- 若 `dist/bundled-modules` 含旧版本额外包，先审计并移出保留。构建器拒绝额外文件是正常保护，不能放宽检查让旧包混入。
- 保留不可变模块缓存和旧版本包，不移动或清理用户数据根中的 `imports`、`installed`、设置或部署工作台。

最终发布暂存使用独立版本目录 `dist/releases/...`，不用正在运行的目录直接上传。`release:stage` 拒绝覆盖已有版本目录；遇到已有内容先核验其来源和摘要，不能为重试而直接删除。

## 5. 从干净提交构建与验证

先按 README 配置锁定的 Node、Python、Rust、MSVC/Windows SDK 和 WebView2 环境。以下命令依次执行，并在非零退出码时停止：

```powershell
npm run check
if ($LASTEXITCODE -ne 0) { throw 'Source checks failed.' }
npm run desktop:test
if ($LASTEXITCODE -ne 0) { throw 'Rust tests failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'App build failed.' }
npm run build:module -- tower-assistant
if ($LASTEXITCODE -ne 0) { throw 'Standalone module build failed.' }
```

首次 Rust 检查若缺少引用的 worker/模块资源，先执行 README 要求的 `build:worker`、`build:modules`。`npm run build` 负责前端、worker、Tauri、NSIS、便携复制和 App 构建收据；不执行 Git 写入或发布。

构建后至少核对：

1. `dist/app-build.json` 的版本、完整 commit、`dirty:false` 与本次干净提交一致，安装器及便携文件摘要匹配。
2. App 随附清单只含当次授权的正式页签；目录中没有额外 `.ndpkg`、私有配置或运行数据。
3. 独立包内外 manifest 相同，来源为本仓库干净开发历史，ID/版本/hostApi/dataSchema/target 正确。
4. App 随附、独立包以及后续暂存的同一 `.ndpkg` 必须 SHA-256 相同。
5. 运行资源齐全，阿宝/阿沐运行媒体随 App；原视频、完整母版及作者处理依赖没有混进运行包。

核心桌面检查示例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke.ps1 -Executable .\dist\OpenDesk\OpenDesk.exe
if ($LASTEXITCODE -ne 0) { throw 'Native core smoke failed.' }
```

通过单独 `powershell.exe -File` 运行后，`$LASTEXITCODE` 对应这个进程的退出码。在同一 PowerShell 中直接调用脚本后读取该变量，可能读到脚本内部或此前命令遗留的值；应同时核对实际 smoke 结果文件，避免把成功测试误报为失败。

涉及模块导入/选择时使用 `module-smoke.ps1`，其 fixture 另由 `build:module-fixtures` 生成，仅保留在本机测试目录；涉及宠物时按 README 使用独立数据根执行 companion smoke。不要把测试包复制到公开随附目录。记录实际结果文件、退出码、使用的 EXE 摘要及本次进程退出情况。准备隔离测试设置 JSON 时写入 **UTF-8 无 BOM**：Windows PowerShell 5.1 的 `Set-Content -Encoding utf8` 会加入 BOM，可能被严格 JSON 读取拒绝；可使用 `[System.IO.File]::WriteAllText($testSettingsPath, $testSettingsJson, (New-Object System.Text.UTF8Encoding($false)))`，只写当次独立测试根，不修改真实用户设置。遇到此问题修正本地测试输入后重新运行，不放宽生产读取校验。

静态检查、Rust 测试、构建、原生 smoke、浏览器视觉检查、原生人工交互、安装/卸载、签名、其他机器兼容性和长时运行是不同证据。未执行的项目明确写“未执行”。生成 NSIS 不代表已安装或已签名；SHA-256 只证明字节身份，不代替发布者签名。

## 6. 生成隔离的发布附件

```powershell
npm run release:stage -- app
if ($LASTEXITCODE -ne 0) { throw 'App staging failed.' }
npm run release:stage -- module tower-assistant
if ($LASTEXITCODE -ne 0) { throw 'Module staging failed.' }
```

本轮已审核发布的目录与**完整上传名单**（后续发布替换版本）：

```text
dist/releases/app/0.15.0/
  OpenDesk_0.15.0_x64-setup.exe
  OpenDesk_0.15.0_windows-x64_portable.zip
  app-manifest.json
  tower-assistant-0.1.9.ndpkg
  release-manifest.json
  SHA256SUMS.txt

dist/releases/modules/tower-assistant/0.1.9/
  tower-assistant-0.1.9.ndpkg
  manifest.json
  release-manifest.json
  SHA256SUMS.txt
```

暂存器检查干净源码、App 收据、实际文件清单、ZIP 内文件摘要、页签来源和白名单；完成后人工复核上述名单。`release-manifest.json` 记录产物身份，`SHA256SUMS.txt` 覆盖附件及 release manifest，但不自引用自身。不得上传整个 `dist`、整仓库目录、本机数据目录或任意通配匹配到的其他包。

## 7. 推送源码、标签和明确附件

先检查 GitHub 登录状态、目标仓库和现有标签/Release。已有同标签时，先读取远端目标 commit、附件名称和摘要，按第 9 节处理；不强制移动标签，不删除旧 Release。

```powershell
$releaseRepo = 'utxnimda/OpenDesk'
$appTag = 'v0.15.0'
$moduleTag = 'modules/tower-assistant/v0.1.9'
gh auth status
git ls-remote --tags origin "refs/tags/$appTag" "refs/tags/$appTag^{}" "refs/tags/$moduleTag" "refs/tags/$moduleTag^{}"
gh release list --repo $releaseRepo --limit 30
```

下例仅用于确认两个标签都不存在、`main` 仍指向 `$releaseCommit` 且所有本地审计通过的情况。`version:tags` 只打印建议，不会执行这些操作：

```powershell
git tag -a $appTag $releaseCommit -m 'OpenDesk 0.15.0'
if ($LASTEXITCODE -ne 0) { throw 'App tag failed.' }
git tag -a $moduleTag $releaseCommit -m 'tower-assistant 0.1.9'
if ($LASTEXITCODE -ne 0) { throw 'Module tag failed.' }
git push --atomic origin main "refs/tags/$appTag" "refs/tags/$moduleTag"
if ($LASTEXITCODE -ne 0) { throw 'Source and tag push failed; inspect remote state.' }
git ls-remote origin refs/heads/main "refs/tags/$appTag^{}" "refs/tags/$moduleTag^{}"
```

核对远端分支和注解标签解析后的 commit 与预期一致。查看对应 GitHub Actions 运行；当前 `.github/workflows/check.yml` 只有读取权限，只执行检查和前端构建，**不会自动制作安装器、创建 Release 或上传附件**。CI 的成功/失败状态单独记录，不能用本地通过冒充远端通过。

将用户可读的 App 和页签说明分别写入 `.local/release-app-0.15.0.md`、`.local/release-tower-0.1.9.md`，内容包含实际改动、兼容性、随附版本、校验方法及验证限制。使用 UTF-8 文本和 `--notes-file` 保留真正换行，不把正文插值拼进 shell 命令。

```powershell
$appDir = 'dist/releases/app/0.15.0'
$moduleDir = 'dist/releases/modules/tower-assistant/0.1.9'
$appFiles = @(
  "$appDir/OpenDesk_0.15.0_x64-setup.exe",
  "$appDir/OpenDesk_0.15.0_windows-x64_portable.zip",
  "$appDir/app-manifest.json",
  "$appDir/tower-assistant-0.1.9.ndpkg",
  "$appDir/release-manifest.json",
  "$appDir/SHA256SUMS.txt"
)
$moduleFiles = @(
  "$moduleDir/tower-assistant-0.1.9.ndpkg",
  "$moduleDir/manifest.json",
  "$moduleDir/release-manifest.json",
  "$moduleDir/SHA256SUMS.txt"
)
gh release create $moduleTag @moduleFiles --repo $releaseRepo --verify-tag --title '塔防山贼助手 0.1.9' --notes-file .local/release-tower-0.1.9.md --latest=false
if ($LASTEXITCODE -ne 0) { throw 'Module release outcome requires inspection.' }
gh release create $appTag @appFiles --repo $releaseRepo --verify-tag --title 'OpenDesk 0.15.0' --notes-file .local/release-app-0.15.0.md --latest=true
if ($LASTEXITCODE -ne 0) { throw 'App release outcome requires inspection.' }
```

独立页签 `latest=false`，避免盖过 App 的最新下载入口；本轮 App 使用 `latest=true`。`--verify-tag` 防止 CLI 为不存在的远端标签隐式创建源码身份。不要添加 `--clobber` 覆盖既有附件。

## 8. 下载远端附件核对，再更新索引

发布后读取两个 Release 的 URL、标签、draft/prerelease 状态与完整附件列表，确认没有多余附件，也没有漏项。下载到全新的本地核验目录；不把本地暂存文件当作已下载远端的证据。

```powershell
gh release view $appTag --repo $releaseRepo --json url,tagName,isDraft,isPrerelease,assets
gh release view $moduleTag --repo $releaseRepo --json url,tagName,isDraft,isPrerelease,assets
$verifyRoot = Join-Path '.local/release-downloads' (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
$verifyApp = Join-Path $verifyRoot 'app'
$verifyModule = Join-Path $verifyRoot 'module'
foreach ($file in $appFiles) {
  $name = Split-Path -Leaf $file
  gh release download $appTag --repo $releaseRepo --pattern $name --dir $verifyApp
  if ($LASTEXITCODE -ne 0) { throw "App download failed: $name" }
  if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne
      (Get-FileHash -LiteralPath (Join-Path $verifyApp $name) -Algorithm SHA256).Hash) {
    throw "Remote App digest mismatch: $name"
  }
}
foreach ($file in $moduleFiles) {
  $name = Split-Path -Leaf $file
  gh release download $moduleTag --repo $releaseRepo --pattern $name --dir $verifyModule
  if ($LASTEXITCODE -ne 0) { throw "Module download failed: $name" }
  if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne
      (Get-FileHash -LiteralPath (Join-Path $verifyModule $name) -Algorithm SHA256).Hash) {
    throw "Remote module digest mismatch: $name"
  }
}
```

同时核对下载的 `SHA256SUMS.txt` 所列各文件，以及两个 Release 中 `.ndpkg` 的摘要相同；检查便携 ZIP 和 `.ndpkg` 内部清单，确认公开内容仍符合白名单。远端核验成功之后，才在 `docs/downloads.md` 增加版本及直接下载链接，在 `docs/verification.md` 记录 commit、标签、附件数量/摘要、CI、测试与未验证项，并更新 README 当前状态。保留旧版本记录。

发布后文档可使用单独的 `docs:` 提交并推送。本次发布标签继续指向实际构建提交，不因更新下载记录而移动；不要把后续文档提交伪装成原包的构建来源。

## 9. 失败、重试与最终报告

- **工作树 dirty / 收据过期**：停止暂存，审阅并提交实际改动，再重新构建；不修改收据来跳过检查。
- **同版本不同摘要**：保留旧产物与错误，确认是否需要提升版本；不覆盖已用或已发布包。
- **本地暂存目录已存在**：核对现有 manifest、源码和摘要。可直接使用已验证的同一套附件；若是未完成暂存，先保存检查结果再将整个目录移到本地备份，重新暂存。不要删除未知文件。
- **上传超时 / 结果未知**：先用 `gh release view` 或 GitHub API 检查远端 Release、标签和附件；不要默认失败后重新创建。只有明确缺失的附件可按原审核名单补传，且已有同名附件必须先下载比较，不使用覆盖参数。
- **远端摘要不一致或混入私有内容**：停止继续公开后续产物，报告具体异常；不得在未查明原因前给出成功下载链接或静默替换同版本文件。
- **CI 或 smoke 失败**：记录实际失败，修复后重新走必要检查；不能把“构建完成”当作“所有验证通过”。

最终报告至少给出 App/独立页签链接、源码提交和标签、随附版本、部署工作台等排除内容的实际审计结果、远端附件复核结论、已执行/未执行验证，以及 Git 提交、推送、Release 的各自状态。发布失败或部分完成时准确说明当前状态和剩余步骤。
