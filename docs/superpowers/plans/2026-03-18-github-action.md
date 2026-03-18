# Reusable GitHub Action Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `Zach-hammad/repotoire-action@v1` — a composite GitHub Action that downloads the repotoire binary and runs analysis in 3 lines of YAML.

**Architecture:** Composite action (`action.yml` + bash scripts). Downloads prebuilt binary from GitHub Releases to `$RUNNER_TOOL_CACHE`, runs `repotoire analyze` or `repotoire diff`, produces SARIF + JSON output, sets GitHub step outputs (score, grade, findings count). SARIF upload is a separate user step via `github/codeql-action/upload-sarif@v3`.

**Tech Stack:** GitHub Actions (composite), bash, curl, jq

---

## File Structure

New repo: `Zach-hammad/repotoire-action`

| File | Responsibility |
|------|---------------|
| `action.yml` | Action metadata: inputs, outputs, composite steps |
| `scripts/install.sh` | Download + cache prebuilt binary |
| `scripts/analyze.sh` | Run analysis (full or diff mode) |
| `scripts/outputs.sh` | Parse JSON results, set GitHub outputs |
| `README.md` | Usage docs with copy-paste examples |
| `LICENSE` | MIT license |
| `.github/workflows/test.yml` | CI: test the action on sample code |

---

### Task 0: Add `--json-sidecar` Flag to Repotoire CLI

**Repo:** `repotoire` (main repo, not the action repo)
**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs` — add `--json-sidecar` CLI arg
- Modify: `repotoire-cli/src/cli/analyze/output.rs` — write JSON sidecar after primary output

- [ ] **Step 1: Add CLI argument**

In `repotoire-cli/src/cli/mod.rs`, add to the `Analyze` command args:

```rust
/// Write a JSON sidecar file alongside the primary format output.
/// Useful for CI actions that need structured data for outputs parsing
/// without running analysis twice.
#[arg(long)]
json_sidecar: Option<PathBuf>,
```

- [ ] **Step 2: Write JSON sidecar in format_and_output**

In `repotoire-cli/src/cli/analyze/output.rs`, after the primary output is written (around line 160, after `cache_results`), add:

```rust
// Write JSON sidecar if requested (single analysis run, two output files)
if let Some(sidecar_path) = json_sidecar {
    let json_report = {
        let mut full = report.clone();
        full.findings = all_findings.to_vec();
        full.findings_summary = FindingsSummary::from_findings(all_findings);
        full
    };
    let json_output = reporters::report(&json_report, "json")?;
    std::fs::write(&sidecar_path, &json_output)?;
}
```

Thread the `json_sidecar: Option<&Path>` parameter through from `run_engine()` → `format_and_output()`.

- [ ] **Step 3: Test**

```bash
cd repotoire-cli
cargo run -- analyze . --format sarif --output /tmp/test.sarif.json --json-sidecar /tmp/test-sidecar.json
# Verify both files exist
python3 -c "import json; d=json.load(open('/tmp/test-sidecar.json')); print(f'Score: {d[\"overall_score\"]}, Findings: {len(d[\"findings\"])}')"
```

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs repotoire-cli/src/cli/analyze/output.rs
git commit -m "feat: add --json-sidecar flag for CI actions (single analysis, two outputs)"
```

---

### Task 1: Create Repo + action.yml

**Files:**
- Create: `action.yml`

- [ ] **Step 1: Create the repotoire-action repo**

```bash
mkdir -p ~/personal/repotoire-action
cd ~/personal/repotoire-action
git init
```

- [ ] **Step 2: Create action.yml**

