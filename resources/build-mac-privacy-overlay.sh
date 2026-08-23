#!/usr/bin/env bash
# Compile mac-privacy-overlay into a native macOS helper.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REASON="${SCRIPT_DIR}/mac-privacy-overlay-reason.swift"
CORE="${SCRIPT_DIR}/mac-privacy-overlay-core.swift"
MAIN="${SCRIPT_DIR}/mac-privacy-overlay.swift"
INFO="${SCRIPT_DIR}/mac-privacy-overlay-Info.plist"
OUT="${SCRIPT_DIR}/mac-privacy-overlay"
APP_DIR="${OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR:-${SCRIPT_DIR}/OpenChroniclePrivacyOverlay.app}"
APP_OUT="${APP_DIR}/Contents/MacOS/mac-privacy-overlay"

if [[ ! -f "${REASON}" || ! -f "${CORE}" || ! -f "${MAIN}" || ! -f "${INFO}" ]]; then
  echo "[mac-privacy-overlay] Source not found." >&2
  exit 1
fi

if [[ -x "${OUT}" && -x "${APP_OUT}" \
      && "${OUT}" -nt "${REASON}" && "${OUT}" -nt "${CORE}" && "${OUT}" -nt "${MAIN}" \
      && "${OUT}" -nt "${INFO}" && "${OUT}" -nt "$0" \
      && "${APP_OUT}" -nt "${REASON}" && "${APP_OUT}" -nt "${CORE}" \
      && "${APP_OUT}" -nt "${MAIN}" && "${APP_OUT}" -nt "${INFO}" \
      && "${APP_OUT}" -nt "$0" ]]; then
  echo "[mac-privacy-overlay] App bundle is up to date, skipping compile."
  exit 0
fi

ARCH="${OPENCHRONICLE_PRIVACY_OVERLAY_ARCH:-$(uname -m)}"
case "${ARCH}" in
  arm64|x86_64)
    TARGET="${ARCH}-apple-macos12.0"
    ;;
  *)
    echo "[mac-privacy-overlay] Unsupported build architecture." >&2
    exit 1
    ;;
esac

CACHE_DIR="${CLANG_MODULE_CACHE_PATH:-/private/tmp/openchronicle-clang-cache}"
mkdir -p "${CACHE_DIR}"

APP_PARENT="$(dirname "${APP_DIR}")"
mkdir -p "${APP_PARENT}"
TEMP_DIR="$(mktemp -d "${APP_PARENT}/.privacy-overlay-build.XXXXXX")"
TEMP_APP="${TEMP_DIR}/OpenChroniclePrivacyOverlay.app"
TEMP_BINARY="${TEMP_APP}/Contents/MacOS/mac-privacy-overlay"
BACKUP_APP=""
cleanup() {
  rm -rf -- "${TEMP_DIR}"
  if [[ -n "${BACKUP_APP}" && -e "${BACKUP_APP}" ]]; then
    rm -rf -- "${BACKUP_APP}"
  fi
}
trap cleanup EXIT

mkdir -p "${TEMP_APP}/Contents/MacOS"
install -m 0644 "${INFO}" "${TEMP_APP}/Contents/Info.plist"

echo "[mac-privacy-overlay] Compiling ${APP_OUT}"
if ! CLANG_MODULE_CACHE_PATH="${CACHE_DIR}" swiftc \
     "${REASON}" "${CORE}" "${MAIN}" \
     -o "${TEMP_BINARY}" \
     -O -warnings-as-errors -target "${TARGET}" -swift-version 5 -framework AppKit; then
  echo "[mac-privacy-overlay] swiftc failed." >&2
  echo "[mac-privacy-overlay] Install Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi

xattr -cr "${TEMP_APP}"
codesign --force --deep --sign - --timestamp=none "${TEMP_APP}"
codesign --verify --deep --strict "${TEMP_APP}"

if [[ -e "${APP_DIR}" ]]; then
  BACKUP_APP="${APP_PARENT}/.OpenChroniclePrivacyOverlay.backup.$$"
  mv "${APP_DIR}" "${BACKUP_APP}"
fi
if ! mv "${TEMP_APP}" "${APP_DIR}"; then
  if [[ -n "${BACKUP_APP}" && -e "${BACKUP_APP}" ]]; then
    mv "${BACKUP_APP}" "${APP_DIR}"
    BACKUP_APP=""
  fi
  exit 1
fi
if [[ -n "${BACKUP_APP}" ]]; then
  rm -rf -- "${BACKUP_APP}"
  BACKUP_APP=""
fi

xattr -cr "${APP_DIR}"
codesign --force --deep --sign - --timestamp=none "${APP_DIR}"
codesign --verify --deep --strict "${APP_DIR}"

TEMP_BARE="${SCRIPT_DIR}/.mac-privacy-overlay.$$"
install -m 0755 "${APP_OUT}" "${TEMP_BARE}"
mv -f "${TEMP_BARE}" "${OUT}"

echo "[mac-privacy-overlay] Done."
