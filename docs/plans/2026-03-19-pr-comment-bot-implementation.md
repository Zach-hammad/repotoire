# PR Comment Bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Post a summary comment on pull requests showing score, grade, and top 5 findings, updating in-place on re-runs.

**Architecture:** New `scripts/comment.sh` in the `repotoire-action` repo runs as a composite step after `outputs.sh`. On PR events, it reads JSON results, builds markdown, and creates/updates a comment via `gh api`. Controlled by `comment` input (default `true`). The action repo uses release-please for proper versioning with floating `v1` tag updated automatically on release.

**Tech Stack:** Bash, jq, gh CLI, release-please, actions/create-github-app-token

---

### Task 0: Set Up release-please in repotoire-action

**Files:**
- Create: `/home/zach/code/repotoire-action/release-please-config.json`
- Create: `/home/zach/code/repotoire-action/.release-please-manifest.json`
- Create: `/home/zach/code/repotoire-action/.github/workflows/release-please.yml`
- Create: `/home/zach/code/repotoire-action/.github/workflows/update-v1-tag.yml`

- [ ] **Step 1: Create `release-please-config.json`**

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": {
    ".": {
      "release-type": "simple",
      "bump-minor-pre-major": true,
      "include-component-in-tag": false
    }
  }
}
```

Notes for the implementer:
- `release-type: simple` — no Cargo.toml or package.json to bump, just tags and changelog.
- Package path is `"."` (repo root).
- `include-component-in-tag: false` produces `v1.1.0` tags.

- [ ] **Step 2: Create `.release-please-manifest.json`**

```json
{
  ".": "1.0.0"
}
```

Current version is `v1.0.0` (the existing release).

- [ ] **Step 3: Create `.github/workflows/release-please.yml`**

```yaml
name: Release Please

on:
  push:
    branches: [main]

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/create-github-app-token@v1
        id: app-token
        with:
          app-id: ${{ secrets.APP_ID }}
          private-key: ${{ secrets.APP_PRIVATE_KEY }}

      - uses: googleapis/release-please-action@v4
        with:
          token: ${{ steps.app-token.outputs.token }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

Notes for the implementer:
- Uses the RepotoireApp GitHub App for token (same as main repo). The app must be installed on `repotoire-action`.
- `APP_ID` and `APP_PRIVATE_KEY` secrets must be set on the `repotoire-action` repo (same values as the main repo).

- [ ] **Step 4: Create `.github/workflows/update-v1-tag.yml`**

This workflow runs when release-please publishes a GitHub Release, and moves the floating `v1` tag to match.

```yaml
name: Update v1 Tag

on:
  release:
    types: [published]

jobs:
  update-tag:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Update floating v1 tag
        run: |
          TAG="${{ github.event.release.tag_name }}"
          MAJOR=$(echo "$TAG" | grep -oP '^v\d+')
          echo "Moving ${MAJOR} tag to ${TAG}"
          git tag -f "$MAJOR" "$TAG"
          git push -f origin "$MAJOR"
```

Notes for the implementer:
- Extracts the major version from the tag (e.g., `v1` from `v1.1.0`).
- Force-updates the floating tag to point to the new release.
- Uses `GITHUB_TOKEN` (not the app) — `contents: write` permission is sufficient for tags on the same repo.

- [ ] **Step 5: Set secrets on repotoire-action repo**

```bash
echo "2390942" | gh secret set APP_ID --repo Zach-hammad/repotoire-action
gh secret set APP_PRIVATE_KEY --repo Zach-hammad/repotoire-action < /home/zach/Downloads/repotoireapp.2026-03-19.private-key.pem
```

- [ ] **Step 6: Verify JSON/YAML is valid**

```bash
cd /home/zach/code/repotoire-action
python3 -c "import json; json.load(open('release-please-config.json')); print('config OK')"
python3 -c "import json; json.load(open('.release-please-manifest.json')); print('manifest OK')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-please.yml')); print('release-please OK')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/update-v1-tag.yml')); print('update-v1-tag OK')"
```

Expected: All print OK.

- [ ] **Step 7: Commit**

```bash
cd /home/zach/code/repotoire-action
git add release-please-config.json .release-please-manifest.json .github/workflows/release-please.yml .github/workflows/update-v1-tag.yml
git commit -m "ci: add release-please and floating v1 tag automation"
```

---

### Task 1: Add `comment` Input to action.yml

**Files:**
- Modify: `/home/zach/code/repotoire-action/action.yml`

- [ ] **Step 1: Add the input**

In `action.yml`, add after the `args` input (line 37):

```yaml
  comment:
    description: 'Post analysis summary as a PR comment (pull_request events only)'
    required: false
    default: 'true'
```

- [ ] **Step 2: Add the composite step**

Add after the `Parse Outputs` step (at the end of the `runs.steps` list):

```yaml
    - name: PR Comment
      id: comment
      shell: bash
      run: ${{ github.action_path }}/scripts/comment.sh
      if: always()
      env:
        INPUT_COMMENT: ${{ inputs.comment }}
        GH_TOKEN: ${{ github.token }}
        INPUT_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
```

- [ ] **Step 3: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('/home/zach/code/repotoire-action/action.yml')); print('Valid')"
```

Expected: `Valid`

- [ ] **Step 4: Commit**

```bash
cd /home/zach/code/repotoire-action
git add action.yml
git commit -m "feat: add comment input and PR Comment composite step"
```

---

### Task 2: Create comment.sh Script

**Files:**
- Create: `/home/zach/code/repotoire-action/scripts/comment.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- Gate checks ---
if [ "${INPUT_COMMENT:-}" != "true" ]; then
  echo "PR comment disabled (comment=$INPUT_COMMENT)"
  exit 0
