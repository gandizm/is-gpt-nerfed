#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
DIST="${DIST:-$ROOT/dist-linux}"
WORK="${WORK:-$ROOT/build-linux}"

"$PYTHON" -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name is-gpt-nerfed-ui \
  --distpath "$DIST" --workpath "$WORK" \
  --add-data "$ROOT/plugin:plugin" \
  --add-data "$ROOT/macos/Sources/IsGPTNerfed/Resources/zh-Hans.lproj/Localizable.strings:localization" \
  --add-data "$ROOT/macos/Resources:resources" \
  "$ROOT/windows/ui_launcher.py"

echo "built $DIST/is-gpt-nerfed-ui"
