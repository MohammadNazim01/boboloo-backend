#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gen_signing_key.sh — Generate the ECDSA-P256 firmware signing keypair.
#
# Run ONCE.  Store the private key in a hardware HSM or, at minimum, on an
# encrypted offline USB drive in a physical safe.  NEVER commit it to git.
#
# Usage:
#   ./gen_signing_key.sh
#
# Output:
#   signing_key.pem        — PRIVATE key  (KEEP SECRET, NEVER COMMIT)
#   signing_key_pub.pem    — Public key   (embed in firmware via sdkconfig)
#
# The public key is burned into eFuse during secure boot provisioning so the
# bootloader can reject unsigned firmware at boot time.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

KEY="signing_key.pem"
PUB="signing_key_pub.pem"

if [[ -f "$KEY" ]]; then
    echo "ERROR: $KEY already exists. Refusing to overwrite." >&2
    echo "       If you genuinely need a new key, delete the old one first." >&2
    exit 1
fi

echo "Generating ECDSA-P256 signing keypair..."

# espsecure.py is part of esp-idf — activate your IDF environment first.
espsecure.py generate_signing_key --version 2 "$KEY"

echo ""
echo "Private key : $KEY"
echo "Public key  : (embedded automatically by espsecure.py during signing)"
echo ""
echo "IMPORTANT:"
echo "  - Move $KEY to an encrypted offline USB drive immediately."
echo "  - Do NOT commit it to git."
echo "  - Store a backup in a separate physical location (e.g. co-founder safe)."
echo "  - The public key will be extracted from $KEY by sign_firmware.sh."
