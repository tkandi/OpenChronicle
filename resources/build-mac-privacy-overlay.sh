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
OUT="${SCRIPT_DIR}/mac-privacy-overlay"

if [[ ! -f "${REASON}" || ! -f "${CORE}" || ! -f "${MAIN}" ]]; then
  echo "[mac-privacy-overlay] Source not found." >&2
  exit 1
fi

if [[ -f "${OUT}" && "${OUT}" -nt "${REASON}" && "${OUT}" -nt "${CORE}" && "${OUT}" -nt "${MAIN}" ]]; then
  echo "[mac-privacy-overlay] Binary is up to date, skipping compile."
  exit 0
fi

ARCH=$(uname -m)
if [[ "${ARCH}" == "arm64" ]]; then
  TARGET="arm64-apple-macos12.0"
else
  TARGET="x86_64-apple-macos12.0"
fi

CACHE_DIR="${CLANG_MODULE_CACHE_PATH:-/private/tmp/openchronicle-clang-cache}"
mkdir -p "${CACHE_DIR}"

echo "[mac-privacy-overlay] Compiling ${OUT}"
if ! CLANG_MODULE_CACHE_PATH="${CACHE_DIR}" swiftc \
     "${REASON}" "${CORE}" "${MAIN}" \
     -o "${OUT}" \
     -O -target "${TARGET}" -swift-version 5 -framework AppKit; then
  echo "[mac-privacy-overlay] swiftc failed." >&2
  echo "[mac-privacy-overlay] Install Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi

echo "[mac-privacy-overlay] Done."
