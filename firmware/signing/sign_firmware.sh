#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# sign_firmware.sh — Sign a compiled firmware binary with the ECDSA-P256 key.
#
# Run this on the OFFLINE signing machine after build is complete.
# The signing machine should never have network access while the key is loaded.
#
# Usage:
#   ./sign_firmware.sh <input_binary> <output_signed_binary>
#
# Example:
#   ./sign_firmware.sh build/boboloo.bin releases/1.2.3/boboloo-1.2.3-signed.bin
#
# Prerequisites:
#   - ESP-IDF environment activated (idf.py / espsecure.py on PATH)
#   - signing_key.pem present in the same directory as this script
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY="$SCRIPT_DIR/signing_key.pem"

INPUT="${1:-}"
OUTPUT="${2:-}"

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
    echo "Usage: $0 <input.bin> <output-signed.bin>" >&2
    exit 1
fi

if [[ ! -f "$KEY" ]]; then
    echo "ERROR: Signing key not found at $KEY" >&2
    echo "       Mount the encrypted USB and run from the correct directory." >&2
    exit 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "ERROR: Input binary not found: $INPUT" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

echo "Signing firmware..."
espsecure.py sign_data \
    --version 2 \
    --keyfile "$KEY" \
    --output "$OUTPUT" \
    "$INPUT"

# Compute and display SHA256 for registration via the backend API.
SHA256=$(sha256sum "$OUTPUT" | awk '{print $1}')
SIZE=$(stat -c%s "$OUTPUT" 2>/dev/null || stat -f%z "$OUTPUT")

echo ""
echo "Signed binary : $OUTPUT"
echo "SHA256        : $SHA256"
echo "Size          : $SIZE bytes"
echo ""
echo "Register this release:"
echo "  POST /sys/control/ota/releases"
echo "  {"
echo "    \"version\": \"<semver>\","
echo "    \"s3_key\": \"releases/<version>/$(basename "$OUTPUT")\","
echo "    \"sha256\": \"$SHA256\","
echo "    \"file_size\": $SIZE"
echo "  }"
echo ""
echo "Upload to S3 first:"
echo "  aws s3 cp $OUTPUT s3://\$S3_FIRMWARE_BUCKET/releases/<version>/$(basename "$OUTPUT")"
