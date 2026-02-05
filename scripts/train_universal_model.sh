#!/bin/bash
# Train a universal GraphSAGE model on popular OSS Python repos
# Run this with FalkorDB running

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR/.."
DATA_DIR="$HOME/.repotoire/training"
MODEL_DIR="$HOME/.repotoire/models"
OSS_DIR="$DATA_DIR/oss_repos"

mkdir -p "$DATA_DIR" "$MODEL_DIR" "$OSS_DIR"

echo "🚀 Universal Model Training Pipeline"
echo "======================================"

# Popular Python repos for training
REPOS=(
    "https://github.com/pallets/flask.git"
    "https://github.com/psf/requests.git"
    "https://github.com/tiangolo/fastapi.git"
    "https://github.com/encode/httpx.git"
    "https://github.com/aio-libs/aiohttp.git"
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

# Step 2: Ingest each repo
echo ""
echo "📊 Step 2: Ingesting repositories into graph..."
for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$OSS_DIR/$repo_name"
    
    echo "  → Ingesting $repo_name..."
    repotoire ingest "$repo_path" --quiet 2>/dev/null || true
done

# Step 3: Generate embeddings
echo ""
echo "🧠 Step 3: Generating Node2Vec embeddings..."
repotoire ml generate-embeddings --dimension 128 --walks-per-node 20

# Step 4: Extract training data from all repos
echo ""
echo "📝 Step 4: Extracting training data from git history..."
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
    --max-commits 500

# Step 5: Train GraphSAGE model
echo ""
echo "🎓 Step 5: Training GraphSAGE model..."
repotoire ml train-graphsage \
    -d "$DATA_DIR/oss_training_data.json" \
    -o "$MODEL_DIR/graphsage_universal.pt" \
    --epochs 50

# Step 6: Also train standard bug predictor
echo ""
echo "🎓 Step 6: Training RandomForest bug predictor..."
repotoire ml train-bug-predictor \
    -d "$DATA_DIR/oss_training_data.json" \
    -o "$MODEL_DIR/bug_predictor.joblib" \
    --grid-search

echo ""
echo "✅ Training complete!"
echo ""
echo "Models saved to:"
echo "  • $MODEL_DIR/graphsage_universal.pt (for zero-shot prediction)"
echo "  • $MODEL_DIR/bug_predictor.joblib (for standard prediction)"
echo ""
echo "Usage:"
echo "  # Zero-shot on new codebase:"
echo "  repotoire ml zero-shot-predict -m $MODEL_DIR/graphsage_universal.pt"
echo ""
echo "  # Standard analyze (auto-loads bug_predictor.joblib):"
echo "  repotoire analyze /path/to/repo"