```yaml
name: 'Repotoire Code Analysis'
description: 'Graph-powered code health analysis. 107 detectors, 9 languages, one binary.'
author: 'Zach-hammad'

branding:
  icon: 'shield'
  color: 'blue'

inputs:
  version:
    description: 'Repotoire version to install (e.g., "v0.3.113" or "latest")'
    required: false
    default: 'latest'
  path:
    description: 'Path to the repository/directory to analyze'
    required: false
    default: '.'
  format:
    description: 'Output format: sarif, json, text, markdown'
    required: false
    default: 'sarif'
  fail-on:
    description: 'Fail if any finding meets this severity: critical, high, medium, low. Empty = do not fail.'
    required: false
    default: ''
  diff-only:
    description: 'Only analyze diff vs base. "auto" = diff on PRs, full on push.'
    required: false
    default: 'auto'
  config:
    description: 'Path to repotoire.toml config file. Empty = auto-detect.'
    required: false
    default: ''
  args:
    description: 'Additional CLI arguments passed to repotoire analyze'
    required: false
    default: ''

outputs:
  score:
    description: 'Overall health score (0-100)'
    value: ${{ steps.parse-outputs.outputs.score }}
  grade:
    description: 'Letter grade (A+ through F)'
    value: ${{ steps.parse-outputs.outputs.grade }}
  findings-count:
    description: 'Total number of findings'
    value: ${{ steps.parse-outputs.outputs.findings-count }}
  critical-count:
    description: 'Critical severity findings'
    value: ${{ steps.parse-outputs.outputs.critical-count }}
  high-count:
    description: 'High severity findings'
    value: ${{ steps.parse-outputs.outputs.high-count }}
  sarif-file:
    description: 'Path to the SARIF output file'
    value: ${{ steps.analyze.outputs.sarif-file }}
  json-file:
    description: 'Path to the JSON output file'
    value: ${{ steps.analyze.outputs.json-file }}
  exit-code:
    description: 'Repotoire exit code (0 = pass, 1 = fail-on triggered)'
    value: ${{ steps.analyze.outputs.exit-code }}

runs:
  using: 'composite'
  steps:
    - name: Install Repotoire
      id: install
      shell: bash
      run: ${{ github.action_path }}/scripts/install.sh
      env:
        INPUT_VERSION: ${{ inputs.version }}

    - name: Run Analysis
      id: analyze
      shell: bash
      run: ${{ github.action_path }}/scripts/analyze.sh
      env:
        INPUT_PATH: ${{ inputs.path }}
        INPUT_FORMAT: ${{ inputs.format }}
        INPUT_FAIL_ON: ${{ inputs.fail-on }}
        INPUT_DIFF_ONLY: ${{ inputs.diff-only }}
        INPUT_CONFIG: ${{ inputs.config }}
        INPUT_ARGS: ${{ inputs.args }}

    - name: Parse Outputs
      id: parse-outputs
      shell: bash
      run: ${{ github.action_path }}/scripts/outputs.sh
      if: always()
```

- [ ] **Step 3: Verify action.yml is valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('action.yml'))" && echo "Valid"
```

- [ ] **Step 4: Commit**

```bash
git add action.yml
git commit -m "feat: add action.yml with inputs, outputs, and composite steps"
```

---

### Task 2: Binary Installation Script

**Files:**
- Create: `scripts/install.sh`

- [ ] **Step 1: Create scripts directory and install.sh**

```bash
mkdir -p scripts
```

Create `scripts/install.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

VERSION="${INPUT_VERSION:-latest}"

# Determine platform
case "${RUNNER_OS:-Linux}" in
  Linux)
    PLATFORM="linux-x86_64"
    EXT="tar.gz"
    ;;
  macOS)
    case "${RUNNER_ARCH:-X64}" in
      ARM64) PLATFORM="macos-aarch64" ;;
      *)     PLATFORM="macos-x86_64" ;;
    esac
    EXT="tar.gz"
    ;;
  *)
    echo "::error::Unsupported platform: ${RUNNER_OS}. Only Linux and macOS are supported."
    exit 1
    ;;
esac

# Resolve 'latest' to actual version tag
if [ "$VERSION" = "latest" ]; then
  echo "::group::Resolving latest version"
  VERSION=$(curl -sfL \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/Zach-hammad/repotoire/releases/latest \
    | jq -r .tag_name)
  if [ -z "$VERSION" ] || [ "$VERSION" = "null" ]; then
    echo "::error::Failed to resolve latest version from GitHub API"
    exit 1
  fi
  echo "Resolved latest version: $VERSION"
  echo "::endgroup::"
