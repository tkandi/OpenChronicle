#!/usr/bin/env bash
# Compile mac-screen-capture into a native macOS helper.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="${SCRIPT_DIR}/mac-screen-capture-core.swift"
MAIN="${SCRIPT_DIR}/mac-screen-capture.swift"
OUT="${SCRIPT_DIR}/mac-screen-capture"

if [[ ! -f "${CORE}" || ! -f "${MAIN}" ]]; then
  echo "[mac-screen-capture] Source not found." >&2
  exit 1
fi

ARCH="$(uname -m)"
case "${ARCH}" in
  arm64|x86_64)
    TARGET="${ARCH}-apple-macos12.0"
    ;;
  *)
    echo "[mac-screen-capture] Unsupported build architecture." >&2
    exit 1
    ;;
esac

CACHE_DIR="${CLANG_MODULE_CACHE_PATH:-/private/tmp/openchronicle-clang-cache}"
mkdir -p "${CACHE_DIR}"

SDK_VERSION="${OPENCHRONICLE_MACOS_SDK_VERSION:-$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)}"
SDK_MAJOR="${SDK_VERSION%%.*}"
if [[ ! "${SDK_MAJOR}" =~ ^[0-9]+$ ]]; then
  echo "[mac-screen-capture] Could not determine the macOS SDK version." >&2
  exit 1
fi

SOURCES=("${CORE}" "${MAIN}")
TEMP_DIR=""
if (( SDK_MAJOR < 14 )); then
  TEMP_DIR="$(mktemp -d "${TMPDIR:-/private/tmp}/openchronicle-screen-capture.XXXXXX")"
  trap 'rm -rf "${TEMP_DIR}"' EXIT
  UNSUPPORTED_MAIN="${TEMP_DIR}/mac-screen-capture-unsupported.swift"
  cat > "${UNSUPPORTED_MAIN}" <<'SWIFT'
import Foundation

@main
enum MacScreenCaptureUnsupported {
    static func main() {
        _ = readLine()
        FileHandle.standardOutput.write(errorResponseLine(.unsupportedOS))
    }
}
SWIFT
  SOURCES=("${CORE}" "${UNSUPPORTED_MAIN}")
fi

echo "[mac-screen-capture] Compiling ${OUT}"
if ! CLANG_MODULE_CACHE_PATH="${CACHE_DIR}" swiftc \
     "${SOURCES[@]}" \
     -o "${OUT}" \
     -O -target "${TARGET}" -swift-version 5 \
     -framework ScreenCaptureKit \
     -framework ImageIO \
     -framework UniformTypeIdentifiers; then
  echo "[mac-screen-capture] swiftc failed." >&2
  echo "[mac-screen-capture] Install Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi

echo "[mac-screen-capture] Done."
