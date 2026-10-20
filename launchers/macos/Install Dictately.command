#!/bin/bash
cd -- "$(dirname -- "$0")/../.."
bash ./scripts/desktop/install-desktop.sh "$@"
result=$?
if [ "$result" -ne 0 ]; then
  echo 'Installation did not finish. See the message above and docs/MACOS-LINUX.md'
fi
read -r -p 'Press Return to close this window '
exit "$result"
