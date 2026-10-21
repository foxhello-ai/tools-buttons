# 原生桌面按钮

Swift / AppKit 按 JSON 生成图标按钮，支持悬浮提示、拖动、项目位置记忆和菜单栏显隐控制。
执行器复用 `../project-actions/`；日志与流程图复用 Web 页面，在应用内的 WKWebView 展示。

通常直接使用仓库根 README 中的单文件安装命令，无需本地编译或另外安装 Python。

从源码构建需要 macOS 13+、Python 3.9+ 与 Xcode Command Line Tools：

```bash
bash directory-button-demo/build-native.sh
python3 project-actions/project_actions.py init /path/to/project
bash directory-button-demo/launch-desktop.sh /path/to/project
```

源码版需要 Python；发布包内置两种架构的 Python 3.12。
`PROJECT_ACTIONS_ARCH=universal` 可构建 Intel 与 Apple Silicon 通用二进制，
`PROJECT_ACTIONS_APP_OUTPUT` 可指定构建输出位置。

自动显隐只读 Codex 的 `.codex-global-state.json` 中 `selected-project` 和 `local-projects`。
这是内部状态格式适配，多窗口依据全局最后选择的项目，不属于公开稳定 API。
每个桌面实例关联一个项目；应用已运行时，离开项目隐藏、返回恢复。
手动隐藏状态会保留，菜单栏可重新显示；退出程序后需要重新启动。

`verify-native.sh` 验证事件处理、拖动、显隐和真实 Shell / WebKit 链路；
其中生命周期夹具需要运行 Codex。安装器验证使用独立项目和偏好设置，
不等于真实桌面鼠标或真实 Codex 项目切换的端到端验证。
