#!/usr/bin/env bash
# Compile mac-window-list.swift into a native binary.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="${SCRIPT_DIR}/mac-window-list-core.swift"
SRC="${SCRIPT_DIR}/mac-window-list.swift"
OUT="${SCRIPT_DIR}/mac-window-list"

if [[ ! -f "${CORE}" || ! -f "${SRC}" ]]; then
  echo "[mac-window-list] Source not found." >&2
  exit 1
fi

if [[ -f "${OUT}" && "${OUT}" -nt "${CORE}" && "${OUT}" -nt "${SRC}" ]]; then
  echo "[mac-window-list] Binary is up to date, skipping compile."
  exit 0
fi

ARCH=$(uname -m)
if [[ "${ARCH}" == "arm64" ]]; then
  TARGET="arm64-apple-macos12.0"
else
  TARGET="x86_64-apple-macos12.0"
fi

CACHE_DIR="/tmp/clang-module-cache"
mkdir -p "${CACHE_DIR}"

echo "[mac-window-list] Compiling ${SRC} -> ${OUT}"
if ! CLANG_MODULE_CACHE_PATH="${CACHE_DIR}" swiftc \
     "${CORE}" "${SRC}" -o "${OUT}" -O -target "${TARGET}" -swift-version 5; then
  echo "[mac-window-list] swiftc failed." >&2
  echo "[mac-window-list] Install Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi

echo "[mac-window-list] Done."
