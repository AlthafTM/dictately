#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

case "$(uname -s)" in
  Darwin) ;;
  Linux)
    if [[ "${XDG_SESSION_TYPE:-}" == "wayland" || -n "${WAYLAND_DISPLAY:-}" ]]; then
      echo 'Dictately currently requires an X11/Xorg session, not Wayland'
      exit 1
    fi
    for dependency in xdotool xclip; do
      if ! command -v "$dependency" >/dev/null; then
        echo "Missing $dependency. See docs/MACOS-LINUX.md for the dependency install command"
        exit 1
      fi
    done
    ;;
  *) echo 'Use the Windows installer on Windows'; exit 1 ;;
esac

python_bin="${DICTATELY_PYTHON:-python3.12}"
if ! command -v "$python_bin" >/dev/null; then
  echo 'Install Python 3.12 first: https://www.python.org/downloads/'
  echo 'Then run this installer again (see docs/MACOS-LINUX.md)'
  exit 1
fi
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
"$python_bin" -m venv .venv-desktop
.venv-desktop/bin/python -m pip install -r requirements/bootstrap.txt
.venv-desktop/bin/python -m pip install -r requirements/desktop.txt
model="${DICTATELY_MODEL:-small}"
.venv-desktop/bin/python app/desktop_app.py --install-model "$model"
.venv-desktop/bin/python app/desktop_app.py --install-launcher "$@"
echo 'Setup complete. Launch Dictately from Applications / your application menu'
echo 'Keep this folder in place. On macOS, grant Microphone, Accessibility and Input Monitoring permissions'
