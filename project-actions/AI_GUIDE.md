# Project Actions：AI 配置与脚本接入指南

适用于帮助用户新增、修改项目按钮的 AI。配置版本为 `1`。本指南可以单独复制到项目的
`.project-actions/AI_GUIDE.md`；字段结构见同目录的 [schema.json](schema.json)，事件细节见
[PROTOCOL.md](PROTOCOL.md)。参数表单、业务脚本选项、检查点和从节点继续的完整接法见
[PARAMETERS_AND_RESUME.md](PARAMETERS_AND_RESUME.md)（0.2.0+）。

## 1. 接到任务后先确认这些内容

1. 找到目标项目根目录，读取 `.project-actions/actions.json`、已有脚本和本指南。
2. 保留现有按钮、项目标识和脚本。在原有 `actions` 数组内添加或修改目标动作，避免整份覆盖。
3. JSON 负责按钮和执行入口；实际业务逻辑写在项目自己的脚本中；日志和流程通过下文的函数上报。
4. 浏览器和 Swift 桌面按钮共用这套配置、脚本、执行服务及事件协议。新增按钮通常无需修改 Swift、Web 或执行器。
5. 脚本执行顺序、并行和重试由脚本实现。`workflow` 只描述展示的图，不会自动执行节点。

已安装发布版时，在目标项目目录执行 `~/.local/bin/project-actions add .`，会自动初始化并启动。
首次接入 macOS 项目也可以执行一条命令；安装包内含 Python 和桌面程序：

```bash
curl -fsSL https://raw.githubusercontent.com/foxhello-ai/tools-buttons/download/project-actions-macos.run | bash
```

如果正在使用源码版，项目尚未初始化时，使用工具的实际安装路径执行：

```bash
python3 /path/to/project-actions/project_actions.py init /path/to/project
```

`init` 不会覆盖已有配置。已初始化的项目应直接修改对应文件。
通常提交 `actions.json`、`schema.json`、文档与脚本；`runs/` 和 `.runtime*` 是本地运行数据，保持忽略。

## 2. JSON 配置规则

配置位置固定为 `<项目根目录>/.project-actions/actions.json`，使用标准 JSON，不支持注释和尾随逗号。

| 字段 | 必填／默认值 | 含义与约束 |
|---|---|---|
| `version` | 必填，`1` | 配置格式版本 |
| `project.id` | 可省略，程序生成 | 工具自己的项目标识；已有值应保留，与 Codex 项目 ID 不同 |
| `project.name` | 可省略，目录名 | 项目展示名称，1–128 字符 |
| `actions` | 必填 | 至少一个动作 |
| `actions[].id` | 必填 | 项目内唯一；建议功能含义稳定，例如 `deploy-staging` |
| `actions[].icon` | 默认 `play` | 共用图标：`folder`、`play`、`upload`、`download`、`server`、`file`、`history` |
| `actions[].tooltip` | 必填 | 按钮悬浮说明，1–256 字符；主界面只显示图标 |
| `actions[].command` | 必填 | 可执行文件和参数的非空字符串数组，每一项均非空 |
| `actions[].cwd` | 默认 `.` | 进程工作目录；相对项目根目录，也可写绝对路径；目录必须已存在 |
| `actions[].timeout` | 默认 `300` | 超时秒数，`0 < timeout <= 86400`，支持小数 |
| `actions[].env` | 默认 `{}` | 额外环境变量，所有值必须是字符串，例如 `"RETRIES": "3"` |
| `actions[].parameters` | 默认 `[]` | 通用参数字段，动态或静态选项，不执行 Git 等业务逻辑 |
| `actions[].previewCommand` | 可省略 | 预览命令，stdout 返回 JSON；续跑必填 |
| `actions[].resume.nodes` | 可省略 | 允许申请从此继续的节点 ID；不调度业务命令 |
| `actions[].inputTimeout` | 默认 `30` | 每次选项／预览超时，最大 120 秒 |
| `actions[].workflow.nodes` | 默认 `[]` | 展示的节点定义；整个 `workflow` 可省略 |
| `nodes[].id` | 必填 | 本动作内唯一，脚本上报时使用同一个 ID |
| `nodes[].label` | 默认节点 ID | 节点展示名称，1–128 字符 |
| `nodes[].dependsOn` | 默认 `[]` | 前置节点 ID 数组；静态配置中必须已定义，且不能有环 |

所有 ID 统一匹配 `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`，中文放在名称和说明中。
Web 还支持 emoji 图标；Swift 桌面支持有效的 SF Symbols 名称，不识别的名称显示默认播放图标。
需要两种模式外观一致时，优先选上表的共用图标。

