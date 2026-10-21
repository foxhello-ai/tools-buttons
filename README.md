# Project Actions 下载文件

此分支保存单文件 macOS 安装包，源码和接入文档位于仓库的 main 分支。

在目标项目目录执行：

```bash
curl -fsSL https://raw.githubusercontent.com/foxhello-ai/tools-buttons/download/project-actions-macos.run | bash
```

安装包约 64 MiB，适用于 macOS 13+，内置 Intel / Apple Silicon 程序、Python 3.12、执行服务及 AI 文档。
后续项目执行 `~/.local/bin/project-actions add .`。
`release.json` 记录版本、运行环境来源与安装包 SHA-256；旁边的 `.sha256` 文件可独立校验下载文件。
