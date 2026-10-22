# 参数、检查点与节点续跑：业务接入指南

本文对应 0.2.0，配置和事件协议仍为版本 `1`。已有普通按钮不必改造。
应用负责通用参数、预览确认、启动脚本、检查点登记、节点展示和历史记录。
目录／分支／worktree 枚举、源码复核、部署、锁、幂等和恢复均写在项目自己的脚本中。
应用不会调用 Git，也不会根据流程图调度节点。

## 先运行完整示例

安装 0.2.0 后，在任意测试项目中执行：

```bash
~/.local/bin/project-actions add . --no-launch
~/.local/bin/project-actions example .
~/.local/bin/project-actions open .
# 也可使用 project-actions web .
```

源码方式（替换工具和测试项目路径）：

```bash
python3 /path/to/project-actions/project_actions.py init /path/to/test-project
python3 /path/to/project-actions/project_actions.py example --project /path/to/test-project
python3 /path/to/project-actions/project_actions.py serve --project /path/to/test-project --open
```

已初始化项目跳过 `init`。`example` 追加 `resumable-demo` 按钮，保留项目 ID 和已有按钮；
发现同名动作、脚本目录或 `demo-data/` 时拒绝覆盖。它只操作本地模拟文件，不连接服务器。

点击新增按钮，选择“财务 目录”和模拟环境，预览并确认。在“模拟部署”进行中点“停止任务”；
停止后点击该节点 → “从此节点继续” → 查看两个前置节点复用 → 确认继续。
若想重新构建，选择“构建与测试”节点。每次继续创建新任务，原任务的日志和文件保持原样。

示例被复制到 `.project-actions/scripts/resumable-demo/`：

| 文件 | 业务方参考内容 |
| --- | --- |
| `pipeline.sh` | Shell 统一入口，将 options / preview / run 分派给业务程序 |
| `pipeline.py` | 完整选项、版本固定、预览、检查点、产物校验、续跑与结果上报 |
| `worktree-options.py` | 可选的项目侧 Git worktree 枚举器；不是应用内置 Git 能力 |
| `README.md` | 演练方法与改造位置 |

## JSON：增加参数和续跑入口

将这个动作合并到已有 `actions` 数组；对应脚本由业务项目编写。

```json
{
  "id": "deploy",
  "icon": "upload",
  "tooltip": "部署",
  "cwd": ".",
  "timeout": 1800,
  "inputTimeout": 30,
  "parameters": [
    {
      "name": "source",
      "label": "部署来源",
      "type": "select",
      "optionsCommand": ["bash", ".project-actions/scripts/deploy.sh", "options"]
    },
    {
      "name": "environment",
      "label": "环境",
      "type": "select",
      "default": "staging",
      "options": [{"label": "测试环境", "value": "staging"}]
    },
    {"name": "note", "label": "备注", "type": "text", "required": false},
    {"name": "retries", "label": "重试次数", "type": "number", "default": 2},
    {"name": "smokeTest", "label": "执行冒烟检查", "type": "boolean", "default": true}
  ],
  "previewCommand": ["bash", ".project-actions/scripts/deploy.sh", "preview"],
  "command": ["bash", ".project-actions/scripts/deploy.sh", "run"],
  "resume": {"nodes": ["check", "build", "deploy", "verify"]},
  "workflow": {"nodes": [
    {"id": "check", "label": "检查"},
    {"id": "build", "label": "构建与测试", "dependsOn": ["check"]},
    {"id": "deploy", "label": "部署", "dependsOn": ["build"]},
    {"id": "verify", "label": "部署后检查", "dependsOn": ["deploy"]}
  ]}
}
```

- `parameters` 最多 64 个。`name` 遵循 ID 规则，`label` 1–256 字符；`required` 默认 true。
- `select` 必须且只能提供静态 `options` 或动态 `optionsCommand`。值可以是 JSON 对象、数组或标量。
- `text`、`number`、`boolean` 分别传字符串、有限数值、布尔值；不转换成命令行字符串。
- `default` 是界面的初始值，必填不等于用户必须勾选布尔框。数字范围、必选 true 等业务条件由预览和执行脚本验证。
- 参数／预览命令沿用动作 `cwd` 和 `env`，argv 不做 Shell 插值。脚本 stdin 关闭。
- 有参数或 `previewCommand` 的动作必须先预览，再确认。只有参数、没有预览命令时，应用生成参数摘要。
- `resume` 必须配 `previewCommand`；`nodes` 只声明允许申请继续的节点，不定义节点命令。
  动态节点也需显式列入 `resume.nodes`，且已存在于原任务中。
