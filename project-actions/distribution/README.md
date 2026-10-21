# 一条命令接入项目

在目标项目目录中执行（macOS 13+，Intel / Apple Silicon）：

```bash
curl -fsSL https://raw.githubusercontent.com/foxhello-ai/tools-buttons/download/project-actions-macos.run | bash
```

一个安装文件包含 Swift 桌面应用、两种 CPU 的 Python 3.12 运行环境、执行服务、Web 页面、
JSON Schema、示例 Shell 和 AI 文档。使用时不需要另外安装 Python、Swift 或 Xcode。
下载后会验证内置压缩包的 SHA-256，再安装到当前用户目录，无需 sudo。

安装同时为当前项目生成 `.project-actions/`，向项目根 `AGENTS.md` 追加有标记的 AI 阅读入口，
并启动原生桌面按钮。已有配置、脚本与文档会保留；重复执行不会重复追加阅读入口。
默认跟随 Codex 中关联项目显示／隐藏，可从 macOS 菜单栏控制。

首次安装之后，其他项目只需：

```bash
~/.local/bin/project-actions add .
```

支持目录路径包含空格；`project-actions /path/to/project` 是 `add` 的简写。
命令位于 `~/.local/bin`，若该目录已在 `PATH` 中，可直接使用 `project-actions`。

```bash
~/.local/bin/project-actions add /path/to/project  # 初始化并启动
~/.local/bin/project-actions add . --no-launch     # 只接入，暂不启动按钮
~/.local/bin/project-actions open .                # 打开已接入项目的按钮
~/.local/bin/project-actions validate .            # 验证 JSON 与图结构
~/.local/bin/project-actions web .                 # 启动浏览器模式
~/.local/bin/project-actions version
```

默认安装到 `~/Library/Application Support/ProjectActions/releases/<版本>-<构建ID>/`。
`current` 指向本次安装版本；更新安装包会创建新版本目录，保留旧版本及所有项目配置。
已经运行的桌面实例继续使用原版本，退出后再打开才能使用新版。
重复启动会检查本项目已运行实例，不会主动恢复被手动隐藏的按钮。

如需先下载查看文件，再执行：

```bash
curl -fL https://raw.githubusercontent.com/foxhello-ai/tools-buttons/download/project-actions-macos.run -o /tmp/project-actions-macos.run
bash /tmp/project-actions-macos.run --project /path/to/project
```

安装器还支持 `--no-launch`、`--project-id ID`、`--install-dir DIR` 和 `--bin-dir DIR`。
这些选项可用于隔离测试；`--project-id` 是 Codex 项目标识，与 JSON 的 `project.id` 不同。
安装文件解包到临时目录，完成或失败后自动清理临时数据。

本地构建（仅构建者需要 Xcode Command Line Tools 与 Python 3.9+）：

```bash
python3 project-actions/distribution/build-release.py --version 0.1.0
```

构建会从 `astral-sh/python-build-standalone` 下载固定版本与 SHA-256 的运行环境，并保留上游许可证。
输出 `project-actions-macos.run`、其 SHA-256 文件及 `release.json`。
程序使用本地 ad hoc 签名，尚未提供 Developer ID 公证。Linux / Windows 不适用此 macOS 安装包。
