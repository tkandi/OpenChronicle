#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${OPENCHRONICLE_APP_INSTALL_DIR:-/Applications}"
SOURCE_ARCHIVE="${ROOT_DIR}/dist/OpenChronicle.app.zip"
DEST_APP="${INSTALL_DIR}/OpenChronicle.app"

designated_requirement() {
  codesign -dr - "$1" 2>&1 \
    | awk '/^#? designated =>/ { sub(/^# /, ""); print }'
}

OLD_REQUIREMENT=""
if [[ -d "${DEST_APP}" ]]; then
  OLD_REQUIREMENT="$(designated_requirement "${DEST_APP}" || true)"
fi

"${ROOT_DIR}/scripts/build-macos-app.sh"

mkdir -p "${INSTALL_DIR}"
pkill -TERM -x OpenChronicle 2>/dev/null || true
for _ in {1..30}; do
  if ! pgrep -x OpenChronicle >/dev/null; then
    break
  fi
  sleep 0.1
done
if [[ -e "${DEST_APP}" ]]; then
  rm -rf "${DEST_APP}"
fi
ditto -x -k --norsrc --noextattr --noqtn --noacl "${SOURCE_ARCHIVE}" "${INSTALL_DIR}"
xattr -cr "${DEST_APP}"
codesign --verify --deep --strict --verbose=2 "${DEST_APP}"
NEW_REQUIREMENT="$(designated_requirement "${DEST_APP}" || true)"

echo "Installed ${DEST_APP}"
if [[ -n "${OLD_REQUIREMENT}" && "${OLD_REQUIREMENT}" != "${NEW_REQUIREMENT}" ]]; then
  echo "Privacy identity changed. Remove the old OpenChronicle rows from macOS Privacy & Security and add ${DEST_APP} again."
fi
echo "Opening OpenChronicle. Grant permissions to OpenChronicle.app, not your terminal."
open "${DEST_APP}"
