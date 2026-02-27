#!/usr/bin/env bash
# Collect training data for the repotoire GBDT classifier.
#
# Clones ~15 diverse repos across all 9 supported languages,
# runs `repotoire analyze --export-training` on each, and merges
# the results into a single training file.
#
# Usage:
#   ./scripts/collect_training_data.sh [--output DIR]
#
# Requirements:
#   - repotoire binary on PATH (or cargo build first)
#   - git

set -euo pipefail

OUTPUT_DIR="${1:-scripts/training_data}"
TEMP_DIR=$(mktemp -d -t repotoire-training-XXXXXX)

echo "=== Repotoire Training Data Collection ==="
echo "Temp directory: $TEMP_DIR"
echo "Output directory: $OUTPUT_DIR"
echo ""

# Ensure repotoire is available
if ! command -v repotoire &>/dev/null; then
    echo "repotoire not found on PATH, building..."
    cargo build --release -p repotoire 2>/dev/null
    REPOTOIRE="./target/release/repotoire"
else
    REPOTOIRE="repotoire"
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$TEMP_DIR/data"

# Repo list: ~15 repos across 9 languages
declare -A REPOS=(
    # Python
    ["flask"]="https://github.com/pallets/flask"
    ["fastapi"]="https://github.com/tiangolo/fastapi"
    ["httpx"]="https://github.com/encode/httpx"
    # TypeScript
    ["express"]="https://github.com/expressjs/express"
    ["date-fns"]="https://github.com/date-fns/date-fns"
    # JavaScript
    ["axios"]="https://github.com/axios/axios"
    # Rust
    ["serde"]="https://github.com/serde-rs/serde"
    ["clap"]="https://github.com/clap-rs/clap"
    # Go
    ["gin"]="https://github.com/gin-gonic/gin"
    ["cobra"]="https://github.com/spf13/cobra"
    # Java
    ["guava"]="https://github.com/google/guava"
    # C#
    ["newtonsoft-json"]="https://github.com/JamesNK/Newtonsoft.Json"
    # C
    ["redis"]="https://github.com/redis/redis"
    ["jq"]="https://github.com/stedolan/jq"
    # C++
    ["fmt"]="https://github.com/fmtlib/fmt"
)

TOTAL=${#REPOS[@]}
CURRENT=0
FAILED=0
SUCCEEDED=0

for NAME in "${!REPOS[@]}"; do
    URL="${REPOS[$NAME]}"
    CURRENT=$((CURRENT + 1))
    REPO_DIR="$TEMP_DIR/repos/$NAME"

    echo "[$CURRENT/$TOTAL] Cloning $NAME from $URL..."

    if ! git clone --depth 500 --quiet "$URL" "$REPO_DIR" 2>/dev/null; then
        echo "  SKIP: clone failed for $NAME"
        FAILED=$((FAILED + 1))
        continue
    fi

    EXPORT_PATH="$TEMP_DIR/data/${NAME}.json"
    echo "  Analyzing $NAME..."

    if $REPOTOIRE analyze "$REPO_DIR" --export-training "$EXPORT_PATH" --log-level warn 2>/dev/null; then
        if [ -f "$EXPORT_PATH" ] && [ -s "$EXPORT_PATH" ]; then
            SAMPLE_COUNT=$(python3 -c "import json; print(len(json.load(open('$EXPORT_PATH'))))" 2>/dev/null || echo "?")
            echo "  OK: $SAMPLE_COUNT samples exported"
            SUCCEEDED=$((SUCCEEDED + 1))
        else
            echo "  SKIP: no training samples produced"
            FAILED=$((FAILED + 1))
        fi
    else
        echo "  SKIP: analysis failed for $NAME"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "=== Merging training data ==="

# Merge all JSON files into one
python3 -c "
import json, glob, sys, os

files = glob.glob('$TEMP_DIR/data/*.json')
merged = []
per_repo = {}

for f in sorted(files):
    name = os.path.splitext(os.path.basename(f))[0]
    try:
        with open(f) as fh:
            data = json.load(fh)
            merged.extend(data)
            per_repo[name] = len(data)
    except (json.JSONDecodeError, IOError) as e:
        print(f'  Warning: skipping {f}: {e}', file=sys.stderr)

# Write merged file
output_path = '$OUTPUT_DIR/merged.json'
with open(output_path, 'w') as fh:
    json.dump(merged, fh, indent=2)

# Statistics
tp_count = sum(1 for d in merged if d.get('is_tp'))
fp_count = len(merged) - tp_count
detectors = set(d.get('detector', '') for d in merged)

print(f'  Total samples: {len(merged)}')
print(f'  True positives: {tp_count}')
print(f'  False positives: {fp_count}')
print(f'  Unique detectors: {len(detectors)}')
print(f'  Repos contributing: {len(per_repo)}')
print()
print('  Per-repo breakdown:')
for name, count in sorted(per_repo.items(), key=lambda x: -x[1]):
    print(f'    {name}: {count} samples')
print()
print(f'  Merged file: {output_path}')
"

echo ""
echo "=== Summary ==="
echo "Repos processed: $CURRENT"
echo "Succeeded: $SUCCEEDED"
echo "Failed/skipped: $FAILED"
echo ""

# Cleanup temp repos (keep data in output dir)
rm -rf "$TEMP_DIR"

echo "Done. Training data saved to $OUTPUT_DIR/merged.json"
echo ""
echo "Next steps:"
echo "  uv run scripts/train_model.py --data $OUTPUT_DIR/merged.json --output repotoire-cli/models/seed_model.json"
