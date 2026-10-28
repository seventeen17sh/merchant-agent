#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo "首次运行：正在创建 Python 虚拟环境。"
  task_python=""
  for task_candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; do
    if [[ -x "$task_candidate" ]] && "$task_candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
      task_python="$task_candidate"
      break
    fi
  done
  if [[ -z "$task_python" ]]; then
    echo "需要 Python 3.10 或更新版本。请安装 Python 3.12 后重新双击此文件。"
    read -r "task_wait?按回车退出…"
    exit 1
  fi
  "$task_python" -m venv .venv
fi
if ! .venv/bin/python -c 'import streamlit, langchain_openai, sqlglot, dotenv' >/dev/null 2>&1; then
  .venv/bin/python -m pip install --no-cache-dir -r requirements.lock.txt
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi
.venv/bin/python -m merchant.seed
if .venv/bin/python - <<'PY'
import sys
from urllib.request import urlopen
try:
    with urlopen('http://127.0.0.1:8501/_stcore/health', timeout=1) as response:
        sys.exit(0 if response.read().strip() == b'ok' else 1)
except Exception:
    sys.exit(1)
PY
then
  echo "本机服务已经运行，正在打开页面。"
  open http://127.0.0.1:8501
  exit 0
fi
echo "页面地址：http://127.0.0.1:8501"
echo "保持这个终端窗口打开。停止服务请在这里按 Control+C。"
exec .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false
