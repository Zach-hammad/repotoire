# Automated Release Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automate releases so merging a release-please PR produces tagged releases with binaries, crates.io publication, and repotoire-action CI validation.

**Architecture:** release-please uses a PAT to create tags (so tag pushes propagate to other workflows) with `skip-github-release: true` (so it only manages PRs + tags). The existing `release.yml` triggers on `v*` tag push and handles builds, GitHub Release creation, cargo publish, and cross-repo dispatch. Clean separation: release-please owns versioning, release.yml owns distribution.

**Tech Stack:** GitHub Actions, release-please v4, cargo publish, repository_dispatch, peter-evans/repository-dispatch@v3

---

### Task 1: Create release-please Config Files

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`

- [ ] **Step 1: Create `release-please-config.json`**

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": {
    "repotoire-cli": {
      "release-type": "rust",
      "component": "repotoire",
      "include-component-in-tag": false,
      "changelog-path": "/CHANGELOG.md",
      "bump-minor-pre-major": true,
      "skip-github-release": true
    }
  }
}
```

Notes for the implementer:
- `"repotoire-cli"` is the path to the directory containing `Cargo.toml`.
- `"include-component-in-tag": false` produces `v0.4.0` tags (not `repotoire-v0.4.0`). Without this, the tag won't match the `v*` pattern in `release.yml`.
- `"changelog-path": "/CHANGELOG.md"` — the leading `/` writes to repo root, bypassing the package path prefix. Do NOT use `"../CHANGELOG.md"` — release-please rejects `..` in paths.
- `"bump-minor-pre-major": true` means `feat:` commits bump minor (not major) while version is `0.x.y`.
- `"skip-github-release": true` — release-please only creates PRs and tags. The existing `release.yml` handles GitHub Release creation via `softprops/action-gh-release`. This avoids duplicate releases.
- No `"extra-files": ["Cargo.lock"]` needed — the Rust strategy auto-updates `Cargo.lock` natively.

- [ ] **Step 2: Create `.release-please-manifest.json`**

```json
{
  "repotoire-cli": "0.3.113"
}
```

This is the current version (must match `Cargo.toml`). release-please computes the next version from conventional commits since this baseline. With 577 commits containing multiple `feat:` commits, the first release PR will bump to `0.4.0`.

- [ ] **Step 3: Verify JSON is valid**

```bash
python3 -c "import json; json.load(open('release-please-config.json')); print('config OK')"
python3 -c "import json; json.load(open('.release-please-manifest.json')); print('manifest OK')"
```

Expected: Both print OK.

- [ ] **Step 4: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "chore: add release-please config (manifest at 0.3.113)"
```

---

### Task 2: Create release-please Workflow

**Files:**
- Create: `.github/workflows/release-please.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Release Please

on:
  push:
    branches: [main]

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          token: ${{ secrets.RELEASE_PAT }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

Notes for the implementer:
- Uses `secrets.RELEASE_PAT` instead of the default `GITHUB_TOKEN`. This is critical: tags created by `GITHUB_TOKEN` do NOT trigger other workflows. The PAT ensures the `v*` tag push triggers `release.yml`.
- No `permissions` block needed — the PAT carries its own permissions.
- When the release PR is merged, this workflow runs, detects the merge, and creates a git tag using the PAT.
- The PAT needs `repo` scope (classic) or `Contents: Read and write` + `Pull requests: Read and write` (fine-grained) on the repotoire repo. It also needs access to `repotoire-action` for the cross-repo dispatch in `release.yml` — so one PAT covers both use cases.

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-please.yml')); print('Valid')"
```

Expected: `Valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release-please.yml
git commit -m "ci: add release-please workflow for automated releases"
```

---

### Task 3: Add cargo publish and Cross-Repo Dispatch to release.yml

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Add publish job after the release job**

Add this job at the end of `.github/workflows/release.yml`, after the existing `release` job:

```yaml
  publish:
    name: Publish to crates.io
    needs: release
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: dtolnay/rust-toolchain@stable

      - name: Publish to crates.io
        run: cd repotoire-cli && cargo publish
        env:
          CARGO_REGISTRY_TOKEN: ${{ secrets.CRATES_IO_TOKEN }}

      - name: Notify repotoire-action
        uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.RELEASE_PAT }}
          repository: Zach-hammad/repotoire-action
          event-type: new-release
          client-payload: '{"version": "${{ github.ref_name }}"}'
