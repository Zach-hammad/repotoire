# Reusable GitHub Action Design

**Date:** 2026-03-18
**Status:** Draft
**Scope:** `Zach-hammad/repotoire-action@v1` — reusable GitHub Action for CI/CD integration

## Problem Statement

Using repotoire in CI requires building from source (3+ minutes) or manually scripting binary downloads. There's no `uses: repotoire-action@v1` that Just Works. This blocks adoption — most developers won't write a 50-line workflow to try a tool.

## Design

### What It Does

A composite GitHub Action that:
1. Downloads the prebuilt repotoire binary from GitHub Releases (5 seconds, not 3 minutes)
2. Runs analysis with configurable options
3. Uploads SARIF to GitHub Code Scanning (inline annotations in the Security tab)
4. Optionally fails the check on severity threshold
5. Outputs structured results for downstream steps

### User Experience

**Minimal (analysis only, no Code Scanning upload):**
```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: Zach-hammad/repotoire-action@v1
```

**With Code Scanning (adds SARIF upload step):**
```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: Zach-hammad/repotoire-action@v1
  id: repotoire
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with:
    sarif_file: ${{ steps.repotoire.outputs.sarif-file }}
    category: repotoire
```

Note: Composite actions cannot call other actions internally, so SARIF upload must be a separate step in the user's workflow.

**Full options:**
```yaml
- uses: Zach-hammad/repotoire-action@v1
  with:
    version: 'latest'           # or '0.3.113', default: 'latest'
    path: '.'                   # repo path to analyze, default: '.'
    format: 'sarif'             # output format, default: 'sarif'
    fail-on: 'high'             # fail if findings >= severity, default: '' (don't fail)
    diff-only: 'true'           # only analyze changed files (PR mode), default: 'true' on PRs
    upload-sarif: 'true'        # upload to Code Scanning, default: 'true'
    config: 'repotoire.toml'    # config file path, default: auto-detect
    args: '--top 50'            # additional CLI args, default: ''
```

**PR workflow (recommended):**
```yaml
name: Code Health
on:
  pull_request:
  push:
    branches: [main]

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
        with:
          fail-on: high
```

### Action Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `version` | `latest` | Repotoire version to install. `latest` fetches the newest release. |
| `path` | `.` | Path to the repository/directory to analyze. |
| `format` | `sarif` | Output format: `sarif`, `json`, `text`, `markdown` |
| `fail-on` | `` | Fail if any finding meets this severity: `critical`, `high`, `medium`, `low`. Empty = don't fail. |
| `diff-only` | `auto` | `true` = only analyze diff vs base. `false` = full analysis. `auto` = diff on PRs, full on push. |
| `upload-sarif` | `false` | *Deprecated in composite — user adds `upload-sarif` step manually.* Kept for future JS action migration. |
| `config` | `` | Path to `repotoire.toml`. Empty = auto-detect. |
| `args` | `` | Additional CLI arguments passed to `repotoire analyze`. |

### Action Outputs

| Output | Description |
|--------|-------------|
| `score` | Overall health score (0-100) |
| `grade` | Letter grade (A+ through F) |
| `findings-count` | Total number of findings |
| `critical-count` | Critical severity findings |
| `high-count` | High severity findings |
| `sarif-file` | Path to the SARIF output file |
| `json-file` | Path to the JSON output file (always produced for outputs) |
| `exit-code` | Repotoire exit code (0 = pass, 1 = fail-on triggered) |

### Action Type: Composite

Use a composite action (`action.yml` + shell scripts), not a Docker or JavaScript action. Reasons:
- No Docker pull overhead (composite runs directly on the runner)
- No Node.js runtime dependency
- Simple: just shell commands downloading a binary and running it
- Same approach as `cargo-binstall`, `rust-toolchain`, and similar Rust tool actions

### Binary Installation

