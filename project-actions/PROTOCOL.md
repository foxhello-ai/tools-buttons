# Project Actions protocol v1

新增／修改按钮先读 [AI_GUIDE.md](AI_GUIDE.md)。0.2.0 增加通用参数、检查点与节点续跑，
完整业务示例见 [PARAMETERS_AND_RESUME.md](PARAMETERS_AND_RESUME.md)。普通动作保持兼容。

## 任务事件

| 环境变量 | 含义 |
| --- | --- |
| `PROJECT_ACTIONS_PROJECT_DIR` | 项目根目录绝对路径 |
| `PROJECT_ACTIONS_PARAMETERS_FILE` | 用户确认参数的 JSON 文件路径 |
| `PROJECT_ACTIONS_CONTEXT_FILE` | mode / phase / plan / resume 等服务上下文的 JSON 文件路径 |
| `PROJECT_ACTIONS_RUN_ID` | 本次任务 ID |
| `PROJECT_ACTIONS_RUN_DIR` | 本次任务数据和产物目录 |
| `PROJECT_ACTIONS_EVENT_URL` | 本次任务的 report 接口完整 URL |
| `PROJECT_ACTIONS_EVENT_TOKEN` | 仅用于本次任务上报的凭据 |
| `PROJECT_ACTIONS_SHELL` | Bash 辅助函数文件路径 |
| `PROJECT_ACTIONS_CLI` / `PROJECT_ACTIONS_PYTHON` | 上报 CLI / 当前 Python 路径 |

options/preview 仅注入项目、参数文件、上下文文件及 Python 路径，不提供 run 目录或事件凭据。
这些命令约定只读；输出一个 JSON 对象，诊断写 stderr；默认超时 30 秒，最大 120 秒，输出合计最多 1 MiB。

向 `PROJECT_ACTIONS_EVENT_URL` POST：

```http
Authorization: Bearer <PROJECT_ACTIONS_EVENT_TOKEN>
Content-Type: application/json
```

每个请求是一个 JSON 对象，上限 64 KiB。成功返回 `{"sequence":12}`。
字段错误 400，凭据错误 403，已结束任务 409。普通 stdout/stderr 自动进入日志，stdout JSON 不自动当作事件。

### 日志、节点与进度

以下每行是一个独立请求：

```json
{"type":"log","level":"info","message":"开始构建","nodeId":"build"}
{"type":"node","id":"build","label":"构建","state":"running","dependsOn":["check"],"message":"编译资源"}
{"type":"progress","id":"build","completed":3,"total":10,"message":"正在处理第3项"}
```

日志级别 debug/info/warning/error，nodeId 可省略。
节点状态 pending/running/succeeded/failed/skipped/cancelled。名称和依赖省略时保留旧值。
未声明节点可动态创建，未知前置节点会加入为 pending；不得有环。图不调度执行。
进度满足 `0 <= completed <= total`、`total > 0`，支持有限小数；进度不改变状态。

### 产物下载

```json
{"type":"artifact","path":".project-actions/runs/<run-id>/report.txt","label":"检查报告"}
```

文件先生成再登记；绝对路径或相对**项目根目录**路径，符号链接解析后仍须位于项目内。

### 检查点

```json
{"type":"checkpoint","nodeId":"build","path":"build-checkpoint.json","label":"构建结果"}
```

Shell：`pa_checkpoint build "$PROJECT_ACTIONS_RUN_DIR/build-checkpoint.json" --label "构建结果"`。
路径相对于**本次 run 目录**，或者使用绝对路径；符号链接解析后仍须在本次 run 内。
最多 16 MiB，文件必须存在，nodeId 必须已经在图中。业务内容由脚本定义，应用不解析。
服务保存 id、nodeId、runId、path、sha256、size、label；再次登记同路径替换快照引用，保留事件历史。
预览、启动、复用事件时验证引用文件完整性；真实产物／远端状态由业务脚本额外验证。

### 复用节点

```json
{"type":"node","id":"build","state":"succeeded","execution":"reused","checkpointId":"已确认计划的检查点ID"}
```

