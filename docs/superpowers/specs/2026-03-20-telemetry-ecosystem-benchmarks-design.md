# Telemetry & Ecosystem Benchmarks Design

*2026-03-20*

## Problem

Repotoire analyzes codebases in isolation. Users get a score, a grade, and findings — but no context for what "good" looks like. Is a score of 72 excellent for a Rust project of this size, or mediocre? Are 3 circular dependencies normal? Is a modularity of 0.72 impressive? Users have no way to know.

Meanwhile, repotoire has no visibility into how the tool is actually used. Which detectors fire most? Which get marked as false positives? Which commands are adopted? Improving the product requires guesswork.

## Goal

Build a two-way data platform:

1. **Opted-in CLI users contribute anonymized analysis data** to a central analytics backend
2. **Users receive ecosystem benchmarks in return** — percentile rankings, comparisons, and trends displayed inline after every analysis
3. **Repotoire team gains product analytics** — feature adoption, detector accuracy, performance characteristics

MVP: enriched CLI output with ecosystem context. Later: web dashboard.

## Non-Goals

- Real-time collaboration or multiplayer features
- User accounts or authentication in the CLI
- Storing any identifiable information (repo names, file paths, code content)
- Building a custom backend service (MVP uses PostHog + CDN)
- Web dashboard (future phase, not designed here)

---

## Architecture Overview

```
CLI (opted-in)                          PostHog Cloud
  │                                         │
  ├── POST events ────────────────────────→ │ (capture API, public key)
  │   (fire-and-forget, background thread)  │
  │                                         │
  │                                    ┌────┴────┐
  │                                    │ ClickHouse│ (PostHog's internal store)
  │                                    └────┬────┘
  │                                         │
  │       Cron Job (GitHub Action, hourly)  │
  │         │                               │
  │         ├── HogQL queries ─────────────→│
  │         │← aggregated benchmarks ───────┤
  │         │                               │
  │         ├── Write JSON segments ──→ CDN (R2/S3)
  │         │                           │
  │         │   benchmarks.repotoire.dev/v1/
  │         │     global.json
  │         │     lang/rust.json
  │         │     lang/rust/10-50k.json
  │         │     ...
  │                                     │
  └── GET benchmark JSON ───────────────┘
      (24h local cache)
```

**Why PostHog:** Official Rust SDK, 1M free events/month, HogQL query API for computing aggregates, feature flags for rollout control. No backend service to build or maintain for the MVP.

**Why CDN for benchmarks (not direct HogQL from CLI):** Avoids exposing a read-scoped API key in the CLI binary. Pre-computed JSON is fast (CDN-cached), works when PostHog is down, and has no rate limit concerns. Benchmarks are up to 1 hour stale — acceptable for code analysis data.

---

## Identity & Privacy

- **`distinct_id`**: Random UUID v4, generated on opt-in, stored at `~/.config/repotoire/telemetry_id`. Not derived from any system or user information.
- **`repo_id`**: SHA-256 of the repository's root commit hash (first commit in `git log --reverse`). Stable across clones, deterministic, not reversible to the actual repository. Enables server-side trend tracking for the same repo across analyses.
- **PostHog configured with `disable_geoip: true`** — no IP-based location data stored.
- **Never collected:** repo names, remote URLs, file paths, code content, usernames, email addresses, or git author information.

---

## Opt-In Flow

Telemetry is **off by default**. Users must explicitly enable it.

### First Run Prompt

On first invocation of any command, if telemetry state is unset:

```
────────────────────────────────────────────────────
Help improve repotoire?

Share anonymous usage data to:
  - Get ecosystem benchmarks ("your score is top 25% for Rust projects")
  - Help us tune detectors and reduce false positives

No repo names, file paths, or code content. Ever.
See what's collected: https://repotoire.dev/telemetry

Enable? [y/N]
────────────────────────────────────────────────────
```

Default answer: **no**. Choice stored in `~/.config/repotoire/config.toml` under `[telemetry]`. Never prompted again unless the user deletes the config.

### Management Commands

- `repotoire config telemetry on` — enable, generate `distinct_id` if not present
- `repotoire config telemetry off` — disable, stop sending events (does not delete `distinct_id`)
- `repotoire config telemetry status` — show current state and summary of what is collected

### Visibility

When telemetry is enabled, every analysis output footer includes:
```
telemetry: on (repotoire config telemetry off to disable)
```

---

## Data Model

### Event: `analysis_complete`

