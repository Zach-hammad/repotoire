#!/bin/bash
# Train a universal GraphSAGE model on popular OSS Python repos
# Uses local FalkorDB (no cloud API)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR/.."
DATA_DIR="$HOME/.repotoire/training"
MODEL_DIR="$HOME/.repotoire/models"
OSS_DIR="$DATA_DIR/oss_repos"

# Force local FalkorDB mode
export FALKORDB_HOST=localhost
export FALKORDB_PORT=6379

mkdir -p "$DATA_DIR" "$MODEL_DIR" "$OSS_DIR"

echo "🚀 Universal Model Training Pipeline (Local FalkorDB)"
echo "======================================================="

# Activate venv
cd "$REPO_DIR"
source .venv/bin/activate

# Popular Python repos for training
REPOS=(
    "https://github.com/pallets/flask.git"
    "https://github.com/psf/requests.git"
    "https://github.com/tiangolo/fastapi.git"
)

# Step 1: Clone repos
echo ""
echo "📦 Step 1: Cloning OSS repositories..."
for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$OSS_DIR/$repo_name"
    
    if [ -d "$repo_path" ]; then
        echo "  ✓ $repo_name (already exists)"
    else
        echo "  → Cloning $repo_name..."
        git clone --depth 100 "$repo_url" "$repo_path" 2>/dev/null
    fi
done

# Step 2: Ingest each repo (Flask already done)
echo ""
echo "📊 Step 2: Ingesting repositories into graph..."
for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$OSS_DIR/$repo_name"
    
    echo "  → Ingesting $repo_name..."
    repotoire ingest "$repo_path" --quiet 2>&1 | tail -5 || true
done

# Step 3: Extract training data from all repos
echo ""
echo "📝 Step 3: Extracting training data from git history..."
PROJECT_PATHS=""
for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$OSS_DIR/$repo_name"
    if [ -n "$PROJECT_PATHS" ]; then
        PROJECT_PATHS="$PROJECT_PATHS,"
    fi
    PROJECT_PATHS="$PROJECT_PATHS$repo_path"
done

repotoire ml extract-multi-project-labels \
    -p "$PROJECT_PATHS" \
    -o "$DATA_DIR/oss_training_data.json" \
    --since 2023-01-01 \
    --max-commits 200 2>&1 | tail -20

# Step 4: Train bug predictor (RandomForest - faster, more robust)
echo ""
echo "🎓 Step 4: Training RandomForest bug predictor..."
repotoire ml train-bug-predictor \
    -d "$DATA_DIR/oss_training_data.json" \
    -o "$MODEL_DIR/bug_predictor.joblib" 2>&1 | tail -20

echo ""
echo "✅ Training complete!"
echo ""
echo "Model saved to: $MODEL_DIR/bug_predictor.joblib"
echo ""
echo "Usage:"
echo "  repotoire analyze /path/to/repo"
