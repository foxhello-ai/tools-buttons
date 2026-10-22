# 可运行的业务侧参数与续跑示例

这是项目脚本示例，所有源码／Git／产物／发布判断都在这些文件中，执行器不处理业务。
`pipeline.sh` 是 Bash 入口；为减少 JSON 转义和外部依赖，业务实现使用 Python 标准库。
Shell 事件写法及协议详见项目 `.project-actions/PARAMETERS_AND_RESUME.md`。

## 安装与演练

0.2.0 及以上：在已接入工具的测试项目执行 `~/.local/bin/project-actions example .`，再 `project-actions open .`。
源码方式：`python3 /path/to/project_actions.py example --project /path/to/test-project`。
仅追加一个动作，不覆盖既有项目标识、按钮、脚本。重复安装会拒绝。

1. 点击“参数与节点续跑示例（本地模拟）”，选择来源目录和环境，预览后确认。
2. 等到“模拟部署”进行中时停止任务（默认等待 8 秒）。
3. 点击“模拟部署”节点，再点“从此节点继续”；预览显示检查、构建复用，部署和检查执行。
4. 确认。新任务引用旧构建产物，原日志／检查点／产物保持原样。
5. 也可选择构建节点，验证重新构建；改动模拟源码或删除 built.txt，验证拒绝复用。

源码在 `.project-actions/demo-data/sources/`；模拟环境为该目录下 `environment/current-<环境>.json`。
没有 SSH、网络发布或真实服务器操作。删除示例时，先停止任务，再从配置移除 resumable-demo，
按需清理 scripts/resumable-demo 和 demo-data；旧 runs/ 留作历史，不要覆盖其他项目内容。

## 按业务改造

- `install.py` 的 action：字段、候选选项、步骤标签和 Shell 入口。
- `main()` options：从业务系统枚举来源；stdout 严格返回 JSON。
- `identity()`：固定提交及实际构建输入，运行前和发布前复核。业务可使用不可变源码快照。
- `preview()`：决定哪些步骤 run / reuse / skip；拒绝不满足条件的请求，不在预览产生业务副作用。
- `validate_checkpoint()`：检查自己定义的格式、业务参数及实际产物；部署后检查还要核对环境真实版本。
- `run()`：重新执行预检，再按确认计划做业务操作，及时上报日志、节点、进度、检查点和实际发布身份。

本例的 atomic JSON 文件仅模拟发布。真实部署必须自行实现跨机器环境锁、远程任务核对、
可恢复／幂等副作用、发布前源码和产物复核、发布后验证与回滚。取消本机进程不能证明远端操作已停止。

## 可选 Git worktree 来源

把 source 的 optionsCommand 改为 `["python3", ".project-actions/scripts/resumable-demo/worktree-options.py"]`，
env 增加 `"EXAMPLE_REPOSITORY":"/absolute/repo"`。需安装 Git 和 Python 3.9+。
候选包含目录、分支、完整 HEAD 和修改状态；脏目录不可选，可直接选择被另一 worktree 占用的财务分支目录。
所有 Git 调用都在项目侧 provider 和校验脚本中，不会自动 checkout。

这只是一个保守示范：它对全仓库跟踪文件计算摘要，支持 detached HEAD，拒绝符号链接／子模块，
并拒绝非忽略的未跟踪文件。大型仓库需要调整源码范围和 inputTimeout；项目自己产生的输出应被忽略或放到仓库外。
不要把本例默认策略直接当作生产项目构建输入清单。