```

Notes for the implementer:
- `needs: release` ensures this runs after the GitHub Release is created.
- `cd repotoire-cli` is required — `Cargo.toml` and `Cargo.lock` are in `repotoire-cli/`, not repo root.
- `cargo publish` reads the version from `Cargo.toml` which release-please already bumped.
- `github.ref_name` is the tag name (e.g., `v0.4.0`) since this workflow triggers on `v*` tag push.
- Uses `RELEASE_PAT` (same PAT as release-please) for the cross-repo dispatch. This PAT needs `repo` scope on both `repotoire` and `repotoire-action`.
- The payload shape is `{"version": "v0.4.0"}` — the action's test workflow reads `github.event.client_payload.version`.

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('Valid')"
```

Expected: `Valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add cargo publish and cross-repo dispatch to release workflow"
```

---

### Task 4: Configure GitHub Secrets

This task requires manual steps in the GitHub UI / CLI. No code changes.

- [ ] **Step 1: Create a PAT for release-please + cross-repo dispatch**

Go to https://github.com/settings/tokens and create a classic PAT:
- Name: `repotoire-release`
- Scopes: `repo` (full control — needed for tag creation on repotoire + dispatch to repotoire-action)
- Expiration: pick something reasonable (90 days, 1 year, or no expiration)

One PAT serves both purposes: release-please tag creation and cross-repo dispatch.

- [ ] **Step 2: Add RELEASE_PAT secret to repotoire repo**

```bash
gh secret set RELEASE_PAT --repo Zach-hammad/repotoire
```

Paste the PAT when prompted.

- [ ] **Step 3: Generate a crates.io API token**

Go to https://crates.io/settings/tokens and create a new token with `publish-update` scope for the `repotoire` crate.

- [ ] **Step 4: Add CRATES_IO_TOKEN secret to repotoire repo**

```bash
gh secret set CRATES_IO_TOKEN --repo Zach-hammad/repotoire
```

Paste the token when prompted.

- [ ] **Step 5: Verify secrets exist**

```bash
gh secret list --repo Zach-hammad/repotoire
```

Expected: Both `RELEASE_PAT` and `CRATES_IO_TOKEN` appear in the list.

---

### Task 5: Update repotoire-action test.yml

**Files:**
- Modify: `/home/zach/code/repotoire-action/.github/workflows/test.yml`

- [ ] **Step 1: Add repository_dispatch trigger**

Change the `on:` block from:

```yaml
on:
  push:
    branches: [main]
  pull_request:
```

to:

```yaml
on:
  push:
    branches: [main]
  pull_request:
  repository_dispatch:
    types: [new-release]
```

- [ ] **Step 2: Make version-pin job conditional on dispatch**

Change the `test-version-pin` job from:

```yaml
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
```

to:

```yaml
  test-version-pin:
    name: Version Pinning
    if: github.event_name == 'repository_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install specific version
        uses: ./
        with:
          version: ${{ github.event.client_payload.version }}
          path: '.'

      - name: Verify installed version
        run: |
          INSTALLED=$(repotoire version 2>&1 | head -1)
          echo "Installed: $INSTALLED"
          echo "Expected: ${{ github.event.client_payload.version }}"
```

Notes for the implementer:
- `if: github.event_name == 'repository_dispatch'` — this job only runs when triggered by the cross-repo dispatch, not on regular push/PR.
- On push/PR, this job is skipped (shows as grey in the UI). The other 3 jobs (basic, fail-on, macOS) still run with `latest`.
- The version comes from `client_payload.version` — the exact field sent by the dispatch in `release.yml`.