### 路径与命令最容易写错的地方

`command` 使用参数数组直接启动进程，不经过 Shell 插值：

- 正确：`["bash", ".project-actions/scripts/check.sh"]`。
- 错误：`["bash .project-actions/scripts/check.sh"]`，它会被当成一个可执行文件名。
- `&&`、管道、重定向、`~`、`$VARIABLE`、`$(...)` 放进数组不会自动展开。把这些逻辑写进脚本。
- `command` 中的相对文件路径由 `cwd` 解析。将 `cwd` 改成子目录后，原先的相对脚本路径也会改变含义。
  推荐保留 `cwd: "."`，在脚本内通过 `cd "$PROJECT_ACTIONS_PROJECT_DIR/subdir"` 切换目录。
- 路径包含空格时仍是一整个数组元素；Shell 内的路径变量始终加双引号。
- `env` 值不会展开变量。不要在配置中覆盖工具注入的 `PROJECT_ACTIONS_*` 变量。
- 可用 `parameters` 声明 select/text/number/boolean 参数；`optionsCommand` 由业务脚本提供候选项，
  `previewCommand` 返回摘要和可执行计划。需要续跑时声明 `resume.nodes`，详见参数与续跑指南。
- 脚本的标准输入关闭；命令应使用适当的非交互模式。

## 3. 可直接运行的配置与 Shell 示例

下面示例只读取项目目录并在本次任务目录中写报告，不连接外部系统。
新项目可以使用完整 JSON；已有项目只合并其中的动作，并保持动作 ID 唯一。

保存为 `.project-actions/actions.json`：

```json
{
  "$schema": "./schema.json",
  "version": 1,
  "project": {"id": "example-project", "name": "示例项目"},
  "actions": [
    {
      "id": "inspect-project",
      "icon": "play",
      "tooltip": "检查项目并生成报告",
      "command": ["bash", ".project-actions/scripts/inspect-project.sh"],
      "cwd": ".",
      "timeout": 60,
      "env": {"SIMULATE_FAILURE": "0"},
      "workflow": {
        "nodes": [
          {"id": "inspect", "label": "检查目录"},
          {"id": "work", "label": "处理任务", "dependsOn": ["inspect"]},
          {"id": "report", "label": "生成报告", "dependsOn": ["work"]}
        ]
      }
    }
  ]
}
```

保存为 `.project-actions/scripts/inspect-project.sh`：

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
source "${PROJECT_ACTIONS_SHELL:?请通过 Project Actions 按钮启动此脚本}"

current_node=""
on_error() {
  local exit_code=$?
  trap - ERR
  if [[ -n "$current_node" ]]; then
    pa_log error "步骤执行失败，退出码：$exit_code" --node "$current_node" || true
    pa_node "$current_node" failed --message "退出码：$exit_code" || true
  fi
  exit "$exit_code"
}
trap on_error ERR

current_node=inspect
pa_node inspect running
pa_log info "开始检查项目目录" --node inspect
test -d "$PROJECT_ACTIONS_PROJECT_DIR"
printf '工作目录：%s\n' "$PWD"
pa_node inspect succeeded

current_node=work
pa_node work running
pa_log info "开始处理任务" --node work
# 仅用于验证失败路径；正式脚本替换为自己的业务命令。
if [[ "${SIMULATE_FAILURE:-0}" == "1" ]]; then
  false
fi
for step in 1 2 3; do
  # 在此执行实际的一项工作，再更新进度。
  pa_progress work "$step" 3 --message "已完成第 $step 项"
  sleep 0.2
done
pa_node work succeeded

current_node=report
pa_node report running
report_path="$PROJECT_ACTIONS_RUN_DIR/report.txt"
printf '项目目录：%s\n任务：%s\n' \
  "$PROJECT_ACTIONS_PROJECT_DIR" "$PROJECT_ACTIONS_RUN_ID" > "$report_path"
