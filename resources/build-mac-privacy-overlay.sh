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

overlay_is_fresh() {
  [[ -x "${OUT}" && -x "${APP_OUT}" \
        && "${OUT}" -nt "${REASON}" && "${OUT}" -nt "${CORE}" && "${OUT}" -nt "${MAIN}" \
        && "${OUT}" -nt "${INFO}" && "${OUT}" -nt "$0" \
        && "${APP_OUT}" -nt "${REASON}" && "${APP_OUT}" -nt "${CORE}" \
        && "${APP_OUT}" -nt "${MAIN}" && "${APP_OUT}" -nt "${INFO}" \
        && "${APP_OUT}" -nt "$0" ]]
}

if [[ ! -f "${REASON}" || ! -f "${CORE}" || ! -f "${MAIN}" || ! -f "${INFO}" ]]; then
  echo "[mac-privacy-overlay] Source not found." >&2
  exit 1
fi

if overlay_is_fresh; then
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
LOCK_DIR="${APP_PARENT}/.privacy-overlay-build.lock"
LOCK_ACQUIRED=0
TEMP_DIR=""
TEMP_APP=""
TEMP_BINARY=""
BACKUP_APP=""
PUBLISHED_APP=0
PUBLISH_COMPLETE=0
TEMP_BARE=""
cleanup() {
  if [[ -n "${TEMP_DIR}" ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
  if [[ -n "${TEMP_BARE}" ]]; then
    rm -f -- "${TEMP_BARE}"
  fi
  if [[ "${PUBLISH_COMPLETE}" -ne 1 ]]; then
    if [[ "${PUBLISHED_APP}" -eq 1 ]]; then
      rm -rf -- "${APP_DIR}"
    fi
    if [[ -n "${BACKUP_APP}" && -e "${BACKUP_APP}" ]]; then
      if mv "${BACKUP_APP}" "${APP_DIR}"; then
        BACKUP_APP=""
      else
        echo "[mac-privacy-overlay] Could not restore the previous app; backup retained at ${BACKUP_APP}." >&2
      fi
    fi
  elif [[ -n "${BACKUP_APP}" && -e "${BACKUP_APP}" ]]; then
    rm -rf -- "${BACKUP_APP}"
  fi
  if [[ "${LOCK_ACQUIRED}" -eq 1 ]]; then
    rm -rf -- "${LOCK_DIR}"
  fi
}
trap cleanup EXIT

for _ in {1..120}; do
  if mkdir "${LOCK_DIR}" 2>/dev/null; then
    LOCK_ACQUIRED=1
    break
  fi
  sleep 0.1
done
if [[ "${LOCK_ACQUIRED}" -ne 1 ]]; then
  echo "[mac-privacy-overlay] Timed out waiting for the build lock." >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d "${APP_PARENT}/.privacy-overlay-build.XXXXXX")"
TEMP_APP="${TEMP_DIR}/OpenChroniclePrivacyOverlay.app"
TEMP_BINARY="${TEMP_APP}/Contents/MacOS/mac-privacy-overlay"

if overlay_is_fresh; then
  echo "[mac-privacy-overlay] App bundle became fresh while waiting, skipping compile."
  exit 0
fi

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
  exit 1
fi
PUBLISHED_APP=1

xattr -cr "${APP_DIR}"
codesign --force --deep --sign - --timestamp=none "${APP_DIR}"
codesign --verify --deep --strict "${APP_DIR}"

TEMP_BARE="${SCRIPT_DIR}/.mac-privacy-overlay.$$"
install -m 0755 "${APP_OUT}" "${TEMP_BARE}"
mv -f "${TEMP_BARE}" "${OUT}"
TEMP_BARE=""
PUBLISH_COMPLETE=1

echo "[mac-privacy-overlay] Done."
