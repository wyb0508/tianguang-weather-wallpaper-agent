#!/bin/sh
set -eu
skill=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${WEATHER_PYTHON:-$skill/../../.venv/bin/python}" "$skill/scripts/build.py" "$@"
