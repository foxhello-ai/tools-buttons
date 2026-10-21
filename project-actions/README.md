# Project Actions

给任意项目增加可点击的操作按钮。按钮由 JSON 定义，功能由项目自己的 Shell、Python、PowerShell 等脚本实现。
程序负责执行、收集日志和展示任务图；点击固定按钮不调用模型。

支持浏览器和 macOS 原生桌面按钮两种模式，共用 JSON、Python 标准库执行服务与事件协议，无第三方 Python 包依赖。
默认只有图标按钮和悬浮提示。浏览器点击后展开详情；桌面版在应用内的任务窗口显示同一套日志与流程图。
收起或关闭详情不会停止任务。

让 AI 新增按钮或改造脚本时，先读 [AI_GUIDE.md](AI_GUIDE.md)：包含配置字段、路径规则、
完整 Shell 示例、日志与节点上报、失败处理和验证步骤。
`init` 会将指南、事件协议和目录级 `AGENTS.md` 一起放入目标项目的 `.project-actions/`，方便 AI 就近读取。

## 给项目接入

macOS 13+ 可在目标项目目录中直接执行一条命令（内置 Python，无需本地编译）：

```bash
curl -fsSL https://raw.githubusercontent.com/foxhello-ai/tools-buttons/download/project-actions-macos.run | bash
```

安装后，其他项目执行 `~/.local/bin/project-actions add .` 即可。会生成配置和 AI 文档、保留已有文件，
并启动原生按钮。单文件安装器及更新说明见 [distribution/README.md](distribution/README.md)。

下面是从源码手工接入的方式：

将本工具目录放在一个固定位置；所有项目可以共用一份工具，不需要复制程序。
当前仓库中的入口是 `project-actions/project_actions.py`。

```bash
# 以下路径换成工具的实际存放位置。
python3 /path/to/project-actions/project_actions.py init /path/to/your-project
python3 /path/to/project-actions/project_actions.py serve --project /path/to/your-project --open
```

也可以进入项目目录后执行 `init .` 和 `serve --open`。服务默认分配空闲端口，启动时打印访问地址。
`init` 创建配置、Schema 和两个示例脚本，已有配置或脚本目录会被保留，不会覆盖。

使用 macOS 桌面按钮（先构建一次 `.app`）：

```bash
bash /path/to/directory-button-demo/build-native.sh
bash /path/to/directory-button-demo/launch-desktop.sh /path/to/your-project
```

桌面版保留拖动、项目位置记忆、菜单栏显隐控制与 Codex 项目跟随；配置约 3 秒刷新。
详细说明见 `../directory-button-demo/README.md`。

```text
your-project/
  .project-actions/
    actions.json                 # 按钮与执行入口，可提交版本库
    schema.json                  # 编辑器自动补全与结构说明
    AGENTS.md                    # AI 阅读入口
    AI_GUIDE.md                  # JSON 与脚本接入指南，含可运行示例
    PROTOCOL.md                  # 事件字段与接口协议
    scripts/                     # 自己编写的脚本
    runs/<run-id>/               # 每次运行的状态、完整事件日志、任务产物
    .runtime.json                # 服务发现与本地凭据，自动生成，不提交
    .gitignore                   # 忽略 runs/ 和 .runtime*
```

运行状态按项目分开存储。修改配置后页面会刷新按钮定义，下一次点击使用新配置，无需重启；
已启动的任务继续使用启动时的配置。一个动作同时运行一个任务，最多四个动作并行。

## 按钮配置

`.project-actions/actions.json` 示例：

```json
{
  "$schema": "./schema.json",
  "version": 1,
  "project": {"id": "my-project", "name": "我的项目"},
  "actions": [
    {
      "id": "deploy",
      "icon": "upload",
      "tooltip": "部署测试环境",
      "command": ["bash", ".project-actions/scripts/deploy.sh"],
      "cwd": ".",
      "timeout": 600,
      "env": {"DEPLOY_ENV": "staging"},
      "workflow": {
        "nodes": [
          {"id": "check", "label": "检查"},
          {"id": "build", "label": "构建", "dependsOn": ["check"]},
          {"id": "release", "label": "发布", "dependsOn": ["build"]}
        ]
      }
    }
  ]
}
```

- 共用 `icon` 为 `folder`、`play`、`upload`、`download`、`server`、`file`、`history`；Web 支持 emoji，桌面支持有效的 SF Symbols 名称，未知名称使用播放图标。
- `command` 是可执行文件和参数数组，程序不对字符串做 Shell 拼接或展开。需要 Shell 语法时写进脚本。
- `cwd` 默认项目根目录；相对路径以项目根目录为准，也支持绝对路径。
- `timeout` 单位为秒，默认300；`env` 的值必须是字符串。
- `workflow` 可省略，节点也可以在运行时动态上报。依赖必须构成无环图。
- 工作流用于展示。节点顺序、并行、失败分支和重试逻辑由脚本控制，程序不会根据图自动调度命令。

