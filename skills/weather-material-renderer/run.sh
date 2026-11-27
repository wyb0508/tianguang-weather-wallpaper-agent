#!/bin/sh
set -eu
base=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
for python in "$base/.venv/bin/python" "$base/../../.venv/bin/python" python3; do
    if "$python" -c 'import numpy, PIL, cv2' 2>/dev/null; then
        exec "$python" -B "$base/scripts/render.py" "$@"
    fi
done
echo '请在技能目录的 .venv 中安装 requirements.txt。' >&2
exit 1
