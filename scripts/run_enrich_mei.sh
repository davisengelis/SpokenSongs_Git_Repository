#!/bin/bash
set -euo pipefail

PROJECT_ROOT="/Users/davisengelis/SpokenSongs_CodeBook"
SCRIPT="$PROJECT_ROOT/scripts/enrich_mei_from_metadata.py"
INPUT_DIR="$PROJECT_ROOT/data/mei/raw_ms_export"
METADATA="$PROJECT_ROOT/metadata/LindaMetadata_1.xlsx"
OUTPUT_DIR="$PROJECT_ROOT/data/mei/enriched"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    PYTHON="python3"
fi

mkdir -p "$OUTPUT_DIR"

"$PYTHON" "$SCRIPT" \
    --input-dir "$INPUT_DIR" \
    --metadata "$METADATA" \
    --output-dir "$OUTPUT_DIR" \
    --overwrite \
    --log-level INFO
