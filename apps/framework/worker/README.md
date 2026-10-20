# Python worker

Python 3.11+，首版仅依赖标准库。worker 是由桌面宿主管理的子进程，只使用标准输入/输出，不监听网络。框架没有任何应用、AI 或游戏业务依赖。

## 开发运行

在工程根目录执行：

```powershell
Push-Location apps/framework/worker
python -m nimdadesk_worker
Pop-Location
```

运行后可输入单行请求。宿主应为每个请求生成唯一 ID；worker 顺序处理请求，并在每条响应后立即刷新 stdout。日志只写 stderr，不记录原始请求或异常文本。

```json
{"protocolVersion":1,"id":"hello-1","method":"runtime.hello","params":{}}
{"protocolVersion":1,"id":"ping-1","method":"runtime.ping","params":{}}
{"protocolVersion":1,"id":"stop-1","method":"runtime.shutdown","params":{}}
```

运行契约测试：

```powershell
python -m unittest discover -s tests/worker -v
```

## 协议 v1

- 传输格式为 UTF-8 JSONL，一行一个对象，接受 LF 或 CRLF；EOF 前最后一行可不带换行。
- 每个请求帧最多 65,536 字节，不计行尾 LF/CRLF；超长行使用有界缓冲丢弃至下一行，返回一次 `FRAME_TOO_LARGE`，随后继续处理。
- 请求必须且只能包含 `protocolVersion`、`id`、`method`、`params`。版本必须是整数 `1`，布尔值不接受。
- `id`、`method` 必须为 1–128 字符的非空白字符串，不能含控制字符或 UTF-16 孤立代理码位；`params` 必须是对象。当前 runtime 方法只接受空对象。
- JSON 重复键、NaN/Infinity、无效 UTF-8 均拒绝。
- 响应为 `{protocolVersion:1,id,result}` 或 `{protocolVersion:1,id,error:{code,message}}`。无法可靠读取合法 ID 的输入错误使用 `id:""`；宿主应将它视为协议诊断，不关联任意请求。
- 未知方法返回 `METHOD_NOT_FOUND`，不会导入模块、执行命令或解释字符串。处理错误后继续处理下一请求。
- EOF 正常退出；`runtime.shutdown` 先发送成功响应，然后退出并忽略后续排队输入。

| 方法 | 返回结果 |
| --- | --- |
| `runtime.hello` | `{protocolVersion:1,worker:"nimdadesk-python",capabilities:[...],maxFrameBytes:65536}` |
| `runtime.ping` | `{message:"pong",protocolVersion:1,pid:number,pythonVersion:string}` |
| `runtime.shutdown` | `{status:"stopping"}` |

错误码包括 `INVALID_JSON`、`INVALID_REQUEST`、`UNSUPPORTED_PROTOCOL`、`INVALID_PARAMS`、`METHOD_NOT_FOUND`、`FRAME_TOO_LARGE`、`INTERNAL_ERROR`。错误消息为固定诊断文字，不回显用户参数。

## 组件边界与后续扩展

- `protocol.py` 只处理帧、验证和编码。
- `registry.py` 只提供显式的处理器注册与分发，不查找、扫描或导入业务模块。
- `runtime.py` 只注册框架自己的生命周期方法。
- `__main__.py` 是首版组合入口，负责连接标准流、注册框架方法和处理退出。
- 新增功能时由应用组合层把经过选择的处理器注入注册器，功能实现保留在各自模块；不在框架中新增对具体功能的直接 import。
- 处理器必须显式校验自己的参数，并返回可序列化的 `MethodResult`。日志不得包含凭据或完整上下文。
- 首版顺序请求/响应只验证通信基础；不承诺后台任务、流式任务事件、取消或进程重启后的任务恢复。增加这些能力时先定义协议和宿主状态机，并添加对应契约测试。

## 打包入口

`freeze_entry.py` 是 PyInstaller 的显式入口。打包由工程脚本负责，推荐目录包，并保证打包后的标准输入/输出仍可用。不要使用会移除标准流的 windowed worker；宿主负责隐藏子进程窗口。

当前 worker 不依赖第三方包，也不扫描或修改用户配置和数据。冻结打包、安装器与桌面实际启动各自需要单独验证，协议测试通过不代表这些环节已经验证。
