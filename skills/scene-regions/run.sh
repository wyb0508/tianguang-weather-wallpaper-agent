#!/bin/sh
set -eu
skill_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
for interpreter in "$skill_dir/.venv/bin/python" "$skill_dir/../../.venv/bin/python" python3; do
    if "$interpreter" -c 'import numpy, onnxruntime, PIL, pillow_heif, cv2' 2>/dev/null; then
        exec "$interpreter" "$skill_dir/scripts/segment.py" "$@"
    fi
done
echo "请用 Python 3.10–3.13 在技能目录创建 .venv，再安装 requirements.txt 中的依赖。" >&2
exit 1
