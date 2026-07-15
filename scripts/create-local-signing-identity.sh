#!/usr/bin/env bash
set -euo pipefail

IDENTITY_NAME="${OPENCHRONICLE_LOCAL_SIGNING_IDENTITY:-OpenChronicle Local Development}"
ROOT_NAME="${IDENTITY_NAME} Root"
KEYCHAIN="${OPENCHRONICLE_SIGNING_KEYCHAIN:-$(security default-keychain -d user | tr -d ' \"')}"

EXISTING="$(security find-identity -v -p codesigning "${KEYCHAIN}" 2>/dev/null || true)"
if awk -v needle="\"${IDENTITY_NAME}\"" 'index($0, needle) { found = 1 } END { exit !found }' \
  <<<"${EXISTING}"
then
  echo "Signing identity already exists: ${IDENTITY_NAME}"
  exit 0
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openchronicle-signing.XXXXXX")"
trap 'rm -rf "${WORK_DIR}"' EXIT
PASSWORD="$(openssl rand -hex 24)"
ROOT_ADDED=0
IDENTITY_IMPORTED=0

cleanup_keychain() {
  if [[ "${IDENTITY_IMPORTED}" == "1" ]]; then
    security delete-identity -Z "${IDENTITY_SHA1}" -t "${KEYCHAIN}" >/dev/null 2>&1 || true
  fi
  if [[ "${ROOT_ADDED}" == "1" ]]; then
    security delete-certificate -Z "${ROOT_SHA1}" -t "${KEYCHAIN}" >/dev/null 2>&1 || true
  fi
}

cleanup_on_error() {
  local status=$?
  trap - ERR
  cleanup_keychain
  exit "${status}"
}
trap cleanup_on_error ERR

openssl req -new -newkey rsa:3072 -nodes -x509 -sha256 -days 3650 \
  -subj "/CN=${ROOT_NAME}/O=OpenChronicle Local Development" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -keyout "${WORK_DIR}/root.key" \
  -out "${WORK_DIR}/root.crt"

openssl req -new -newkey rsa:3072 -nodes -sha256 \
  -subj "/CN=${IDENTITY_NAME}/O=OpenChronicle Local Development" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=critical,codeSigning" \
  -keyout "${WORK_DIR}/identity.key" \
  -out "${WORK_DIR}/identity.csr"

openssl x509 -req \
  -in "${WORK_DIR}/identity.csr" \
  -CA "${WORK_DIR}/root.crt" \
  -CAkey "${WORK_DIR}/root.key" \
  -CAcreateserial \
  -days 3650 \
  -sha256 \
  -copy_extensions copy \
  -out "${WORK_DIR}/identity.crt"

openssl pkcs12 -export \
  -inkey "${WORK_DIR}/identity.key" \
  -in "${WORK_DIR}/identity.crt" \
  -certfile "${WORK_DIR}/root.crt" \
  -name "${IDENTITY_NAME}" \
  -passout "pass:${PASSWORD}" \
  -out "${WORK_DIR}/identity.p12"

ROOT_SHA1="$(openssl x509 -in "${WORK_DIR}/root.crt" -noout -fingerprint -sha1 \
  | cut -d= -f2 | tr -d ':')"
IDENTITY_SHA1="$(openssl x509 -in "${WORK_DIR}/identity.crt" -noout -fingerprint -sha1 \
  | cut -d= -f2 | tr -d ':')"

ROOT_ADDED=1
security add-trusted-cert \
  -r trustRoot \
  -p codeSign \
  -k "${KEYCHAIN}" \
  "${WORK_DIR}/root.crt"

IDENTITY_IMPORTED=1
security import "${WORK_DIR}/identity.p12" \
  -k "${KEYCHAIN}" \
  -f pkcs12 \
  -P "${PASSWORD}" \
  -x \
  -T /usr/bin/codesign

UPDATED="$(security find-identity -v -p codesigning "${KEYCHAIN}" 2>/dev/null || true)"
if ! awk -v needle="\"${IDENTITY_NAME}\"" 'index($0, needle) { found = 1 } END { exit !found }' \
  <<<"${UPDATED}"
then
  echo "The certificate was imported, but macOS does not consider it a valid code-signing identity." >&2
  false
fi

trap - ERR

echo "Created stable local signing identity: ${IDENTITY_NAME}"
echo "The locally trusted root is restricted to code signing; its private key was not imported."
echo "Future builds select the leaf identity automatically. Keep its private key in your login keychain."
