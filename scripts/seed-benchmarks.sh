#!/usr/bin/env bash
# Seed PostHog with benchmark data from popular open-source repos.
# Runs repotoire analyze on each repo and sends telemetry events.
#
# Prerequisites:
#   - repotoire built and in PATH (cargo install --path repotoire-cli)
#   - Telemetry enabled: repotoire config telemetry on
#
# Usage:
#   ./scripts/seed-benchmarks.sh

set -euo pipefail

WORKDIR=$(mktemp -d)
trap "rm -rf $WORKDIR" EXIT

echo "Seeding benchmarks from open-source repos..."
echo "Working directory: $WORKDIR"
echo ""

# Popular repos across languages, varied sizes
REPOS=(
  # Rust
  "https://github.com/BurntSushi/ripgrep"
  "https://github.com/sharkdp/bat"
  "https://github.com/sharkdp/fd"
  "https://github.com/alacritty/alacritty"
  "https://github.com/starship/starship"
  "https://github.com/astral-sh/ruff"
  "https://github.com/tokio-rs/axum"
  "https://github.com/serde-rs/serde"
  "https://github.com/clap-rs/clap"
  "https://github.com/diesel-rs/diesel"
  "https://github.com/hyperium/hyper"
  "https://github.com/rust-lang/cargo"
  "https://github.com/nushell/nushell"
  "https://github.com/zellij-org/zellij"
  "https://github.com/helix-editor/helix"

  # Python
  "https://github.com/psf/requests"
  "https://github.com/pallets/flask"
  "https://github.com/django/django"
  "https://github.com/fastapi/fastapi"
  "https://github.com/pydantic/pydantic"
  "https://github.com/sqlalchemy/sqlalchemy"
  "https://github.com/psf/black"
  "https://github.com/python-poetry/poetry"
  "https://github.com/httpie/cli"
  "https://github.com/celery/celery"

  # TypeScript/JavaScript
  "https://github.com/microsoft/TypeScript"
  "https://github.com/vercel/next.js"
  "https://github.com/facebook/react"
  "https://github.com/expressjs/express"
  "https://github.com/prisma/prisma"
  "https://github.com/trpc/trpc"
  "https://github.com/t3-oss/create-t3-app"
  "https://github.com/shadcn-ui/ui"
  "https://github.com/tailwindlabs/tailwindcss"
  "https://github.com/vitejs/vite"

  # Go
  "https://github.com/junegunn/fzf"
  "https://github.com/jesseduffield/lazygit"
  "https://github.com/charmbracelet/bubbletea"
  "https://github.com/go-chi/chi"
  "https://github.com/gofiber/fiber"
  "https://github.com/gin-gonic/gin"
  "https://github.com/spf13/cobra"
  "https://github.com/gorilla/mux"
  "https://github.com/containerd/containerd"
  "https://github.com/prometheus/prometheus"

  # Java
  "https://github.com/spring-projects/spring-boot"
  "https://github.com/google/guava"
  "https://github.com/square/okhttp"
  "https://github.com/square/retrofit"
  "https://github.com/apache/kafka"

  # C/C++
  "https://github.com/redis/redis"
  "https://github.com/jqlang/jq"
  "https://github.com/curl/curl"
  "https://github.com/tmux/tmux"
  "https://github.com/git/git"

  # C#
  "https://github.com/dotnet/aspnetcore"
  "https://github.com/jellyfin/jellyfin"
)

TOTAL=${#REPOS[@]}
COUNT=0
FAILED=0

for REPO_URL in "${REPOS[@]}"; do
  COUNT=$((COUNT + 1))
  REPO_NAME=$(basename "$REPO_URL")
  CLONE_DIR="$WORKDIR/$REPO_NAME"

  echo "[$COUNT/$TOTAL] $REPO_NAME"

  # Shallow clone to save time/space
  if ! git clone --depth 1 --quiet "$REPO_URL" "$CLONE_DIR" 2>/dev/null; then
    echo "  SKIP: clone failed"
    FAILED=$((FAILED + 1))
    continue
  fi

  # Run repotoire analyze (telemetry sends automatically)
  if repotoire analyze "$CLONE_DIR" --format text > /dev/null 2>&1; then
    echo "  OK"
  else
    echo "  SKIP: analysis failed"
    FAILED=$((FAILED + 1))
  fi

  # Clean up clone to save disk
  rm -rf "$CLONE_DIR"
done

echo ""
echo "Done! Analyzed $((COUNT - FAILED))/$TOTAL repos."
echo "Events are in PostHog. Run the benchmark generator to publish:"
echo "  python scripts/generate-benchmarks.py"