fi

# Check cache
INSTALL_DIR="${RUNNER_TOOL_CACHE:-/tmp}/repotoire/${VERSION}"
if [ -x "$INSTALL_DIR/repotoire" ]; then
  echo "Using cached repotoire $VERSION"
  echo "$INSTALL_DIR" >> "$GITHUB_PATH"
  exit 0
fi

# Download
echo "::group::Installing repotoire $VERSION ($PLATFORM)"
DOWNLOAD_URL="https://github.com/Zach-hammad/repotoire/releases/download/${VERSION}/repotoire-${PLATFORM}.${EXT}"
echo "Downloading: $DOWNLOAD_URL"

mkdir -p "$INSTALL_DIR"
HTTP_CODE=$(curl -sfL -w "%{http_code}" -o /tmp/repotoire-download.${EXT} "$DOWNLOAD_URL" || true)

if [ "$HTTP_CODE" != "200" ]; then
  # Retry once
  echo "First download attempt failed (HTTP $HTTP_CODE), retrying..."
  sleep 2
  HTTP_CODE=$(curl -sfL -w "%{http_code}" -o /tmp/repotoire-download.${EXT} "$DOWNLOAD_URL" || true)
  if [ "$HTTP_CODE" != "200" ]; then
    echo "::error::Failed to download repotoire $VERSION for $PLATFORM (HTTP $HTTP_CODE). Check that the version exists at: $DOWNLOAD_URL"
    exit 1
  fi
fi

# Extract
tar xzf /tmp/repotoire-download.${EXT} -C "$INSTALL_DIR"
rm -f /tmp/repotoire-download.${EXT}

# Verify binary
if [ ! -x "$INSTALL_DIR/repotoire" ]; then
  # Binary might be nested in a directory
  FOUND=$(find "$INSTALL_DIR" -name "repotoire" -type f | head -1)
  if [ -n "$FOUND" ]; then
    mv "$FOUND" "$INSTALL_DIR/repotoire"
    chmod +x "$INSTALL_DIR/repotoire"
  else
    echo "::error::Downloaded archive did not contain a 'repotoire' binary"
    exit 1
  fi
fi

