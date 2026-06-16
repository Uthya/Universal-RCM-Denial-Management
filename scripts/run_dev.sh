#!/usr/bin/env bash
# CR-081 Issue D — supported dev launcher.
#
# Always uses --reload so source edits under src/rcm/ trigger an automatic
# worker restart. Pass extra uvicorn flags after `--`:
#   scripts/run_dev.sh -- --log-level debug
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="src"

# Anything after "--" is forwarded to uvicorn.
extra=()
if [[ "${1:-}" == "--" ]]; then
  shift
  extra=("$@")
fi

exec python -m uvicorn rcm.main:app \
  --reload \
  --host 127.0.0.1 \
  --port 8000 \
  --log-level warning \
  "${extra[@]}"