Sent after every `repotoire analyze`. The core data event.

| Property | Type | Example | Purpose |
|---|---|---|---|
| `repo_id` | string | `a1b2c3d4...` (SHA-256) | Server-side trend tracking |
| `nth_analysis` | int | `14` | Client-computed analysis count for this repo |
| `score` | float | `72.4` | Ecosystem benchmarking |
| `grade` | string | `B+` | Ecosystem benchmarking |
| `pillar_structure` | float | `78.0` | Per-pillar benchmarks |
| `pillar_quality` | float | `65.2` | Per-pillar benchmarks |
| `pillar_architecture` | float | `74.1` | Per-pillar benchmarks |
| `languages` | map\<string, int\> | `{rust: 12000, python: 8000}` | Segmentation (raw LOC per language) |
| `primary_language` | string | `rust` | Primary segmentation axis |
| `frameworks` | list\<string\> | `[actix-web, diesel]` | Framework-level segmentation |
| `total_files` | int | `342` | Size segmentation |
| `total_kloc` | float | `20.0` | Size segmentation |
| `repo_shape` | string | `workspace` | Derived enum (see repo shape detection) |
| `has_workspace` | bool | `true` | Repo structure detail |
| `workspace_member_count` | int | `8` | Repo structure detail |
| `buildable_roots` | int | `1` | Independent build entry points |
| `language_count` | int | `2` | Languages with >5% LOC share |
| `primary_language_ratio` | float | `0.75` | Monolingual vs mixed signal |
| `findings_by_severity` | map\<string, int\> | `{critical: 2, high: 8, medium: 23, low: 41}` | Severity benchmarks |
| `findings_by_detector` | map\<string, map\> | `{sql_injection: {critical: 2, high: 1}}` | Per-detector, per-severity counts |
| `findings_by_category` | map\<string, int\> | `{security: 11, architecture: 8, quality: 23}` | Category benchmarks |
| `graph_nodes` | int | `1847` | Graph health benchmarks |
| `graph_edges` | int | `5231` | Graph health benchmarks |
| `graph_modularity` | float | `0.72` | Graph health benchmarks |
| `graph_scc_count` | int | `3` | Circular dependency benchmarks |
| `graph_avg_degree` | float | `5.66` | Coupling benchmarks |
| `graph_articulation_points` | int | `12` | Fragility benchmarks |
| `calibration_total` | int | `107` | Calibration divergence summary |
| `calibration_at_default` | int | `89` | How many thresholds stayed at default |
| `calibration_outliers` | map\<string, float\> | `{complexity: 14.2, nesting: 2.1}` | Top 10 most-divergent thresholds |
| `analysis_duration_ms` | int | `3400` | Performance monitoring |
| `analysis_mode` | string | `cold` / `cached` / `incremental` | Performance segmentation |
| `incremental_files_changed` | int | `4` | Incremental performance tracking |
| `ci` | bool | `false` | CI vs local segmentation |
| `os` | string | `linux` | Platform stats |
| `version` | string | `1.2.0` | Version tracking |

#### Repo Shape Detection

`repo_shape` is derived from the underlying properties:

| Shape | Condition |
|---|---|
| `monorepo` | `buildable_roots >= 3` |
| `workspace` | `has_workspace == true && buildable_roots < 3` |
| `multi-package` | `buildable_roots >= 2 && !has_workspace` |
| `single-package` | Everything else |

Detection heuristics:
- `has_workspace`: Cargo.toml `[workspace]`, pnpm-workspace.yaml, lerna.json, Go work file
- `buildable_roots`: Count of independent build files (Cargo.toml, package.json with scripts, go.mod, pyproject.toml) at distinct directory subtrees
- `language_count`: Languages with >5% of total LOC
- `primary_language_ratio`: LOC of dominant language / total LOC

#### Calibration Outlier Selection

From the full set of adaptive thresholds, compute `abs(calibrated - default) / default` for each. Send the top 10 by this ratio. Include `calibration_total` and `calibration_at_default` so the aggregate picture is clear without sending all 107 values.

### Event: `detector_feedback`

Sent on `repotoire feedback`.

| Property | Type | Example | Purpose |
|---|---|---|---|
| `repo_id` | string | `a1b2c3d4...` | Correlate with analysis data |
| `detector` | string | `sql_injection` | False positive rate per detector |
| `verdict` | string | `true_positive` / `false_positive` | Primary tuning signal |
| `severity` | string | `critical` | FP rate by severity |
| `language` | string | `python` | FP rate by language |
| `framework` | string | `django` | FP rate by framework |
| `version` | string | `1.2.0` | Track improvement across versions |