- `inputTimeout` 默认 30 秒，最大 120 秒；每次选项／预览输出合计最多 1 MiB。
  这些命令约定只读；不要在这里 checkout、构建、部署或上报任务事件。应用不对脚本做只读沙箱隔离。

## 选项命令返回什么

stdout 只能输出一个 JSON 对象，诊断写 stderr。非零退出、超时、无效 JSON 会在表单显示错误。
应用调用每个 `optionsCommand` 一次，传入 `phase: "options"`、`parameter: "参数名"`，参数文件为 `{}`；
首期不支持依赖另一表单参数的联动选项。

```json
{
  "options": [
    {
      "label": "财务分支",
      "value": {"directory": "/repo finance", "branch": "feat/finance", "commit": "完整提交"},
      "details": [
        {"label": "目录", "value": "/repo finance"},
        {"label": "分支", "value": "feat/finance"},
        {"label": "提交", "value": "完整提交"},
        {"label": "修改状态", "value": "干净"}
      ]
    },
    {"label": "其他来源", "value": "other", "disabledReason": "存在未提交修改"}
  ]
}
```

可选 `default` 需与一个可用选项的 `value` 相等。最多 1000 项，值不得重复，单个值序列化后上限 16 KiB。
`details` 与后续 `summary` 都是最多 128 项的字符串 `label/value` 数组。
应用只解释通用字段，不解释 `value` 中 directory、branch、commit 等业务字段。

业务侧选择已有 worktree 的接法：将示例动作的 source.optionsCommand 改为
`["python3", ".project-actions/scripts/resumable-demo/worktree-options.py"]`，
并在该动作 `env` 配置 `EXAMPLE_REPOSITORY` 为仓库绝对路径；运行它需要 Git 和 Python 3.9+。
示例列出路径、分支、完整提交、修改状态，支持中文、空格、detached HEAD，禁用脏目录；不会 checkout。
它读取选定仓库的受版本控制文件并计算摘要；仅供业务脚本参考。
实际项目应自行界定构建输入范围（示例 `paths: ["."]`）、未跟踪文件／子模块／符号链接策略，
以及扫描成本；在整个仓库中生成新的非忽略文件可能导致后续复核被拒绝。
需切分支时，同样通过参数传入目标名称，再由 run 阶段的业务脚本处理；应优先选择已存在的 worktree。

## 参数文件和上下文

程序通过两个环境变量传递 JSON 文件路径；所有参数都从文件读取，不拼接到 Shell 命令中。

```bash
py="${PROJECT_ACTIONS_PYTHON:?}"
source_dir="$($py - <<'PY'
import json, os
p = json.load(open(os.environ['PROJECT_ACTIONS_PARAMETERS_FILE']))
print(p['source']['directory'])
PY
)"
cd -- "$source_dir"
```

`PROJECT_ACTIONS_PARAMETERS_FILE` 是用户确认的参数对象，最多 32 KiB。
续跑沿用原参数，不能同时改来源或环境；需要改参数时从按钮新建任务。

`PROJECT_ACTIONS_CONTEXT_FILE` 是服务生成的上下文。首次运行 `mode: "new"`；续跑示意：

```json
{
  "version": 1,
  "mode": "resume",
  "phase": "run",
  "runId": "本次新任务ID",
  "nodeIds": ["check", "build", "deploy", "verify"],
  "resume": {
    "fromNode": "deploy",
    "parentRunId": "上次任务ID",
    "rootRunId": "最初任务ID",
    "parentRunDirectory": "/project/.project-actions/runs/上次任务ID",
    "checkpoints": [
      {
        "id": "服务分配的检查点ID",
        "nodeId": "build",
        "runId": "实际产出检查点的任务ID",
        "path": "/project/.project-actions/runs/实际产出任务ID/build-checkpoint.json",
        "sha256": "检查点文件摘要",
        "size": 256,
        "label": "构建结果"
      }
    ]
  },
  "plan": [
    {"nodeId": "build", "operation": "reuse", "checkpointId": "服务分配的检查点ID"},
    {"nodeId": "deploy", "operation": "run"},
    {"nodeId": "verify", "operation": "run"}
  ]
}
```

上例省略 check 的检查点和计划项。预览时 `phase: "preview"`，没有新 runId，也没有本次 run 目录／上报令牌；
预览输入还不包含即将返回的 plan。正式执行有 `phase: "run"` 和已确认的 plan。
首次任务没有 resume、nodeIds。连续续跑时检查点可能来自更早的祖先任务，不要拼接父目录猜测位置。
文件按只读输入使用；快照不会物理锁定源码或远程环境。

