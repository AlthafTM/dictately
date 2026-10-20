#!/bin/bash
cd -- "$(dirname -- "$0")"
if [ ! -x .venv-desktop/bin/python ]; then
  echo 'Run Install Dictately.command first'
  read -r -p 'Press Return to close this window '
  exit 1
fi
exec .venv-desktop/bin/python desktop_app.py