### Event: `fix_applied`

Sent on `repotoire fix`.

| Property | Type | Example | Purpose |
|---|---|---|---|
| `repo_id` | string | `a1b2c3d4...` | Correlate |
| `detector` | string | `dead_code` | Fix acceptance per detector |
| `fix_type` | string | `rule` / `ai` | Rule-based vs AI effectiveness |
| `accepted` | bool | `true` | Direct value signal |
| `language` | string | `rust` | Per-language fix quality |
| `ai_provider` | string | `claude` / `gpt4` / `ollama` / `null` | AI provider comparison |
| `version` | string | `1.2.0` | Track improvement |

### Event: `diff_run`

Sent on `repotoire diff`.

| Property | Type | Example | Purpose |
|---|---|---|---|
| `repo_id` | string | `a1b2c3d4...` | Trend tracking |
| `score_before` | float | `68.2` | Score trajectory |
| `score_after` | float | `72.4` | Score trajectory |
| `score_delta` | float | `+4.2` | Improvement rate benchmarks |
| `findings_added` | int | `3` | Finding churn rate |
| `findings_removed` | int | `11` | Fix rate |
| `findings_added_by_severity` | map\<string, int\> | `{high: 1, medium: 2}` | Severity-specific churn |
| `findings_removed_by_severity` | map\<string, int\> | `{critical: 1, high: 3}` | Severity-specific fix rate |
| `version` | string | `1.2.0` | Track |

### Event: `watch_session`

Sent when `repotoire watch` exits.

| Property | Type | Example | Purpose |
|---|---|---|---|
| `repo_id` | string | `a1b2c3d4...` | Correlate |
| `duration_s` | int | `3600` | Engagement depth |
| `reanalysis_count` | int | `12` | Usage intensity |
| `files_changed_total` | int | `34` | Activity level |
| `score_start` | float | `70.1` | Session-level improvement |
| `score_end` | float | `72.4` | Session-level improvement |
| `version` | string | `1.2.0` | Track |

### Event: `command_used`

Sent on every CLI invocation.

| Property | Type | Example | Purpose |
|---|---|---|---|
| `command` | string | `graph` | Feature adoption |
| `subcommand` | string | `stats` | Feature adoption |
| `flags` | list\<string\> | `[--format, --output]` | Flag usage patterns |
| `duration_ms` | int | `120` | Performance |
| `exit_code` | int | `0` | Error rates |
| `version` | string | `1.2.0` | Version tracking |
| `os` | string | `linux` | Platform stats |
| `ci` | bool | `false` | CI vs local |

**Flags allowlist:** Maintained explicitly in code. New flags are added to the allowlist manually, not automatically. Only flag names are sent, never values.

### Volume Estimate

| User type | Events/month |
|---|---|
| Light (analyze weekly) | ~8 |
| Regular (analyze daily, occasional fix/feedback) | ~50 |
| Heavy (watch sessions, daily diff, feedback) | ~200 |
| CI (analyze per PR) | ~100-500 |

At 1,000 opted-in users (mixed): ~50K-80K events/month. The free tier (1M events) supports ~15K active opted-in users before paid pricing kicks in.

---

## Benchmark Queries

The cron job (GitHub Action, hourly) queries PostHog via HogQL and publishes pre-computed benchmark JSON to a CDN.

### Benchmark Segments

```
benchmarks.repotoire.dev/v1/
  global.json                  — all-project aggregates
  lang/{language}.json         — per-language aggregates
  lang/{language}/{size}.json  — per-language + size bucket
```

Size buckets: `0-5k`, `5-10k`, `10-50k`, `50-100k`, `100k+` (by total kLOC).

The cron job only regenerates segments that received new data since the last run. Each segment file is independently cacheable.

### Computed Benchmarks

Each segment JSON contains:

```json
{
  "segment": {"language": "rust", "kloc_bucket": "10-50k"},
  "sample_size": 1247,
  "updated_at": "2026-03-20T14:00:00Z",
  "score": {
    "p25": 58.2, "p50": 67.1, "p75": 76.8, "p90": 84.3
  },
  "pillar_structure": {
    "p25": 62.0, "p50": 71.3, "p75": 80.1, "p90": 87.5
  },
  "pillar_quality": {
    "p25": 55.1, "p50": 64.8, "p75": 74.2, "p90": 82.0
  },
  "pillar_architecture": {
    "p25": 60.4, "p50": 69.7, "p75": 78.5, "p90": 85.8
  },
  "graph_modularity": {
    "p25": 0.45, "p50": 0.58, "p75": 0.71, "p90": 0.82
  },
  "graph_avg_degree": {
    "p25": 3.2, "p50": 5.1, "p75": 8.4, "p90": 12.7
  },
  "graph_scc_count": {
    "pct_zero": 0.45, "p50": 2, "p75": 5, "p90": 11
  },
  "grade_distribution": {
    "A+": 0.02, "A": 0.05, "A-": 0.08, "B+": 0.12,
    "B": 0.18, "B-": 0.15, "C+": 0.13, "C": 0.10,
    "C-": 0.07, "D+": 0.04, "D": 0.03, "D-": 0.02, "F": 0.01
  },
  "top_detectors": [
    {"name": "dead_code", "pct_repos_with_findings": 0.78},
    {"name": "magic_numbers", "pct_repos_with_findings": 0.65},
    {"name": "deep_nesting", "pct_repos_with_findings": 0.52}
  ],
  "detector_accuracy": [
    {"name": "sql_injection", "true_positive_rate": 0.88, "feedback_count": 234},
    {"name": "dead_code", "true_positive_rate": 0.94, "feedback_count": 891}
  ],
  "avg_improvement_per_analysis": 0.8
}
```

### Fallback Chain

When computing a user's percentile, the CLI uses the most specific segment with sufficient data:

1. `lang/{language}/{size}.json` — "Among Rust projects of 10-50k LOC"
2. `lang/{language}.json` — "Among Rust projects"
3. `global.json` — "Across all projects"

**Minimum sample size: 50.** If a segment has fewer than 50 data points, the segment file is not generated and the CLI falls back to the next level. The CLI labels the comparison group so users know what they're being compared against.

### Percentile Computation

Given the user's score and the segment's percentile distribution, the CLI interpolates the user's percentile rank. For example, if the user's score is 72.4 and the segment shows p50=67.1 and p75=76.8, the user is approximately at the 65th percentile.

---

## CLI Output Integration

### Analysis Output (Telemetry On, Benchmarks Available)

```
Score: 72.4 (B+)
  Structure: 78.0  |  Quality: 65.2  |  Architecture: 74.1

── Ecosystem Context ──────────────────────────────
  Score:         better than 68% of Rust projects
  Structure:     top 30%  |  Quality: top 55%  |  Architecture: top 20%
  Modularity:    top 15% for projects your size (10-50k LOC)
  Coupling:      lower than 60% — well-decoupled
  Trend:         +4.2 since last analysis (avg across ecosystem: +1.8)

  Compared against 1,247 Rust projects (last 90 days)
───────────────────────────────────────────────────
  telemetry: on (repotoire config telemetry off)
```

### Analysis Output (Telemetry On, Insufficient Data)

```
Score: 72.4 (B+)
  Structure: 78.0  |  Quality: 65.2  |  Architecture: 74.1

── Ecosystem Context ──────────────────────────────
  Not enough data for Rust workspace projects yet.
  Your analyses help build these benchmarks.
───────────────────────────────────────────────────
```

### Analysis Output (Telemetry Off)

```
Score: 72.4 (B+)
  Structure: 78.0  |  Quality: 65.2  |  Architecture: 74.1

  Tip: Enable telemetry to see how your project compares
       to the ecosystem. Run: repotoire config telemetry on
```

Shown once per session, not on every command.

### New Command: `repotoire benchmark`

Standalone benchmark fetch. Shows full ecosystem comparison using the most recent analysis of the current repo.

```
$ repotoire benchmark

── Ecosystem Benchmarks (last analysis: 2h ago) ──

  Overall Score: 72.4 (B+)
    → better than 68% of Rust projects
    → better than 74% of Rust projects of 10-50k LOC

  Pillars:
    Structure:     78.0  → top 30%
    Quality:       65.2  → top 55%
    Architecture:  74.1  → top 20%

  Graph Health:
    Modularity:        0.72  → top 15%
    Avg coupling:      5.66  → lower than 60%
    Circular deps:     3 SCCs → 45% of Rust projects have 0
    Articulation pts:  12    → typical for your size

  Top Findings vs Ecosystem:
    #1 dead_code (12)       — also #1 across Rust projects
    #2 magic_numbers (8)    — #3 across Rust projects
    #3 deep_nesting (6)     — #5 across Rust projects

  Detector Accuracy (from community feedback):
    sql_injection:    88% true positive rate
    dead_code:        94% true positive rate
    magic_numbers:    71% true positive rate — review flagged instances

  Your Trend (14 analyses):
    Score: 58.1 → 72.4 (+14.3 over 3 months)
    Avg improvement per analysis: +1.02
    Ecosystem avg: +0.8 per analysis

  Compared against 1,247 Rust projects (last 90 days)
────────────────────────────────────────────────────
```

