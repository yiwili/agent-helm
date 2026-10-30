#!/bin/sh
set -eu
script_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${AGENT_ORCH_PYTHON:-python3}" "$script_root/scripts/build.py" "$@"