echo "$INSTALL_DIR" >> "$GITHUB_PATH"
echo "Installed repotoire $VERSION to $INSTALL_DIR"
"$INSTALL_DIR/repotoire" version
echo "::endgroup::"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/install.sh
```

- [ ] **Step 3: Test locally (smoke test)**

```bash
# Create mock GitHub env files
touch /tmp/test-github-path /tmp/test-github-output
RUNNER_OS=Linux RUNNER_TOOL_CACHE=/tmp/test-cache GITHUB_PATH=/tmp/test-github-path INPUT_VERSION=latest bash scripts/install.sh
# Verify binary was downloaded
ls -la /tmp/test-cache/repotoire/*/repotoire
```

Note: The binary won't be on PATH locally since `$GITHUB_PATH` is only consumed by the GitHub Actions runner. The test just verifies download + extraction works.

- [ ] **Step 4: Commit**

```bash
git add scripts/install.sh
git commit -m "feat: add binary installation script with caching and retry"
```

---

### Task 3: Analysis Script

**Files:**
- Create: `scripts/analyze.sh`

- [ ] **Step 1: Create analyze.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${INPUT_PATH:-.}"
FORMAT="${INPUT_FORMAT:-sarif}"
FAIL_ON="${INPUT_FAIL_ON:-}"
DIFF_ONLY="${INPUT_DIFF_ONLY:-auto}"
CONFIG="${INPUT_CONFIG:-}"
EXTRA_ARGS="${INPUT_ARGS:-}"

# Warn if shallow clone
if [ -d "$REPO_PATH/.git" ]; then
  DEPTH=$(git -C "$REPO_PATH" rev-list --count --all 2>/dev/null || echo "0")
  if [ "$DEPTH" -lt 10 ]; then
    echo "::warning::Shallow clone detected ($DEPTH commits). Use 'fetch-depth: 0' in checkout for full git analysis (churn, blame, co-change)."
  fi
fi

# Build output paths
OUTPUT_DIR="${RUNNER_TEMP:-/tmp}/repotoire-results"
mkdir -p "$OUTPUT_DIR"
SARIF_FILE="$OUTPUT_DIR/results.sarif.json"
JSON_FILE="$OUTPUT_DIR/results.json"

# Build command
CMD_ARGS=()

# Determine mode: diff or full analysis
if [ "$DIFF_ONLY" = "auto" ]; then
  if [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ] || [ "${GITHUB_EVENT_NAME:-}" = "pull_request_target" ]; then
    DIFF_ONLY="true"
  else
    DIFF_ONLY="false"
  fi
fi

if [ "$DIFF_ONLY" = "true" ] && [ -n "${GITHUB_EVENT_PATH:-}" ]; then
  BASE_SHA=$(jq -r '.pull_request.base.sha // empty' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  if [ -n "$BASE_SHA" ]; then
    echo "Running diff analysis against base: $BASE_SHA"
    CMD_ARGS+=(diff "$BASE_SHA" --path "$REPO_PATH")
    CMD_ARGS+=(--format "$FORMAT" --output "$SARIF_FILE")
  else
    echo "::warning::Could not determine base SHA for diff. Falling back to full analysis."
    CMD_ARGS+=(analyze "$REPO_PATH" --format "$FORMAT" --output "$SARIF_FILE")
  fi
else
  CMD_ARGS+=(analyze "$REPO_PATH" --format "$FORMAT" --output "$SARIF_FILE")
fi

# Add fail-on if specified
if [ -n "$FAIL_ON" ]; then
  CMD_ARGS+=(--fail-on "$FAIL_ON")
fi

# Add config if specified
if [ -n "$CONFIG" ]; then
  CMD_ARGS+=(--config "$CONFIG")
fi

# Add per-page 0 for full output in structured formats
if [ "$FORMAT" = "sarif" ] || [ "$FORMAT" = "json" ]; then
  CMD_ARGS+=(--per-page 0)
fi

# Add extra args (word-split intentionally)
if [ -n "$EXTRA_ARGS" ]; then
  # shellcheck disable=SC2206
  CMD_ARGS+=($EXTRA_ARGS)
fi

# Use --json-sidecar to produce JSON alongside the primary format in a single
# analysis run. This avoids running analysis twice when format is sarif/text/etc.
CMD_ARGS+=(--json-sidecar "$JSON_FILE")

echo "::group::Repotoire Analysis"
echo "Command: repotoire ${CMD_ARGS[*]}"

# Run analysis once — produces both the primary format output and a JSON sidecar
EXIT_CODE=0
repotoire "${CMD_ARGS[@]}" || EXIT_CODE=$?

echo "::endgroup::"

# Set outputs
echo "sarif-file=$SARIF_FILE" >> "$GITHUB_OUTPUT"
echo "json-file=$JSON_FILE" >> "$GITHUB_OUTPUT"
echo "exit-code=$EXIT_CODE" >> "$GITHUB_OUTPUT"

# Fail the step if fail-on was triggered
if [ -n "$FAIL_ON" ] && [ "$EXIT_CODE" -ne 0 ]; then
  echo "::error::Repotoire found findings at or above '$FAIL_ON' severity (exit code $EXIT_CODE)"
  exit "$EXIT_CODE"
fi
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/analyze.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/analyze.sh
git commit -m "feat: add analysis script with diff mode, fail-on, and JSON output"
```

---

### Task 4: Outputs Parser Script

**Files:**
- Create: `scripts/outputs.sh`

- [ ] **Step 1: Create outputs.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

JSON_FILE="${RUNNER_TEMP:-/tmp}/repotoire-results/results.json"

if [ ! -f "$JSON_FILE" ]; then
  echo "::warning::No JSON results file found at $JSON_FILE — skipping output parsing"
  echo "score=0" >> "$GITHUB_OUTPUT"
  echo "grade=?" >> "$GITHUB_OUTPUT"
  echo "findings-count=0" >> "$GITHUB_OUTPUT"
  echo "critical-count=0" >> "$GITHUB_OUTPUT"
  echo "high-count=0" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Parse results
SCORE=$(jq -r '.overall_score // 0' "$JSON_FILE")
GRADE=$(jq -r '.grade // "?"' "$JSON_FILE")
TOTAL=$(jq -r '.findings | length' "$JSON_FILE")
CRITICAL=$(jq -r '[.findings[] | select(.severity == "critical")] | length' "$JSON_FILE")
HIGH=$(jq -r '[.findings[] | select(.severity == "high")] | length' "$JSON_FILE")

# Round score to 1 decimal
SCORE=$(printf "%.1f" "$SCORE")

echo "score=$SCORE" >> "$GITHUB_OUTPUT"
echo "grade=$GRADE" >> "$GITHUB_OUTPUT"
echo "findings-count=$TOTAL" >> "$GITHUB_OUTPUT"
echo "critical-count=$CRITICAL" >> "$GITHUB_OUTPUT"
echo "high-count=$HIGH" >> "$GITHUB_OUTPUT"

# Summary
echo "### Repotoire Analysis" >> "$GITHUB_STEP_SUMMARY"
echo "" >> "$GITHUB_STEP_SUMMARY"
echo "| Metric | Value |" >> "$GITHUB_STEP_SUMMARY"
echo "|--------|-------|" >> "$GITHUB_STEP_SUMMARY"
echo "| Score | $SCORE ($GRADE) |" >> "$GITHUB_STEP_SUMMARY"
echo "| Findings | $TOTAL |" >> "$GITHUB_STEP_SUMMARY"
echo "| Critical | $CRITICAL |" >> "$GITHUB_STEP_SUMMARY"
echo "| High | $HIGH |" >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/outputs.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/outputs.sh
git commit -m "feat: add outputs parser with GitHub step summary"
```

---

### Task 5: README

**Files:**
- Create: `README.md`
- Create: `LICENSE`

- [ ] **Step 1: Create README.md**

Write README with:
- Header with badges (GitHub Action version, repotoire version, license)
- Quick start (minimal 3-line example)
- Full example with Code Scanning upload
- PR workflow example (recommended setup)
- All inputs table with descriptions and defaults
- All outputs table
- Using outputs in downstream steps example:
```yaml
- uses: Zach-hammad/repotoire-action@v1
  id: repotoire
- run: |
    echo "Score: ${{ steps.repotoire.outputs.score }}"
    echo "Grade: ${{ steps.repotoire.outputs.grade }}"
    if [ "${{ steps.repotoire.outputs.critical-count }}" -gt 0 ]; then
      echo "::error::Critical findings detected!"
    fi
```
- Troubleshooting section (shallow clone warning, permissions for SARIF upload)
- Link to repotoire main repo

- [ ] **Step 2: Create LICENSE**

MIT license matching the main repotoire repo.

- [ ] **Step 3: Commit**

```bash
git add README.md LICENSE
git commit -m "docs: add README with usage examples and LICENSE"
```

---

### Task 6: CI Workflow (Test the Action)

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1: Create test workflow**

```yaml
name: Test Action

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test-basic:
    name: Basic Analysis
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      # Create a sample file to analyze
      - name: Create test fixture
        run: |
          mkdir -p test-repo
          cat > test-repo/app.py << 'PYEOF'
          import os
          def run_command(user_input):
              os.system(user_input)
              eval(user_input)
          PYEOF
          # Strip leading whitespace from heredoc (YAML indentation)
          sed -i 's/^          //' test-repo/app.py

      - name: Run Repotoire Action
        id: repotoire
        uses: ./
        with:
          path: test-repo
          format: sarif

      - name: Verify outputs
        run: |
          echo "Score: ${{ steps.repotoire.outputs.score }}"
          echo "Grade: ${{ steps.repotoire.outputs.grade }}"
          echo "Findings: ${{ steps.repotoire.outputs.findings-count }}"
          echo "SARIF: ${{ steps.repotoire.outputs.sarif-file }}"

          # Score should be a number
          [[ "${{ steps.repotoire.outputs.score }}" =~ ^[0-9]+\.?[0-9]*$ ]] || { echo "::error::Score is not a number"; exit 1; }

          # SARIF file should exist
          [ -f "${{ steps.repotoire.outputs.sarif-file }}" ] || { echo "::error::SARIF file not found"; exit 1; }

  test-fail-on:
    name: Fail-On Threshold
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Create vulnerable file
        run: |
          mkdir -p test-repo
          cat > test-repo/vuln.py << 'PYEOF'
          import os
          def handler(request):
              os.system(request.data)
          PYEOF
          sed -i 's/^          //' test-repo/vuln.py

      - name: Run with fail-on (expect failure)
        id: repotoire
        uses: ./
        with:
          path: test-repo
          fail-on: low
        continue-on-error: true

      - name: Verify it failed
        run: |
          if [ "${{ steps.repotoire.outputs.exit-code }}" = "0" ]; then
            echo "::error::Expected non-zero exit code with fail-on: low"
            exit 1
          fi
          echo "Correctly failed with exit code ${{ steps.repotoire.outputs.exit-code }}"

  test-version-pin:
    name: Version Pinning
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install specific version
        uses: ./
        with:
          version: 'v0.3.113'
          path: '.'

  test-macos:
    name: macOS Compatibility
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run on macOS
        uses: ./
        with:
          path: '.'
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add test workflow for the action"
```

---

### Task 7: Publish v1

- [ ] **Step 1: Push to GitHub**

```bash
cd ~/personal/repotoire-action
gh repo create Zach-hammad/repotoire-action --public --source=. --push
```

- [ ] **Step 2: Verify CI passes**

Check https://github.com/Zach-hammad/repotoire-action/actions — all 4 test jobs should pass.

- [ ] **Step 3: Tag v1**

```bash
# 1. Create and push the versioned tag (triggers GitHub Release)
git tag -a v1.0.0 -m "v1.0.0: Initial release"
git push origin v1.0.0

# 2. Create GitHub Release from the tag (via gh CLI or GitHub UI)
gh release create v1.0.0 --title "v1.0.0" --notes "Initial release of repotoire-action"

# 3. Create floating major-version tag (what users reference as @v1)
git tag -a v1 -m "v1 major version tag"
git push origin v1
```

On future releases, update the floating `v1` tag:
```bash
git tag -f v1
git push -f origin v1
```

- [ ] **Step 4: Test from another repo**

Create a test workflow in the main repotoire repo:
```yaml
# .github/workflows/test-action.yml
name: Test Repotoire Action
on: workflow_dispatch
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Zach-hammad/repotoire-action@v1
        with:
          fail-on: critical
```

Run it manually and verify it works end-to-end.

- [ ] **Step 5: Update main repo README**

Add a "CI/CD" section to `repotoire/README.md`:

```markdown
## CI/CD Integration

```yaml
# .github/workflows/code-health.yml
name: Code Health
on: [pull_request, push]
permissions:
  security-events: write
  contents: read
jobs:
  repotoire:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Zach-hammad/repotoire-action@v1
        id: repotoire
        with:
          fail-on: high
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: ${{ steps.repotoire.outputs.sarif-file }}
```
```

- [ ] **Step 6: Commit README update**

```bash
cd ~/personal/repotoire
git add README.md
git commit -m "docs: add GitHub Action CI/CD integration to README"
```

---

## Task Dependencies

```
Task 0 (--json-sidecar in main repo) ──→ Task 1 (action.yml) ──→ Task 2 (install.sh) ──→ Task 3 (analyze.sh) ──→ Task 4 (outputs.sh) ──→ Task 5 (README) ──→ Task 6 (CI) ──→ Task 7 (publish)
```

Sequential. Task 0 is in the main repotoire repo (prerequisite CLI change). Tasks 1-7 are in the new `repotoire-action` repo.