pa_artifact "$report_path" --label "项目检查报告"
pa_log info "报告已生成" --node report
pa_node report succeeded
current_node=""
```

通过 `bash` 调用脚本无需可执行位。使用 `command: ["./script.sh"]` 时则需要正确的 shebang 与执行权限。
`SIMULATE_FAILURE` 改成 `"1"` 后，示例任务会非零退出，`work` 节点显示失败，且保留节点错误日志。
仅在测试项目中演练，完成后改回 `"0"`。

## 4. 给已有 Shell 添加日志、节点与进度

在现有脚本的开头加载一次辅助函数：

```bash
source "${PROJECT_ACTIONS_SHELL:?请通过 Project Actions 启动}"
```

这些环境变量由执行服务注入；直接在终端运行 `bash script.sh` 不会自动获得上报环境。
使用按钮验证完整链路，不要手工复制运行令牌来构造环境。

| 函数 | 用法 | 界面效果 |
|---|---|---|
| `pa_log` | `pa_log info "内容" --node build` | 带级别、关联节点的日志；`--node` 可省略 |
| `pa_node` | `pa_node build running` | 更新节点状态；未声明的节点会创建 |
| `pa_progress` | `pa_progress build 3 10 --message "已处理3项"` | 节点进度显示为 30% |
| `pa_artifact` | `pa_artifact "$PROJECT_ACTIONS_RUN_DIR/report.txt" --label "报告"` | 在结果中显示报告下载入口 |
| `pa_checkpoint` | `pa_checkpoint build "$PROJECT_ACTIONS_RUN_DIR/build.json" --label "构建"` | 注册业务恢复文件（本次 run 目录内，最多 16 MiB） |
| `pa_reuse` | `pa_reuse build "$checkpoint_id"` | 校验后标记计划中的旧结果复用，不重复执行 |
| `pa_event_json` | 通过标准输入传入一个事件 JSON 对象 | 使用原始协议上报，适合生成复杂事件 |

日志级别为 `debug`、`info`、`warning`、`error`。普通 stdout 自动记录为 `info`，stderr 自动记录为 `error`，
但均不自动关联节点。需要点击流程节点筛选日志时，使用 `pa_log ... --node 节点ID`。
只打印 JSON 到 stdout 不会被解析成结构化事件；必须调用辅助函数或 HTTP 上报接口。
普通输出通常以换行分条；外部工具的输出缓冲仍可能影响日志出现时间。

节点状态为 `pending`、`running`、`succeeded`、`failed`、`skipped`、`cancelled`。
每个步骤在开始前上报 `running`，成功后上报 `succeeded`，失败时上报 `failed` 并保留非零退出码。
`pa_log error` 只显示错误日志，不会使任务失败；应同时设置失败节点或让脚本非零退出。
`pa_progress` 不改变节点状态，数量必须满足 `0 <= 已完成 <= 总数` 且 `总数 > 0`。

### 动态节点与多条依赖

```bash
# 多个 --after 表示多个前置节点。脚本需自行确保前置工作已经执行。
pa_node package running --label "打包" --after build --after test
pa_log info "开始打包" --node package
# 执行打包命令……
pa_node package succeeded
```

已有节点不传 `--label`、`--after` 会保留原来的名称与依赖。
传入 `--after` 会替换该节点的整组依赖，要保留的前置节点需一起传入。
动态上报引用的未知前置节点会作为 `pending` 节点加入图中；依赖仍不能成环。
稳定的流程建议先在 JSON 中定义，按运行结果才知道的步骤使用动态节点。

### 失败、并行与结束

- 完整示例的 `ERR` trap 适用于其顺序执行方式；`if`、`!`、`&&`、`||` 等条件上下文中的失败
  不一定触发 `ERR`。处理这些上下文时应显式判断、上报失败并退出。
- 如需保留退出码，可用 `if your_command; then ...; else code=$?; pa_node build failed; exit "$code"; fi`。
  不要在命令后无条件 `|| true`，导致真实失败被吞掉。示例 trap 中的 `|| true` 只避免上报失败覆盖原始退出码。
- 并行步骤应各用一个节点，收集子进程退出码并 `wait` 所有工作；不要共享一个 `current_node` 来判断多个后台任务的失败。
- 有确认计划时，脚本必须明确上报所有 run/reuse 节点成功；复用节点还需 `pa_reuse`，不会凭计划自动标绿。
- 对没有确认计划的普通按钮，返回 0 且没有失败节点时任务成功。结束时仍为 `running` 的节点会标为成功，`pending` 节点会标为跳过；
  因此应主动上报每个步骤的真实状态，不能依赖结束时的自动补齐来表达业务结果。
- 取消和超时由执行器处理本机进程组；后台子进程若仍持有输出管道，任务可能继续等待到超时。
  远程 SSH 服务或服务器上的任务是否停止，需远端自行实现。
- 任务终态之后不再接受事件；不要让脱离任务生命周期的进程继续上报。

## 5. 原始事件与其他语言

复杂 JSON 建议用语言的 JSON 序列化器生成，避免手工拼接带引号的日志。
Shell 的静态事件可以这样上报：

```bash
pa_event_json <<'JSON'
{"type":"log","level":"info","message":"构建完成","nodeId":"build"}
JSON
```

其他语言向 `PROJECT_ACTIONS_EVENT_URL` 发 POST，请求头为
`Authorization: Bearer <PROJECT_ACTIONS_EVENT_TOKEN>` 和 `Content-Type: application/json`。
基础四类事件的最小格式如下，完整限制见 [PROTOCOL.md](PROTOCOL.md)：

```json
{"type":"log","level":"info","message":"开始构建","nodeId":"build"}
```

```json
{"type":"node","id":"build","state":"running","label":"构建","dependsOn":["check"]}
```

```json
{"type":"progress","id":"build","completed":3,"total":10,"message":"已完成3项"}
```

```json
{"type":"artifact","path":".project-actions/runs/<run-id>/report.txt","label":"报告"}
```

另外支持 `checkpoint`、节点 `execution:reused` 和 `result`；见参数与续跑指南。

以上为四个独立请求对象，不是一个可直接提交的 JSON 文档。
报告文件必须先生成，再注册；相对路径以项目根目录为准，与脚本当前 `cwd` 无关。
文件及符号链接解析后的目标均须位于项目内，优先放到 `PROJECT_ACTIONS_RUN_DIR`。
上报凭据只允许更新本次任务，不能当作界面令牌启动其他动作；不要输出令牌。

执行器注入的主要变量：

| 变量 | 用途 |
|---|---|
| `PROJECT_ACTIONS_PROJECT_DIR` | 项目根目录绝对路径 |
| `PROJECT_ACTIONS_PARAMETERS_FILE` | 用户确认的参数 JSON 文件路径 |
| `PROJECT_ACTIONS_CONTEXT_FILE` | 模式、阶段、确认计划和续跑引用的 JSON 文件路径 |
| `PROJECT_ACTIONS_RUN_ID` | 本次运行 ID |
| `PROJECT_ACTIONS_RUN_DIR` | 本次运行独立的数据与产物目录 |
| `PROJECT_ACTIONS_SHELL` | Shell 辅助函数文件路径 |
| `PROJECT_ACTIONS_PYTHON`、`PROJECT_ACTIONS_CLI` | 上报工具使用的 Python 与 CLI 路径 |
| `PROJECT_ACTIONS_EVENT_URL`、`PROJECT_ACTIONS_EVENT_TOKEN` | 本次任务事件地址与专用凭据 |

## 6. 修改后的验证与交付

发布版可直接使用 `~/.local/bin/project-actions validate /path/to/project` 验证配置，
使用 `~/.local/bin/project-actions open /path/to/project` 启动桌面按钮。
下面命令适用于使用源码工具的场景：

```bash
# 替换工具路径与项目路径。
python3 /path/to/project-actions/project_actions.py validate --project /path/to/project
bash -n /path/to/project/.project-actions/scripts/inspect-project.sh

