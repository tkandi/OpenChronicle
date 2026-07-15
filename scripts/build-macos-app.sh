#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="${ROOT_DIR}/macos/OpenChronicleApp"
SCRATCH_DIR="${ROOT_DIR}/.build/macos-app"
OUTPUT_ARCHIVE="${ROOT_DIR}/dist/OpenChronicle.app.zip"
LEGACY_OUTPUT_APP_DIR="${ROOT_DIR}/dist/OpenChronicle.app"
SIGNING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openchronicle-app-signing.XXXXXX")"
APP_DIR="${SIGNING_DIR}/OpenChronicle.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
ICONSET_DIR="${SCRATCH_DIR}/OpenChronicle.iconset"
LOCAL_IDENTITY_NAME="${OPENCHRONICLE_LOCAL_SIGNING_IDENTITY:-OpenChronicle Local Development}"
IDENTITY="${CODE_SIGN_IDENTITY:-}"
LOCAL_IDENTITY_HASH=""

trap 'rm -rf "${SIGNING_DIR}"' EXIT

if [[ -z "${IDENTITY}" ]]; then
  IDENTITY_LIST="$(security find-identity -v -p codesigning 2>/dev/null || true)"
  LOCAL_IDENTITY_HASH="$(awk -v needle="\"${LOCAL_IDENTITY_NAME}\"" \
    'index($0, needle) { print $2; exit }' <<<"${IDENTITY_LIST}")"
  IDENTITY="${LOCAL_IDENTITY_HASH:--}"
fi

export CLANG_MODULE_CACHE_PATH="${SCRATCH_DIR}/clang-module-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="${SCRATCH_DIR}/swift-module-cache"

mkdir -p "${SCRATCH_DIR}" "${ROOT_DIR}/dist"

swift build \
  --package-path "${PACKAGE_DIR}" \
  --scratch-path "${SCRATCH_DIR}" \
  --configuration release

BIN_DIR="$(swift build \
  --package-path "${PACKAGE_DIR}" \
  --scratch-path "${SCRATCH_DIR}" \
  --configuration release \
  --show-bin-path)"

mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}" "${ICONSET_DIR}"

install -m 0755 "${BIN_DIR}/OpenChronicle" "${MACOS_DIR}/OpenChronicle"
install -m 0644 "${PACKAGE_DIR}/Info.plist" "${CONTENTS_DIR}/Info.plist"

if [[ "${IDENTITY}" == "-" ]]; then
  plutil -insert OpenChronicleSigningMode -string "ad-hoc" "${CONTENTS_DIR}/Info.plist"
else
  plutil -insert OpenChronicleSigningMode -string "stable" "${CONTENTS_DIR}/Info.plist"
fi

# Reuse the existing artwork. The source is landscape, so crop it to a centered
# square before generating the standard macOS icon sizes.
ICON_BASE="${SCRATCH_DIR}/OpenChronicle-1024.png"
sips --cropToHeightWidth 926 926 "${ROOT_DIR}/assets/logo.png" \
  --out "${ICON_BASE}" >/dev/null
sips --resampleHeightWidth 1024 1024 "${ICON_BASE}" \
  --out "${ICON_BASE}" >/dev/null

make_icon() {
  local pixels="$1"
  local output="$2"
  sips --resampleHeightWidth "${pixels}" "${pixels}" "${ICON_BASE}" \
    --out "${ICONSET_DIR}/${output}" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
make_icon 1024 icon_512x512@2x.png
iconutil --convert icns "${ICONSET_DIR}" --output "${RESOURCES_DIR}/OpenChronicle.icns"

plutil -lint "${CONTENTS_DIR}/Info.plist" >/dev/null
# Image assets copied from Finder may carry resource forks or quarantine metadata.
# Clear those only from the generated bundle; codesign rejects them.
xattr -cr "${APP_DIR}"

if [[ "${IDENTITY}" == "-" ]]; then
  codesign --force --deep --sign - --timestamp=none "${APP_DIR}"
else
  TIMESTAMP_OPTION="--timestamp"
  if [[ -n "${LOCAL_IDENTITY_HASH}" && "${IDENTITY}" == "${LOCAL_IDENTITY_HASH}" ]]; then
    TIMESTAMP_OPTION="--timestamp=none"
  fi
  codesign --force --deep --options runtime "${TIMESTAMP_OPTION}" \
    --sign "${IDENTITY}" "${APP_DIR}"
fi
codesign --verify --deep --strict --verbose=2 "${APP_DIR}"

if [[ -e "${LEGACY_OUTPUT_APP_DIR}" ]]; then
  rm -rf "${LEGACY_OUTPUT_APP_DIR}"
fi
if [[ -e "${OUTPUT_ARCHIVE}" ]]; then
  rm -f "${OUTPUT_ARCHIVE}"
fi
ditto -c -k --keepParent --norsrc --noextattr --noqtn --noacl \
  "${APP_DIR}" "${OUTPUT_ARCHIVE}"

VERIFY_DIR="${SIGNING_DIR}/verify"
mkdir -p "${VERIFY_DIR}"
ditto -x -k --norsrc --noextattr --noqtn --noacl \
  "${OUTPUT_ARCHIVE}" "${VERIFY_DIR}"
xattr -cr "${VERIFY_DIR}/OpenChronicle.app"
codesign --verify --deep --strict --verbose=2 "${VERIFY_DIR}/OpenChronicle.app"

echo "Built ${OUTPUT_ARCHIVE}"
if [[ "${IDENTITY}" == "-" ]]; then
  echo "Signing: ad hoc. Privacy permissions will stop matching after the next rebuild."
  echo "Run scripts/create-local-signing-identity.sh once, or set CODE_SIGN_IDENTITY to an Apple identity."
else
  echo "Signing: ${IDENTITY}"
fi
