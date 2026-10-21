#!/usr/bin/env bash
set -euo pipefail
source "$PROJECT_ACTIONS_SHELL"

pa_node inspect running
pa_log info "开始检查项目目录" --node inspect
test -d "$PROJECT_ACTIONS_PROJECT_DIR"
printf '工作目录：%s\n' "$PWD"
pa_node inspect succeeded

pa_node count running
pa_log info "统计项目第一层文件与目录" --node count
for step in 1 2 3; do
  pa_progress count "$step" 3 --message "检查进度 $step / 3"
  sleep 0.4
done
"$PROJECT_ACTIONS_PYTHON" - <<'PY'
import os
from pathlib import Path
project = Path(os.environ['PROJECT_ACTIONS_PROJECT_DIR'])
items = list(project.iterdir())
report = Path(os.environ['PROJECT_ACTIONS_RUN_DIR']) / 'project-report.txt'
report.write_text('项目：%s\n目录：%s\n文件：%s\n子目录：%s\n' % (
    project.name, project, sum(p.is_file() for p in items), sum(p.is_dir() for p in items)), encoding='utf-8')
print('项目统计已完成')
PY
pa_node count succeeded

pa_node report running
pa_log info "报告已生成，可以从任务结果中下载" --node report
pa_artifact "$PROJECT_ACTIONS_RUN_DIR/project-report.txt" --label "项目检查报告"
pa_node report succeeded
