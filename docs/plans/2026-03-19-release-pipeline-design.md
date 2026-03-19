# Automated Release Pipeline Design

## Goal

Automate releases so that merging to main eventually produces tagged releases with binaries, crates.io publication, and repotoire-action CI validation — without manual tagging.

## Architecture

```
push to main
    │
    ▼
release-please (runs on every push to main, uses PAT)
    │
    ├─ No releasable commits → no-op
    │   (docs:, chore:, test: commits alone won't trigger a release)
    │
    └─ Releasable commits (feat:, fix:) → Opens/updates Release PR
                              - Bumps repotoire-cli/Cargo.toml + Cargo.lock
                              - Generates CHANGELOG.md at repo root
                              - Title: "chore(main): release repotoire 0.4.0"
                              │
                              ▼
                         Merge the PR
                              │
                              ▼
                    release-please creates git tag (v0.4.0) via PAT
                    (skip-github-release: true — no release created here)
                              │
                              ▼
                    PAT tag push triggers release.yml
                              │
                              ├─ Build 4 platform binaries (existing matrix)
                              ├─ Create GitHub Release with assets (softprops/action-gh-release)
                              ├─ cargo publish from repotoire-cli/
                              │
                              ▼
                    Post-release: repository_dispatch → repotoire-action
                         payload: { "version": "v0.4.0" }
                              │
                              ▼
                    Action CI runs all tests against new version
                    (version-pin job only runs on dispatch, uses client_payload)
```

## Separation of Concerns

- **release-please** owns: version bumps, changelog generation, PRs, tags
- **release.yml** owns: builds, GitHub Releases, crates.io publishing, cross-repo dispatch
- Each workflow is small and single-purpose
- Manual tagging still works identically (triggers release.yml directly)

## Components

### 1. release-please workflow (new)

File: `.github/workflows/release-please.yml`

- Runs on push to main
- Uses `googleapis/release-please-action@v4` with a PAT (`RELEASE_PAT`)
- `release-type: rust`, `skip-github-release: true`
- Bumps `repotoire-cli/Cargo.toml` version + auto-updates `Cargo.lock` (Rust strategy handles this natively)
- Generates `CHANGELOG.md` at repo root via `changelog-path: "/CHANGELOG.md"`

**Why a PAT instead of GITHUB_TOKEN:** Tags created by `GITHUB_TOKEN` do not trigger other workflow runs (GitHub security policy). The PAT ensures the `v*` tag push propagates to `release.yml`.

**Why `skip-github-release: true`:** Without this, release-please creates a GitHub Release AND the existing `release.yml` creates another one → duplicates. With this flag, release-please only manages PRs and tags.

Config files:
- `release-please-config.json` — package path (`repotoire-cli`), `include-component-in-tag: false` (produces `v0.4.0` not `repotoire-v0.4.0`), changelog sections, tag settings
- `.release-please-manifest.json` — set to `"repotoire-cli": "0.3.113"` (current Cargo.toml version). release-please computes the next version from conventional commits since this baseline.

### 2. Modified release.yml

Additions to existing workflow:

- **cargo publish job**: runs after builds succeed, publishes to crates.io. Must `cd repotoire-cli` before running `cargo publish` (Cargo.toml and Cargo.lock are both in `repotoire-cli/`, not repo root).
- **Cross-repo dispatch step**: sends `new-release` event to `Zach-hammad/repotoire-action` using `RELEASE_PAT`. Exact payload shape:

```json
{
  "event_type": "new-release",
  "client_payload": {
    "version": "v0.4.0"
  }
}
```

Both the dispatch and `cargo publish` use `github.ref_name` (the tag name) as the version string.

Existing build matrix and GitHub Release creation unchanged.

### 3. repotoire-action test.yml update

- Add `repository_dispatch` trigger (`new-release` type)
- Version-pin job is conditional: `if: github.event_name == 'repository_dispatch'`
- Version comes from `github.event.client_payload.version`
- On regular push/PR, version-pin job is skipped; other 3 jobs run with `latest`
- No commits to the action repo. No self-triggering.

## Edge Cases

### Cargo.lock auto-updated by Rust strategy
The Rust release-type in release-please has a built-in `CargoLock` updater. It does a targeted TOML replacement of the crate's version entry in `Cargo.lock` — it does NOT run `cargo` to regenerate the full lock file. This is sufficient because a version bump doesn't change the dependency tree.

### docs-only periods produce no releases
`docs:`, `chore:`, `test:`, `ci:` commits are not "releasable" by default. The next `feat:` or `fix:` commit opens a release PR that includes everything since the last release.

### Tag format must be `v0.4.0`, not `repotoire-v0.4.0`
release-please defaults to including the component name in tags. `include-component-in-tag: false` is required or the `v*` trigger in `release.yml` won't match.

### crates.io is already claimed
`repotoire` is published on crates.io at `0.3.113`. We own it.

### Manual tagging coexistence
`release.yml` triggers on any `v*` tag push. Manual tagging still works. If you manually tag, update `.release-please-manifest.json` to match or the next release-please PR will compute the wrong version.

## Secrets Required

| Secret | Repo | Purpose |
|--------|------|---------|
| `RELEASE_PAT` | repotoire | Classic PAT with `repo` scope. Used by release-please (tag creation) and release.yml (cross-repo dispatch). One PAT serves both purposes. |
| `CRATES_IO_TOKEN` | repotoire | crates.io API token for `cargo publish`. |

## Versioning

- Manifest starts at `0.3.113` (current Cargo.toml version)
- First release PR bumps to `0.4.0` (577 commits with multiple `feat:` commits → minor bump)
- From there: `feat:` bumps minor, `fix:` bumps patch, `BREAKING CHANGE` bumps major
- Cargo.toml version kept in sync by release-please

## What Doesn't Change

- Existing release.yml build matrix (4 platforms)
- CI workflow (ci.yml)
- Manual tagging still works (release.yml triggers on any `v*` tag)
