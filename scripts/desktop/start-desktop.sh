#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
if [[ ! -x .venv-desktop/bin/python ]]; then
  echo 'Run bash scripts/desktop/install-desktop.sh first'
  exit 1
fi
exec .venv-desktop/bin/python app/desktop_app.py