- [ ] **Step 3: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('/home/zach/code/repotoire-action/.github/workflows/test.yml')); print('Valid')"
```

Expected: `Valid`

- [ ] **Step 4: Commit and push**

```bash
cd /home/zach/code/repotoire-action
git add .github/workflows/test.yml
git commit -m "ci: add repository_dispatch trigger for cross-repo release validation"
git push origin main
```

---

### Task 6: Push and Verify Pipeline

- [ ] **Step 1: Push repotoire changes**

```bash
cd /home/zach/code/repotoire
git push origin main
```

- [ ] **Step 2: Verify release-please creates a PR**

```bash
# Wait ~60 seconds for the workflow to run
gh run list --repo Zach-hammad/repotoire --workflow release-please.yml --limit 1
```

Expected: A workflow run appears. Check if a release PR was opened:

```bash
gh pr list --repo Zach-hammad/repotoire --label "autorelease: pending"
```

Expected: A PR titled something like "chore(main): release repotoire 0.4.0" with:
- `repotoire-cli/Cargo.toml` version bumped to `0.4.0`
- `repotoire-cli/Cargo.lock` updated
- `CHANGELOG.md` generated at repo root with all changes since `0.3.113`

- [ ] **Step 3: Review the release PR**

```bash
gh pr view <PR_NUMBER> --repo Zach-hammad/repotoire
gh pr diff <PR_NUMBER> --repo Zach-hammad/repotoire
```

Verify:
- Cargo.toml version is `0.4.0`
- Cargo.lock is updated
- CHANGELOG.md is at repo root (not `repotoire-cli/CHANGELOG.md`)
- Tag format will be `v0.4.0` (not `repotoire-v0.4.0`)
- No unexpected file changes

- [ ] **Step 4: Merge the release PR (triggers the full pipeline)**

```bash
gh pr merge <PR_NUMBER> --repo Zach-hammad/repotoire --merge
```

This triggers: release-please creates `v0.4.0` tag via PAT → tag push triggers `release.yml` → build 4 platform binaries → GitHub Release → cargo publish → dispatch to repotoire-action.

- [ ] **Step 5: Monitor the release pipeline**

```bash
# Watch for the tag to appear
git -C /home/zach/code/repotoire fetch --tags
git -C /home/zach/code/repotoire tag --sort=-v:refname | head -3

# Watch the release workflow
gh run list --repo Zach-hammad/repotoire --workflow release.yml --limit 1

# Check crates.io (may take a few minutes to index)
curl -s https://crates.io/api/v1/crates/repotoire | jq '.crate.max_version'

# Check repotoire-action CI was triggered
gh run list --repo Zach-hammad/repotoire-action --limit 1
```

Expected:
- Tag `v0.4.0` exists
- Release workflow completed (4 builds + publish + dispatch)
- crates.io shows `0.4.0`
- repotoire-action has a new CI run triggered by `repository_dispatch` with version-pin job running against `v0.4.0`

---

## Secrets Summary

| Secret | Repo | Purpose |
|--------|------|---------|
| `RELEASE_PAT` | repotoire | PAT with `repo` scope. Used by release-please (tag creation that triggers other workflows) and by release.yml (cross-repo dispatch to repotoire-action). |
| `CRATES_IO_TOKEN` | repotoire | crates.io API token for `cargo publish`. |

## Task Dependencies

```
Task 1 (config files) ──→ Task 2 (workflow) ──→ Task 3 (publish + dispatch) ──→ Task 4 (secrets) ──→ Task 5 (action test.yml) ──→ Task 6 (push + verify)
```

Tasks 1-3 are code changes in the repotoire repo. Task 4 is manual secret setup. Task 5 is in the repotoire-action repo. Task 6 is end-to-end verification.

Tasks 4 and 5 are independent of each other and can be done in parallel.