Supports `--json` for programmatic access.

---

## Implementation Architecture

### Module Structure

```
repotoire-cli/src/
  telemetry/
    mod.rs            — public API: init(), capture(), fetch_benchmarks()
    config.rs         — opt-in state, telemetry_id/repo_id generation, prompt
    events.rs         — event structs (AnalysisComplete, DetectorFeedback, etc.)
    posthog.rs        — PostHog client wrapper (capture via posthog-rs)
    benchmarks.rs     — CDN fetch, percentile interpolation, fallback chain
    cache.rs          — 24h local cache at ~/.cache/repotoire/benchmarks/
    display.rs        — terminal formatting for benchmark output
```

### Lifecycle

1. **`telemetry::init()`** — called once at CLI startup. Loads config from `~/.config/repotoire/config.toml`. If telemetry is enabled, initializes the PostHog client. Returns a `Telemetry` handle. If telemetry is disabled, returns a no-op stub with the same interface.

2. **Event capture** — after analysis (or any tracked command) completes, events are sent via `ureq` in a **background thread**. Fire-and-forget. Never blocks CLI output. Failures are silently ignored (no retry, no error message).

3. **Benchmark fetch** — after events are sent, the CLI checks the local cache (`~/.cache/repotoire/benchmarks/`). If the cache is fresh (<24h), it uses the cached data. Otherwise, it fetches 2-3 JSON files from the CDN based on the user's repo profile (global + language + language/size). If the fetch fails (network error, CDN down), it uses stale cache with a "(cached Xh ago)" label. If no cache exists, benchmarks are silently omitted.

### API Key Handling

- **PostHog capture API key** (write-only): Embedded in the binary. This is standard practice for client-side analytics. PostHog keys are designed to be public — they can only write events, not read data.
- **CDN benchmark files**: Public, no API key needed. Read-only static JSON.
- **No read-scoped PostHog key in the CLI binary.** All benchmark data is pre-computed and served via CDN.

### Cron Job (Benchmark Generator)

A GitHub Action on a schedule (hourly):

1. Queries PostHog HogQL API with a server-side API key (stored as a GitHub secret)
2. Computes percentiles, distributions, and detector accuracy for each segment
3. Writes JSON files to R2/S3
4. Invalidates CDN cache for updated segments
5. Only regenerates segments that received new `analysis_complete` events since last run

### Dependencies

New Cargo dependencies:
- `posthog-rs` — official PostHog Rust SDK for event capture
- `uuid` (v4 generation) — for `distinct_id` (may already be available via existing deps)

No new heavy dependencies. `ureq` (already used) handles the CDN fetch.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| PostHog free tier exceeded | Monitor usage; paid tiers are usage-based and scale smoothly |
| PostHog API changes or downtime | CDN decouples benchmark reads from PostHog availability; events are fire-and-forget |
| Low opt-in rate → insufficient benchmark data | Benchmarks degrade gracefully (fallback chain + minimum sample size); the ecosystem context section simply doesn't appear |
| Privacy concern despite anonymization | Transparent documentation at repotoire.dev/telemetry; easy opt-out; `repo_id` is a one-way hash |
| CDN cost | At <1000 benchmark files of ~2KB each, storage is negligible; bandwidth is low (each CLI fetches 2-3 files per day max) |
| `repo_id` collision | SHA-256 of root commit; collision probability is negligible |
| Flag allowlist maintenance | Enforced in code — new flags must be explicitly added; CI test can verify completeness |

---

## Future Extensions (Not In Scope)

- **Web dashboard** — serve the same benchmark data in a browser with interactive charts
- **Team benchmarks** — opt-in team grouping for private cross-repo comparisons
- **Detector auto-tuning** — use aggregated feedback data to adjust detector thresholds in new releases
- **Cohort analysis** — "repos that adopted repotoire 6 months ago improved their score by X on average"
- **PostHog feature flags** — gate new detectors or CLI features behind flags for gradual rollout