## 预览协议和业务检查

`previewCommand` 在首次执行和续跑时都会调用。读取上述两个文件，验证业务状态，然后返回：

```json
{
  "allowed": true,
  "summary": [{"label": "环境", "value": "staging"}, {"label": "固定提交", "value": "完整提交"}],
  "plan": [
    {"nodeId": "check", "operation": "reuse", "checkpointId": "检查节点的检查点ID"},
    {"nodeId": "build", "operation": "reuse", "checkpointId": "构建节点的检查点ID"},
    {"nodeId": "deploy", "operation": "run"},
    {"nodeId": "verify", "operation": "run"}
  ]
}
```

不能执行时返回 `{"allowed":false,"reason":"构建产物缺失，请从构建节点继续"}`。
业务条件不满足时建议以 0 退出并返回 allowed:false；脚本自身故障以非零退出并写 stderr。

计划最多 256 项，节点不得重复，必须是配置图节点或原任务已有动态节点。
`operation` 支持 `run/reuse/skip`；首次执行不能引用旧检查点；续跑选择的起点必须 operation:run。
引用的检查点必须由该节点实际登记。程序验证文件归属和 SHA-256，业务脚本验证其内容及实际产物。
并行、下游失效、环境锁和执行顺序由脚本决定；应用不会调整你返回的业务计划。
首次执行可以省略 plan；续跑必须提供包含起点的计划。

选项和预览凭据有效 10 分钟。改变表单参数会取消已确认预览；动作 JSON 改动后需要重新预览。
确认消费一次性凭据后才创建任务。预览后到执行间可能发生变化，因此 run 阶段必须再次：

1. 校验固定提交、实际源码和构建输入；不要只比较分支名。
2. 校验复用检查点格式及其对应的真实文件／摘要。
3. 获得业务环境互斥，确认前次远端任务已停止或可以恢复，并核对远端实际版本。
4. 在发布前再次复核输入，按业务幂等策略操作。业务侧可用不可变源码快照进一步防止竞态。

同一个按钮的本机并发限制不能代替跨项目、跨机器的环境锁。

## Shell 如何保存检查点和报告复用

以下片段放在 run 阶段；完整可运行流程见示例。业务命令及其检查点内容由业务方定义。

```bash
set -Eeuo pipefail
source "${PROJECT_ACTIONS_SHELL:?}"
py="${PROJECT_ACTIONS_PYTHON:?}"

pa_node build running
pa_log info "开始构建" --node build
# 在此执行自己的构建／测试命令，并检查退出码。
# 这里生成一个可运行的本地演示产物。
printf 'example artifact\n' > "$PROJECT_ACTIONS_RUN_DIR/package.txt"

# 先原子写入业务 JSON，再注册；不能把节点变绿当作检查点。
"$py" - <<'PY'
import hashlib, json, os
from pathlib import Path
root = Path(os.environ['PROJECT_ACTIONS_RUN_DIR'])
artifact = root / 'package.txt'
data = {'version': 1,
        'parameters': json.load(open(os.environ['PROJECT_ACTIONS_PARAMETERS_FILE'])),
        'artifact': str(artifact),
        'artifactSha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}
temporary = root / 'build-checkpoint.tmp'
temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
temporary.replace(root / 'build-checkpoint.json')
PY
pa_checkpoint build "$PROJECT_ACTIONS_RUN_DIR/build-checkpoint.json" --label "构建结果"
pa_artifact "$PROJECT_ACTIONS_RUN_DIR/package.txt" --label "构建产物"
pa_node build succeeded
```

检查点是最多 16 MiB 的既有文件，解析符号链接后须位于**本次任务目录**。
`pa_checkpoint` 的相对路径相对于 run 目录；`pa_artifact` 的相对路径相对于项目根目录。
同路径重新登记会更新本次快照的引用，旧事件仍保留。续跑不要改写原任务文件。
程序只校验检查点文件完整性，不解析其 JSON，更不会自动验证其指向的业务产物。

业务脚本读取确认计划，只有在自行复核检查点和真实产物之后，才能标记 reuse：

