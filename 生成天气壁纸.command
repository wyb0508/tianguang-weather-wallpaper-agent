#!/bin/zsh
set -eu
project_dir="${0:A:h}"
if (( $# == 0 )); then
  image_path=$(/usr/bin/osascript -e 'POSIX path of (choose file with prompt "选择一张图片，生成动态天气壁纸" of type {"public.image"})') || exit 0
  set -- "$image_path" --open
fi
python_env="$project_dir/.venv/bin/python"
if [[ ! -x "$python_env" ]]; then
  candidates=(python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3")
  for candidate in "${candidates[@]}"; do
    if "$candidate" -c 'import sys; sys.exit(not ((3,10) <= sys.version_info[:2] <= (3,13)))' 2>/dev/null; then
      "$candidate" -m venv "$project_dir/.venv"
      break
    fi
  done
fi
if [[ ! -x "$python_env" ]]; then
  print -u2 '请安装 Python 3.10–3.13 后重新打开生成器。'
  exit 1
fi
if ! "$python_env" -c 'import PIL,numpy,onnxruntime,pillow_heif,cv2' 2>/dev/null; then
  "$python_env" -m pip install -r "$project_dir/requirements.txt"
fi
exec "$python_env" "$project_dir/agent.py" "$@"