fi

if [ "${GITHUB_EVENT_NAME:-}" != "pull_request" ] && [ "${GITHUB_EVENT_NAME:-}" != "pull_request_target" ]; then
  echo "Not a PR event (event=$GITHUB_EVENT_NAME) — skipping comment"
  exit 0
fi

# --- Read PR number ---
PR_NUMBER=$(jq -r '.pull_request.number // empty' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
if [ -z "$PR_NUMBER" ]; then
  echo "::warning::Could not read PR number from event payload — skipping comment"
  exit 0
fi

# --- Read results ---
JSON_FILE="${RUNNER_TEMP:-/tmp}/repotoire-results/results.json"
if [ ! -f "$JSON_FILE" ]; then
  echo "::warning::No JSON results file — skipping PR comment"
  exit 0
fi

SCORE=$(jq -r '.overall_score // 0' "$JSON_FILE")
GRADE=$(jq -r '.grade // "?"' "$JSON_FILE")
TOTAL=$(jq -r '.findings | length' "$JSON_FILE")
CRITICAL=$(jq -r '[.findings[] | select(.severity == "critical")] | length' "$JSON_FILE")
HIGH=$(jq -r '[.findings[] | select(.severity == "high")] | length' "$JSON_FILE")

SCORE=$(printf "%.1f" "$SCORE")

# --- Build file link base ---
HEAD_SHA="${INPUT_HEAD_SHA:-${GITHUB_SHA:-HEAD}}"
LINK_BASE="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/blob/${HEAD_SHA}"

# --- Extract top 5 findings ---
FINDINGS_TABLE=$(jq -r --arg base "$LINK_BASE" '
  def sev_order: {"critical":0,"high":1,"medium":2,"low":3,"info":4}[.] // 5;
  def escape_md: gsub("\\|"; "\\|") | gsub("`"; "");
  def clean_path: ltrimstr("./");
  [.findings[]
    | {
        severity,
        title: (.title | escape_md),
        file: ((.affected_files[0] // null) | if . then clean_path else null end),
        line: .line_start
      }
  ]
  | sort_by(.severity | sev_order)
  | .[0:5]
  | .[]
  | if .file then
      if .line then
        "| \(.severity) | [`\(.file):\(.line)`](\($base)/\(.file)#L\(.line)) | \(.title) |"
      else
        "| \(.severity) | `\(.file)` | \(.title) |"
      end
    else
      "| \(.severity) | | \(.title) |"
    end
' "$JSON_FILE" 2>/dev/null || true)

# --- Build comment body ---
BODY_FILE=$(mktemp)

cat > "$BODY_FILE" << MDEOF
<!-- repotoire-comment -->
### Repotoire Analysis

| Score | Grade | Findings | Critical | High |
|-------|-------|----------|----------|------|
| ${SCORE} | ${GRADE} | ${TOTAL} | ${CRITICAL} | ${HIGH} |
MDEOF

if [ "$TOTAL" -gt 0 ] && [ -n "$FINDINGS_TABLE" ]; then
  DISPLAY_COUNT=$(echo "$FINDINGS_TABLE" | wc -l)
  cat >> "$BODY_FILE" << MDEOF

<details>
<summary>Top findings (${DISPLAY_COUNT})</summary>

| Severity | File | Finding |
|----------|------|---------|
${FINDINGS_TABLE}

</details>
MDEOF
elif [ "$TOTAL" -eq 0 ]; then
  echo "" >> "$BODY_FILE"
  echo "No findings detected." >> "$BODY_FILE"
fi

# --- Find existing comment ---
COMMENT_ID=$(gh api "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
  --paginate \
  --jq '.[] | select(.body | contains("<!-- repotoire-comment -->")) | .id' \
  2>/dev/null | head -1 || true)

# --- Create or update ---
if [ -n "$COMMENT_ID" ]; then
  echo "Updating existing comment $COMMENT_ID"
  jq -Rs '{body: .}' "$BODY_FILE" | \
    gh api "repos/${GITHUB_REPOSITORY}/issues/comments/${COMMENT_ID}" \
      --method PATCH \
      --input - \
      > /dev/null 2>&1 \
    || echo "::warning::Failed to update PR comment (check pull-requests: write permission)"
else
  echo "Creating new comment on PR #${PR_NUMBER}"
  jq -Rs '{body: .}' "$BODY_FILE" | \
    gh api "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
      --method POST \
      --input - \
      > /dev/null 2>&1 \
    || echo "::warning::Failed to create PR comment (check pull-requests: write permission)"
fi

rm -f "$BODY_FILE"
echo "PR comment done"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /home/zach/code/repotoire-action/scripts/comment.sh
```

- [ ] **Step 3: Commit**

```bash
cd /home/zach/code/repotoire-action
git add scripts/comment.sh
git commit -m "feat: add PR comment script with top-5 findings and update-in-place"
```

---

### Task 3: Update Test Workflow

**Files:**
- Modify: `/home/zach/code/repotoire-action/.github/workflows/test.yml`

- [ ] **Step 1: Add a PR comment test job**

Add after the `test-macos` job:

```yaml
  test-comment-disabled:
    name: Comment Disabled
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Create test fixture
        run: |
          mkdir -p test-repo
          cat > test-repo/app.py << 'PYEOF'
          import os
          def run_command(user_input):
              os.system(user_input)
          PYEOF
          sed -i 's/^          //' test-repo/app.py

      - name: Run with comment disabled
        uses: ./
        with:
          path: test-repo
          comment: 'false'
```

Notes for the implementer:
- We can't test actual PR commenting in a push-triggered workflow (no PR context). This test verifies the `comment: false` path doesn't error.
- Actual PR comment testing happens manually by opening a PR against the action repo.

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('/home/zach/code/repotoire-action/.github/workflows/test.yml')); print('Valid')"
```

Expected: `Valid`

- [ ] **Step 3: Commit**

```bash
cd /home/zach/code/repotoire-action
git add .github/workflows/test.yml
git commit -m "test: add comment-disabled test job"
```

---

### Task 4: Update README

**Files:**
- Modify: `/home/zach/code/repotoire-action/README.md`

- [ ] **Step 1: Add comment input to inputs table**

Find the inputs table in README.md and add a row:

```markdown
| `comment` | Post analysis summary as PR comment | `'true'` |
```

- [ ] **Step 2: Add permissions note**

Add a "Permissions" section (or add to existing one):

```markdown
## Permissions

For PR comments, your workflow needs:

```yaml
permissions:
  pull-requests: write
  contents: read
```

Without `pull-requests: write`, the comment step will warn but not fail.
```

- [ ] **Step 3: Add example showing the comment**

Add to the examples section:

```markdown
### PR Analysis with Comment

```yaml
name: Code Health
on: pull_request
permissions:
  pull-requests: write
  contents: read
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Zach-hammad/repotoire-action@v1
        with:
          fail-on: high
```

The action automatically posts a summary comment on the PR with score, grade, and top findings. Set `comment: 'false'` to disable.
```

- [ ] **Step 4: Commit**

```bash
cd /home/zach/code/repotoire-action
git add README.md
git commit -m "docs: add PR comment input, permissions, and example to README"
```

---

### Task 5: Push, Test, and Release

- [ ] **Step 1: Push all changes**

```bash
cd /home/zach/code/repotoire-action
git push origin main
```

- [ ] **Step 2: Verify CI passes**

```bash
gh run list --repo Zach-hammad/repotoire-action --limit 1
```

Wait for completion. All jobs (basic, fail-on, macOS, comment-disabled) should pass. Version-pin is skipped (only on dispatch).

- [ ] **Step 3: Verify release-please opened a PR**

```bash
gh pr list --repo Zach-hammad/repotoire-action --state open
```

Expected: A PR titled "chore(main): release 1.1.0" (feat: commits trigger minor bump).

- [ ] **Step 4: Manual PR comment test**

Create a test branch, open a PR, and verify the comment appears:

```bash
cd /home/zach/code/repotoire-action
git checkout -b test-comment
echo "# test" >> README.md
git add README.md
git commit -m "test: trigger PR comment"
git push origin test-comment
gh pr create --title "test: PR comment bot" --body "Testing PR comment feature"
```

Wait for the action to run, then check the PR for the comment. After verifying, close and delete:

```bash
gh pr close test-comment --delete-branch
```

- [ ] **Step 5: Merge the release PR**

```bash
gh pr merge <PR_NUMBER> --repo Zach-hammad/repotoire-action --merge
```

This triggers: release-please creates `v1.1.0` tag + GitHub Release → `update-v1-tag.yml` moves `v1` to `v1.1.0`.

- [ ] **Step 6: Verify the v1 tag was updated**

```bash
gh run list --repo Zach-hammad/repotoire-action --workflow update-v1-tag.yml --limit 1
git -C /home/zach/code/repotoire-action fetch --tags
git -C /home/zach/code/repotoire-action tag -l 'v1*'
```

Expected: `v1` and `v1.1.0` both exist, `v1` points to the same commit as `v1.1.0`.

---

## Task Dependencies

```
Task 0 (release-please setup) → Task 1 (action.yml input + step) → Task 2 (comment.sh script) → Task 3 (test workflow) → Task 4 (README) → Task 5 (push + test + release)
```

Sequential. All changes are in the `repotoire-action` repo.