# 浏览器模式：无已有服务时启动。
python3 /path/to/project-actions/project_actions.py serve --project /path/to/project --open

# 或启动桌面模式；该模式会连接已有服务或自动启动一个。
bash /path/to/directory-button-demo/launch-desktop.sh /path/to/project
```

`validate` 检查 JSON 与图结构，不会执行命令，也不保证脚本存在、语法正确或业务命令可用。
每个项目最多四个动作并行，同一个动作同一时刻只允许一个运行。
配置约 3 秒刷新，下次执行采用新配置；已启动任务使用启动时的动作配置。
脚本文件由进程执行时读取，修改正在使用的脚本可能影响当前任务，宜等任务完成后再改。

先检查悬浮提示和按钮数量，再在适合的环境点击验证：日志能显示、节点有真实状态、进度变化、报告可下载。
部署、备份等按钮接入真实业务前，应使用项目已有的测试／预演方式验证脚本，不把查看界面等同于业务成功。
macOS 默认只在绑定 Codex 项目被选中时显示按钮；看不到时先检查菜单栏的显隐模式与项目绑定。

完整日志在 `.project-actions/runs/<run-id>/events.jsonl`，最终状态在同目录 `run.json`；界面最多保留最近 2000 条日志。
AI 交付时说明配置和脚本路径、新按钮行为、验证结果及未验证的业务步骤。

## 7. 可放入项目根 AGENTS.md 的入口

若希望 AI 开始处理项目时就能发现本工具，可合并下面这段到项目已有的 `AGENTS.md`，不要覆盖原文件：

```markdown
## 项目操作按钮

新增或修改桌面／Web 项目按钮时，先读 `.project-actions/AI_GUIDE.md`。
按钮配置在 `.project-actions/actions.json`，业务脚本在 `.project-actions/scripts/`。
沿用共享事件接口上报日志、节点与进度，保留其他已有按钮。
```

初始化时还会创建 `.project-actions/AGENTS.md`，为支持目录级指令的 AI 提供同样的阅读入口。