验证配置：

```bash
python3 /path/to/project-actions/project_actions.py validate --project /path/to/your-project
```

## Shell 上报接口

脚本运行时会自动获得上报地址、任务凭据和辅助工具路径：

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$PROJECT_ACTIONS_SHELL"

pa_node check running
pa_log info "正在检查配置" --node check
# 自己的检查命令
pa_node check succeeded

pa_node build running
pa_progress build 1 3 --message "已完成第一步"
echo "普通 stdout 也会自动进入日志"
# 自己的构建命令
pa_progress build 3 3
pa_node build succeeded

# 配置未声明的节点也可动态创建，并连接到已有节点。
pa_node release running --label "发布" --after build
pa_log info "开始发布" --node release
# 自己的发布命令
pa_node release succeeded
```

接口列表：

| 函数 | 参数 |
|---|---|
| `pa_log` | `info "内容" [--node 节点ID]`；级别支持 debug/info/warning/error |
| `pa_node` | `节点ID 状态 [--label 名称] [--after 前置节点ID] [--message 说明]` |
| `pa_progress` | `节点ID 已完成数量 总数量 [--message 说明]` |
| `pa_artifact` | `项目内文件路径 [--label 展示名称]`，生成可下载的任务产物 |
| `pa_event_json` | 从标准输入读取一个事件 JSON 对象 |

节点状态：`pending`、`running`、`succeeded`、`failed`、`skipped`、`cancelled`。
`--after` 可重复使用，表示多个前置节点。图中点击节点可筛选该节点的结构化日志。
stdout/stderr 自动采集；需要日志关联到具体节点时使用 `pa_log --node`。

脚本返回0且没有失败节点时任务成功；非0退出码、失败节点或启动失败会显示失败。
用户停止或超时会终止本机任务进程组。未完成节点会随任务结果更新，尚未执行的节点在成功结束时标为跳过。
远程 SSH 任务是否随本机连接终止，要由远程脚本自行处理。

## 其他语言／HTTP 上报

所有辅助函数最终发送 JSON 到同一个 HTTP 接口。其他语言可以使用同样的协议，无需依赖 Bash。

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $PROJECT_ACTIONS_EVENT_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"type":"log","level":"info","message":"构建完成","nodeId":"build"}' \
  "$PROJECT_ACTIONS_EVENT_URL"
```

事件格式、环境变量和浏览器接口见 [PROTOCOL.md](PROTOCOL.md)。
任务凭据只允许向本次任务上报事件，不能启动其他按钮。

## 日志和恢复

- 实时事件通过 SSE 推送到界面，断线后按事件序号重连。
- 页面保留最近2000条日志；完整结构化记录位于对应运行目录的 `events.jsonl`。
- 任务记录保存在 `run.json`，关闭页面后可通过历史按钮查看，刷新页面也可重新进入运行任务。
- 正常停止服务会取消本机运行任务；服务异常退出后，未结束的历史任务标记为中断，不自动重跑。
- 服务重启后需刷新页面以取得新会话凭据；单次连接中断则自动按游标重连。
- 任务报告等文件可通过 `pa_artifact` 注册，界面提供下载按钮。

## 平台和窗口边界

需要 Python 3.9+ 和支持 fetch 流的现代浏览器。本版已在 macOS 验证。
默认示例使用 Bash；Linux 可沿用，Windows 需要 Bash 环境或将 `command` 换成 PowerShell/Python 等入口。
Windows 的任务树终止路径已经实现，尚未在 Windows 实机验证。

浏览器版按启动目录读取配置，不控制整个浏览器窗口的显隐。
macOS 的 `directory-button-demo` 已接入共享配置、执行服务和详情页面，需要 macOS 13+、Python 3.9+。
它使用 Swift / AppKit 原生浮动按钮，任务窗口使用 WebKit；目前只在 macOS 构建验证。
Windows/Linux 可复用执行服务与 Web 页面，原生窗口需要对应平台的实现或替换桌面壳。

CLI 服务按项目使用文件锁，只允许一个服务写入该项目任务状态。
`.project-actions/.runtime.json` 记录 URL、项目路径与接口凭据，供桌面连接已有服务；权限为 `0600`，不要提交或分享。
正常停止服务会移除发现文件。已有服务运行时，再次 `serve` 会提示复用该文件中的 URL。
桌面程序退出会停止它自己启动的服务；独立启动的网页服务不受影响。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

集成测试实际启动脚本和 HTTP 服务，验证项目隔离、配置重载、Shell 事件、进度、任务图、产物下载、
SSE、历史恢复、stdout/stderr、失败、超时、取消、重复点击和接口边界。
另覆盖 CLI 服务发现、凭据文件权限、项目服务互斥和退出清理；桌面集成检查使用 `../directory-button-demo/verify-native.sh`。
