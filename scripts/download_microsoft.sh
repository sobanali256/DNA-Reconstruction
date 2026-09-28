#!/usr/bin/env bash
# Download the Microsoft Clustered Nanopore Reads (CNR) dataset at its pinned commit
# into data/raw/microsoft_cnr/ and verify the file checksums.
#
#   bash scripts/download_microsoft.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/external_pins.env"
DEST="$ROOT/data/raw/microsoft_cnr"
mkdir -p "$ROOT/data/raw"

if [ ! -d "$DEST/.git" ]; then
    git clone --quiet "$MSCNR_URL" "$DEST"
fi
git -C "$DEST" fetch --quiet origin
git -C "$DEST" checkout --quiet "$MSCNR_COMMIT"

echo "== Verifying checksums"
(cd "$DEST" && sha256sum -c "$ROOT/data/splits/microsoft_cnr.sha256")
echo "== Microsoft CNR dataset ready at $DEST"
