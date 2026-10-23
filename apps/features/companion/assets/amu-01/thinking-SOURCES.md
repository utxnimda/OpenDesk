# 阿沐：透明前景托腮思考

本动作来自用户提供的 `生成指定动作的循环视频 (4).mp4`，源 SHA-256 为 `6b22da5c7cbda5ff08f75f6c0b45a804c32d313335178cbdd6d30d014461be48`。用户于 2026-10-23 明确要求背景透明：保留原视频里的阿沐、电脑、键盘、桌面/支撑和座椅，去掉后方房间墙面及物体间可见的墙面空隙。仍接受原片的半身构图，不生成腿脚或缺失身体。

此前保留整张房间画面的 MP4 和静态图已撤出生产资源并归档到本机 `.local/`。本目录现在使用 `thinking.webm` 和透明 `thinking-still.png`；原骑行 `loop.png` / `still.png` 以及阿宝资源保持原摘要。

## 原帧与透明度处理

完整保留全部 145 帧及原 24 FPS 动作顺序，源时长为 145/24 秒，前端标称时长 6042ms。生成工具仅提供第一帧的灰度 alpha 轮廓参考；没有使用该参考图的 RGB，也没有 AI 重画、插帧或重设计角色。

逐帧使用小位移光流传播、原图 GrabCut 校正、真实固定道具轮廓内核和 14 个稳定背景采样点；移动头发用当前原图的可靠颜色约束，不将错误传播的 mask 当作绝对前景。桌下蓝墙和头顶与外部连通的残墙按原像素颜色及已核实区域清理，保护蓝色衣料、真实发带和深色描边。原分辨率 RGBA 的 RGB 与对应视频帧逐字节相同，只修改 alpha。

全部帧采用共同前景包围盒 `[0, 167, 720, 1280]`，统一缩放为 453×700，置于 768×768 画布的 `(157, 34)`，固定底线 734；没有逐帧重居中。裁掉的上方空白已经透明，不丢弃前景。

## 运行时与母版

- `thinking.webm`：768×768 VP9 alpha 视频，145 帧，名义 24 FPS，实际容器时长 6.042 秒。保持无音轨，应用单次播放并以 `ended` 完成。
- `thinking-still.png`：同一透明前景第一帧的静态图。
- 本机 `dist/companion-assets/amu-01/thinking/thinking.png`：无损 RGBA APNG 母版，145 帧，每帧精确 1/24 秒，播放一次。大型母版不放入 App 运行时。

APNG 通过全部 145 帧的 Pillow 逐像素解码核对。WebM 使用 `libvpx-vp9`、`yuva420p`、`lossless=1`、`auto-alt-ref=0`，并强制用 `libvpx-vp9` 解码验证；不能只依赖 `alpha_mode` 标签。全部 145 帧中，原 alpha 0 与 255 完全保持，软边 alpha 最大差 1/255，8 位 alpha 全画布平均绝对差约 0.00536。WebM 的 YUV420 转换及色度采样会改变 RGB，不能称为 RGB 逐像素无损；alpha 加权 RGB 平均绝对差约 1.558/255，实心像素约 1.542/255，低 alpha 边缘的单通道最大差为 235。WebM 时间基为 1/1000，帧时间戳最大量化差约 0.333ms，帧序与帧数不变。

## 可复现作者工具

```powershell
python scripts/prepare-companion-foreground.py --input <source-video> --output <new-output-directory> --alpha-seed apps/features/companion/assets/amu-01/authoring/thinking-alpha-seed.png --outline-config apps/features/companion/assets/amu-01/authoring/thinking-outline.json --export-webm
```

依赖见 `packaging/requirements-companion-assets.txt`；隔离作者环境可额外指定 `--opencv-dir <authoring-dependency-directory>`，FFmpeg/FFprobe 可通过对应参数指定。配置绑定源视频 SHA、尺寸、145 帧/24 FPS 和 alpha 种子摘要，避免误套其他视频。原视频不进入 Git 或安装包，灰度种子和轮廓配置只用于作者工具，不作为运行时画面。

本次正式工具迁移已核对第 1、73、145 帧的原生 RGBA 与 raw propagation mask，逐像素匹配已完成的完整候选序列；算法函数的 AST 机械等价，6 类无效输入检查通过。没有为迁移再次重跑整段 145 帧正式 CLI，也不冒称已做该项验证。真实源码、媒体、构建和桌面运行结果分别记录。

最初制作阶段仅获本地纠错、打包与启动授权。用户随后于 2026-10-23 要求提交现有改动与相关资源，本动作的运行媒体、来源记录及作者输入纳入该次审阅后的 Git 提交范围；原视频和完整母版仍排除。本地提交、远端推送和 Release 分别记录，不把此前 2026-10-22 对旧素材的授权延伸为本次发布事实，也不附加未提供的第三方许可证。

用户于同日进一步明确要求完善发布文档并执行发布流程。本次授权包含审阅后的源码和处理后运行资源、App 及正式页签包，明确排除本机导入的部署工作台；原视频和完整 APNG 母版仍不上传。实际发布结果以仓库下载索引与验证记录为准，不以授权记录代替发布成功证据。
