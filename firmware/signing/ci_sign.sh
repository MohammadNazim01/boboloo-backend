#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ci_sign.sh — CI/CD signing via AWS KMS (future path, no raw key in CI).
#
# When the signing key is managed in AWS KMS (recommended for production CI):
#   - The KMS key never leaves AWS hardware.
#   - CI holds only the KMS key ARN and an IAM role that allows kms:Sign.
#   - The signature is retrieved from KMS and attached to the binary.
#
# This script is a TEMPLATE.  It requires aws-espsecure-kms or a custom
# signer that bridges espsecure.py with AWS KMS.
#
# Usage (in CI pipeline):
#   export KMS_KEY_ARN="arn:aws:kms:us-east-1:123456789:key/mrk-xxxx"
#   export VERSION="1.2.3"
#   ./firmware/signing/ci_sign.sh build/boboloo.bin releases/$VERSION/boboloo-$VERSION-signed.bin
#
# If you are on batch 1 and using the offline signing machine instead,
# use sign_firmware.sh — NOT this script.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INPUT="${1:-}"
OUTPUT="${2:-}"
KMS_KEY_ARN="${KMS_KEY_ARN:-}"
VERSION="${VERSION:-unknown}"

if [[ -z "$INPUT" || -z "$OUTPUT" || -z "$KMS_KEY_ARN" ]]; then
    echo "Usage: KMS_KEY_ARN=<arn> VERSION=<ver> $0 <input.bin> <output-signed.bin>" >&2
    exit 1
fi

echo "CI signing via AWS KMS: $KMS_KEY_ARN"

# ── Step 1: Compute SHA256 of the unsigned binary ────────────────────────────
HASH_HEX=$(sha256sum "$INPUT" | awk '{print $1}')
HASH_B64=$(echo "$HASH_HEX" | xxd -r -p | base64)

echo "Input SHA256: $HASH_HEX"

# ── Step 2: Request ECDSA signature from KMS ─────────────────────────────────
# KMS ECDSA_SHA_256 signs the raw SHA256 digest (not the message).
SIGNATURE_B64=$(aws kms sign \
    --key-id "$KMS_KEY_ARN" \
    --message "$HASH_B64" \
    --message-type DIGEST \
    --signing-algorithm ECDSA_SHA_256 \
    --query 'Signature' \
    --output text)

echo "KMS signature obtained ($(echo "$SIGNATURE_B64" | wc -c) bytes base64)"

# ── Step 3: Attach signature to firmware using espsecure.py ──────────────────
# NOTE: espsecure.py does not natively support KMS-provided signatures.
# You need a wrapper that:
#   1. Calls espsecure.py's internal image signing with a pre-computed signature.
#   2. Or uses a custom signing plugin.
#
# This is a placeholder.  Implement the KMS→espsecure bridge here, or use
# the offline signing machine (sign_firmware.sh) until this is resolved.
echo "TODO: attach KMS signature to firmware binary" >&2
echo "      See: https://github.com/espressif/esp-idf/issues/<open-issue>" >&2

# ── Step 4: Compute and output final SHA256 ───────────────────────────────────
SHA256=$(sha256sum "$OUTPUT" | awk '{print $1}' 2>/dev/null || echo "N/A")
echo "Signed binary SHA256: $SHA256"