```bash
# Determine platform
case "$RUNNER_OS" in
  Linux)  PLATFORM="linux-x86_64" ; EXT="tar.gz" ;;
  macOS)
    case "$RUNNER_ARCH" in
      ARM64) PLATFORM="macos-aarch64" ;;
      *)     PLATFORM="macos-x86_64" ;;
    esac
    EXT="tar.gz" ;;
  Windows) PLATFORM="windows-x86_64" ; EXT="zip" ;;  # v1: Linux/macOS only, Windows support later
esac

# Resolve version
if [ "$VERSION" = "latest" ]; then
  VERSION=$(curl -sL https://api.github.com/repos/Zach-hammad/repotoire/releases/latest | jq -r .tag_name)
fi

# Download and extract to runner tool cache (avoids sudo, works on self-hosted)
INSTALL_DIR="${RUNNER_TOOL_CACHE}/repotoire/${VERSION}"
mkdir -p "$INSTALL_DIR"
DOWNLOAD_URL="https://github.com/Zach-hammad/repotoire/releases/download/${VERSION}/repotoire-${PLATFORM}.${EXT}"
curl -sL "$DOWNLOAD_URL" | tar xz -C "$INSTALL_DIR"
echo "$INSTALL_DIR" >> "$GITHUB_PATH"
```

Cache the binary using `actions/cache` keyed on version to avoid re-downloading on repeat runs.

### Diff Mode (PR Analysis)

On `pull_request` events, the action runs `repotoire diff` instead of `repotoire analyze`:

```bash
if [ "$DIFF_ONLY" = "true" ] && [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then
  BASE_SHA=$(jq -r .pull_request.base.sha "$GITHUB_EVENT_PATH")
  repotoire diff "$BASE_SHA" --format sarif --output results.sarif.json
else
  repotoire analyze "$REPO_PATH" --format sarif --output results.sarif.json
fi
```

This gives developers findings only for code they changed, not the full repo debt.

### SARIF Upload

Composite actions cannot call other actions internally (`uses:` is not allowed in composite steps). SARIF upload must be a separate step in the user's workflow:

```yaml
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with:
    sarif_file: ${{ steps.repotoire.outputs.sarif-file }}
    category: repotoire
```

The action always produces a SARIF file and exposes its path via the `sarif-file` output. The README examples include the upload step for the recommended workflow.

### Repository Structure

New repo: `Zach-hammad/repotoire-action`

```
repotoire-action/
├── action.yml          # Action metadata (inputs, outputs, runs)
├── scripts/
│   ├── install.sh      # Binary download + cache
│   ├── analyze.sh      # Run analysis (full or diff)
│   └── outputs.sh      # Parse JSON, set GitHub outputs
├── README.md           # Usage docs with examples
├── LICENSE
└── .github/
    └── workflows/
        └── test.yml    # CI: test the action itself on sample repos
```

### Error Handling

- **Binary download fails:** Retry once, then fail with clear message ("Failed to download repotoire v0.3.113 for linux-x86_64")
- **Analysis fails:** Surface repotoire's stderr in the action log, fail the step
- **SARIF upload fails:** Warn but don't fail the step (user might not have `security-events: write`)
- **Invalid version:** Fail early with "Version 'v999' not found in releases"
- **No git history:** Warn if `fetch-depth` wasn't set to 0 (git analysis requires full history)

### Testing the Action

The action repo's own CI tests it against sample repos:
- A small Python repo with known findings → verify SARIF output, score output, finding count
- A clean repo → verify score > 90, no findings
- A PR diff → verify diff-only mode works (both explicit `true` and `auto` detection)
- Version pinning → verify specific version installs correctly
- Outputs → verify score, grade, findings-count are set correctly

## Out of Scope (v1)

- PR comments (requires backend or separate step)
- Check run annotations via Checks API (requires GitHub App)
- Caching analysis results across runs
- Self-hosted runner support (works but not tested/documented)
- Windows support (binary exists but composite action uses bash scripts — add PowerShell variant later)

## Success Criteria

1. `uses: Zach-hammad/repotoire-action@v1` works in 3 lines
2. Binary downloads in < 10 seconds (not 3-minute Rust build)
3. SARIF appears in GitHub Security tab with inline annotations
4. `fail-on: high` correctly fails the check on high+ findings
5. PR diff mode only reports new findings
6. Action outputs (score, grade, counts) are usable by downstream steps
7. README has copy-paste workflow examples
