# Tools Buttons

实现项目按钮或为 Shell 添加日志、节点、进度上报前，先读 `project-actions/AI_GUIDE.md`。

- `project-actions/`：共享 JSON 配置、执行器、事件协议、Web 页面和项目模板。
- `directory-button-demo/`：Swift / AppKit 原生桌面按钮和 WebKit 任务窗口。
- `project-actions/distribution/`：内置 Python 的单文件安装器、项目接入命令及发布验证。
- 修改接入行为时保留已有项目配置、脚本与 AGENTS.md 内容。
- 后端验证：`python3 -m unittest discover -s project-actions/tests -v`。
- 安装包验证：`python3 project-actions/distribution/verify-release.py dist/project-actions-macos.run`。