Shell：`pa_reuse build "$checkpoint_id"`。只能引用服务确认计划中该节点的 reuse 检查点，
不能用节点 ID 代替检查点 ID。快照保存 execution:reused 和 reusedFrom:{runId,checkpointId}。
计划声明复用不会自动标绿。有计划的任务需明确报告所有 run/reuse 节点成功，否则即使命令返回 0 仍失败。

### 实际业务结果

```json
{"type":"result","data":{"source":{"directory":"/repo","commit":"完整提交"},"environment":"staging","artifactSha256":"实际产物摘要","releaseId":"实际发布ID"}}
```

`data` 是最多 32 KiB 的通用 JSON 对象；字段由业务定义，重复事件替换当前结果。
Shell 使用 `pa_event_json`；界面显示结果，最终 run.json 保留此数据。

## 界面／AI／自建 MCP 客户端接口

本机服务仅监听 127.0.0.1。URL 和界面 token 在 `.project-actions/.runtime.json`，权限 0600，
页面通过 meta 取得 token。不得提交或输出凭据。HTTP API 可供 AI 工具和自建 MCP 适配器使用；本项目没有内置 MCP server。

```http
Authorization: Bearer <interface-token>
```

界面 token 与单次任务上报 token 不同。所有接口只调用配置声明的命令，不接受请求指定任意 command/cwd。

| 方法与路径 | 请求／返回 |
| --- | --- |
| `GET /api/project` | 项目、按钮定义和最近 100 次任务；每个参数的 optionsCommand 尚未执行 |
| `GET /api/actions/<id>/options` | 解析静态／脚本选项，返回 `{optionsId,parameters}` |
| `POST /api/actions/<id>/preview` | `{parameters:{...},optionsId:"..."}`；返回 allowed、summary、plan、previewId、parameters、context |
| `POST /api/runs/<id>/resume-preview` | `{nodeId:"deploy"}`；使用原参数和服务解析的检查点，返回预览，不创建任务 |
| `POST /api/runs` | 普通动作 `{actionId:"directory"}`；参数／预览／续跑动作 `{actionId:"deploy",previewId:"..."}`；202 返回快照 |
| `GET /api/runs/<id>` | 指定任务快照，含原参数、计划、检查点、父／根任务、结果及节点 |
| `GET /api/runs/<id>/events?after=12` | 从序号 12 后接收 SSE |
| `POST /api/runs/<id>/cancel` | 停止本机进程组 |
| `GET /api/runs/<id>/artifacts/<artifact-id>` | 下载已登记产物 |

有参数时先调用 options（包括纯静态参数），preview 时带 optionsId；无参数预览可传 parameters:{}。
allowed:false 返回 reason，没有 previewId。成功预览凭据有效 600 秒，仅能消费一次；
绑定动作 JSON、参数、上下文、计划与检查点引用。取消只丢弃凭据，不创建任务。
错误：校验 400，鉴权 403，找不到 404，配置变化／过期／已消费／并发／不可续跑 409，
选项／预览超时 408，待确认缓存达 100 项 429。

可续跑父任务状态：failed/cancelled/timed_out/interrupted。成功或仍在运行的任务不可续跑。
节点需在动作 resume.nodes 和原任务节点中；动作 JSON 必须与原任务一致；需要真实检查点。
续跑创建新 ID，保存 parentRunId、rootRunId、resumeFromNode；不覆盖原任务。
连续续跑只继承确认要复用的检查点，不把将重新执行的下游旧结果带入新任务。

## SSE 与持久化

SSE 使用 fetch 流读取以携带 Authorization。每个块如下：

```text
id: 13
event: update
data: {"type":"node","runId":"...","sequence":13,"timestamp":"...","node":{"id":"build","state":"running","label":"构建","dependsOn":["check"],"progress":null}}

```

输出类型 run/log/node/artifact/checkpoint/result；progress 输入转成 node 输出。
checkpoint 输出含 checkpoint 对象；result 输出含 data。必要时服务发送 event:snapshot，数据为任务快照。
页面最多保留最近 2000 条日志，完整 events.jsonl 和最终 run.json 在每次 run 目录。
参数、context 分别写为 parameters.json、context.json。脚本的业务凭据不要写入这些快照或界面结果。