```bash
# checkpoint_id 从 context.plan 的对应步骤读取；不能使用节点名代替。
checkpoint_id="$($py - <<'PY'
import json, os
c = json.load(open(os.environ['PROJECT_ACTIONS_CONTEXT_FILE']))
step = next(s for s in c['plan'] if s['nodeId'] == 'build')
if step['operation'] != 'reuse':
    raise SystemExit('此段只适用于计划中的复用分支')
cp = next(x for x in c['resume']['checkpoints'] if x['id'] == step['checkpointId'])
data = json.load(open(cp['path']))
import hashlib
from pathlib import Path
if data.get('version') != 1:
    raise SystemExit('检查点格式不兼容')
if data['parameters'] != json.load(open(os.environ['PROJECT_ACTIONS_PARAMETERS_FILE'])):
    raise SystemExit('检查点参数不同')
if hashlib.sha256(Path(data['artifact']).read_bytes()).hexdigest() != data['artifactSha256']:
    raise SystemExit('产物已变化，需要重新构建')
print(cp['id'])
PY
)"
pa_reuse build "$checkpoint_id"
pa_log info "构建产物校验通过，复用原任务结果" --node build
```

`pa_reuse` 等价于上报 `node` 的 succeeded + execution:reused + checkpointId。
界面显示“复用结果”和所属任务。应用不会因为计划写了 reuse 自动标绿；
有计划的任务在返回 0 前需明确上报所有非 skip 步骤成功，复用步骤还需正确上报 reuse。
执行 run 分支时仍用 `pa_node ... running/succeeded`；skip 分支上报 `pa_node ... skipped`。
Shell 的 `ERR` trap、后台任务退出码等处理见 [AI_GUIDE.md](AI_GUIDE.md)。

## 上报实际发布结果

产物下载用 `pa_artifact`；来源、提交、实际发布 ID／镜像 digest 等由 result 保存并在界面显示：

```bash
"$py" - <<'PY' | pa_event_json
import json, os
p = json.load(open(os.environ['PROJECT_ACTIONS_PARAMETERS_FILE']))
print(json.dumps({'type': 'result', 'data': {
    'source': p['source'], 'environment': p['environment'],
    'releaseId': 'replace-with-actual-published-id',
    'artifactSha256': 'replace-with-actual-published-digest'
}}, ensure_ascii=False))
PY
```

占位值需要替换成业务命令实际返回的发布结果；不要只记录期望发布的分支。
`result.data` 是最多 32 KiB 的 JSON 对象，再次上报会替换当前结果。
任务目录保留 `parameters.json`、`context.json`、`run.json`、`events.jsonl` 及业务文件。
`run.json` 包含动作配置、计划、parentRunId、rootRunId、resumeFromNode、检查点和实际结果。
动作 JSON 改动后不允许从旧任务继续；脚本文件版本和检查点格式的兼容性由业务方自己校验。
旧任务未登记检查点不能继续，仍可查看日志或重新执行按钮。

## AI／其他客户端调用

这是本机 HTTP JSON API，可由 AI 工具、CLI 或自建 MCP 适配器调用；本项目没有提供 MCP server。
服务地址和界面 token 从项目 `.project-actions/.runtime.json` 读取，只用于本机，不要输出或提交。
事件 token 仅能为本次任务上报，不能启动按钮。

调用顺序（所有请求使用 `Authorization: Bearer <interface-token>`）：

```text
GET  /api/actions/deploy/options
     -> optionsId, parameters（动态选项已解析）
POST /api/actions/deploy/preview
     {"optionsId":"...","parameters":{"source":...,"environment":"staging",...}}
     -> allowed, summary, plan, previewId, parameters, context
POST /api/runs
     {"actionId":"deploy","previewId":"..."}
     -> 202，新任务快照

POST /api/runs/<stopped-run-id>/resume-preview
     {"nodeId":"deploy"}
     -> allowed, summary, plan, previewId, parameters, context
POST /api/runs
     {"actionId":"deploy","previewId":"..."}
     -> 202，新续跑任务快照
```

调用方确认 allowed:true 后才使用 previewId；allowed:false 没有可执行凭据。
无参数的预览动作可以直接提交 parameters:{}；有参数的动作需先获取 optionsId（包括纯静态选项）。
取消预览只需丢弃凭据，不创建任务。重复、过期、配置变化返回 409；校验错误 400；
探测超时 408；待确认缓存超过 100 项返回 429；非法凭据 403。
接口只运行配置中声明的命令，不接受调用方临时传 command、cwd、任意检查点路径或变更续跑参数。
完整事件、SSE 和历史接口见 [PROTOCOL.md](PROTOCOL.md)。

## 接入检查

验证 JSON 与 Shell 语法；在隔离环境验证“执行 → 停止 → 选节点 → 预览 → 继续”。
至少演练源码变化、产物丢失、检查点不兼容、预览后业务状态变化，以及远端任务尚未停止的拒绝路径。
真实部署应由项目脚本验证远程锁、发布身份、回滚和恢复策略。示例只提供本地流程，不是生产部署脚本。
