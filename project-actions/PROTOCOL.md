# Project Actions event protocol v1

新增按钮和改造 Shell 时，先读 [AI_GUIDE.md](AI_GUIDE.md) 中的配置说明与完整示例；本文用于查询事件和接口细节。

## 事件上报

任务环境变量：

| 变量 | 含义 |
|---|---|
| `PROJECT_ACTIONS_PROJECT_DIR` | 项目根目录的绝对路径 |
| `PROJECT_ACTIONS_RUN_ID` | 本次运行ID |
| `PROJECT_ACTIONS_RUN_DIR` | 本次运行的数据和产物目录 |
| `PROJECT_ACTIONS_EVENT_URL` | 本次任务事件接口的完整URL |
| `PROJECT_ACTIONS_EVENT_TOKEN` | 仅用于本次任务上报事件的凭据 |
| `PROJECT_ACTIONS_SHELL` | Bash 辅助函数文件路径 |
| `PROJECT_ACTIONS_CLI` | Python 上报工具路径 |
| `PROJECT_ACTIONS_PYTHON` | 当前 Python 可执行文件路径 |

向 `PROJECT_ACTIONS_EVENT_URL` 发送 `POST`：

```http
Authorization: Bearer <PROJECT_ACTIONS_EVENT_TOKEN>
Content-Type: application/json
```

单个请求上限64 KiB；成功返回 `{"sequence": 12}`。
内容校验错误返回400，凭据错误返回403，已结束任务拒绝新增事件并返回409。

### 日志

```json
{"type":"log","level":"info","message":"开始构建","nodeId":"build"}
```

`nodeId` 可省略。支持 `debug`、`info`、`warning`、`error`。

### 节点

```json
{"type":"node","id":"build","label":"构建","state":"running","dependsOn":["check"],"message":"编译资源"}
```

已有节点只需上报 `id` 和 `state`；省略的名称、依赖关系保持原值。
未声明节点会动态创建。尚未上报的前置节点会作为待执行节点加入图中。
不允许环形依赖。依赖只用于展示，不会调度脚本执行。

### 进度

```json
{"type":"progress","id":"build","completed":3,"total":10,"message":"正在处理第3项"}
```

`0 <= completed <= total` 且 `total > 0`；数量可为小数，界面换算为百分比。
进度不改变节点状态；开始和结束仍通过节点事件上报。

### 任务产物

```json
{"type":"artifact","path":".project-actions/runs/<run-id>/report.txt","label":"检查报告"}
```

路径可为项目内绝对路径，或相对项目根目录的路径。文件必须存在，符号链接解析后仍须位于项目中。

## 浏览器／其他界面接口

本机服务仅监听 `127.0.0.1`。界面请求使用当前页面 meta 标记中的会话令牌：

```http
Authorization: Bearer <interface-token>
```

该令牌与脚本上报凭据不同。接口不接受请求中任意指定的命令，只接受配置中的动作ID。

| 接口 | 含义 |
|---|---|
| `GET /api/project` | 当前项目、按钮配置和最近100次任务记录 |
| `POST /api/runs` | 请求体 `{"actionId":"deploy"}`，202返回任务快照 |
| `GET /api/runs/<id>` | 任务快照：整体状态、节点、产物、退出码 |
| `GET /api/runs/<id>/events?after=12` | 从序号12之后接收 SSE 事件 |
| `POST /api/runs/<id>/cancel` | 停止任务 |
| `GET /api/runs/<id>/artifacts/<artifact-id>` | 下载已注册产物 |

SSE 使用 `fetch` 流读取，以便携带 Authorization 标头。

```text
id: 13
event: update
data: {"type":"node","runId":"...","sequence":13,"timestamp":"...","node":{"id":"build","state":"running","label":"构建","dependsOn":["check"],"progress":null}}

```

输出事件类型为 `run`、`log`、`node`、`artifact`。`progress` 输入转换为带百分比的 `node` 输出事件。
动态节点输出附带 `nodes` 数组以更新完整图结构。
终态任务在发送完事件后关闭连接；客户端应读取一次最终快照，以取得服务恢复时的中断状态。
如果游标早于内存保留的2000个事件，会先发送 `event: snapshot`，再发送可用的最近事件。

整体状态：`queued`、`running`、`succeeded`、`failed`、`cancelled`、`timed_out`、`interrupted`。
`run.json` 和 `events.jsonl` 都位于项目的 `.project-actions/runs/<id>/` 中。
