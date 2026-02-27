# ML Classifier Pipeline Overhaul — Design Document

**Date:** 2026-02-27
**Status:** Approved
**Scope:** Better FP filtering, finding prioritization, full ML overhaul, predictive debt scoring

---

## Context

Repotoire's current classifier is a hand-rolled 2-layer MLP (51 features) with a heuristic fallback. It uses only warning metadata and path patterns — no graph metrics, no git history, no cross-finding features. The n-gram model is built but not integrated. LLM verification is stubbed but not connected.

Academic research (Yang & Menzies 2021, Wang et al. 2018, DeMuVGN 2024) shows that static analysis warning classification is "intrinsically easy" — the data lives in <2 dimensions, and simple models (GBDT, Random Forest) achieve >95% AUC. The key is **feature engineering**, not model complexity.

Repotoire already computes most of the features the research identifies as highest-signal (petgraph metrics, git2 history, tree-sitter code metrics) but never feeds them to the classifier.

## Research Foundation

| Paper | Key Finding |
|-------|-------------|
| Yang & Menzies 2021 | AWI is intrinsically <2 dimensions; linear SVM suffices for >95% AUC |
| Wang et al. 2018 | "Golden 23" features generalize across projects; 5 leak labels |
| DeMuVGN 2024 | Graph metrics + code metrics in GBDT improve F1 by 17-46% |
| PLOS ONE 2025 | XGBoost best at F2=0.77 with SNA + TD metrics combined |
| CMU SEI 2018 | 88-91% accuracy with alert fusion across multiple tools |
| Kamei/Heliyon 2024 | Weighted code churn: Random Forest AUC 0.83 |
| SA Retrospective | Warning context features suffer from data leakage |

## Feature Set: 28 Evidence-Backed Features

Every feature backed by at least 2 papers. No leaking features. All computable from existing repotoire infrastructure.

### Warning Metadata (6) — Tier 1 across all papers

| # | Feature | Source |
|---|---------|--------|
| 1 | Detector ID (hashed bucket) | Wang, Yang, SEI |
| 2 | Severity (ordinal 0-3) | Wang, Yang, SEI |
| 3 | Confidence score | Detectors |
| 4 | Detector category (ordinal) | Category-aware thresholds |
| 5 | Has CWE ID | SEI, SonarQube study |
| 6 | Entity type (function/class/file) | SEI |

### Size (4) — #1 predictor in DeMuVGN, PLOS ONE, SEI

| # | Feature | Source |
|---|---------|--------|
| 7 | Function LOC | DeMuVGN, PLOS ONE, all papers |
| 8 | File LOC | DeMuVGN, PLOS ONE, SEI |
| 9 | Function count in file | Wang, SEI |
| 10 | Finding line span (normalized) | SEI |

### Complexity (2) — Tier 1, different granularity to avoid LOC correlation

| # | Feature | Source |
|---|---------|--------|
| 11 | Function cyclomatic complexity | DeMuVGN, PLOS ONE (WMC), SEI |
| 12 | Max nesting depth at finding location | Wang, DeMuVGN, SEI |

### Coupling (3) — Tier 2, strong evidence from DeMuVGN + PLOS ONE

| # | Feature | Source |
|---|---------|--------|
| 13 | Fan-in (callers) | DeMuVGN (CountInput), PLOS ONE (CBO) |
| 14 | Fan-out (callees) | DeMuVGN (CountOutput), PLOS ONE |
| 15 | SCC membership (in cycle: 0/1) | PLOS ONE (SNA), petgraph Tarjan |

### Git History (5) — Tier 1 in Kamei, Heliyon, PLOS ONE

| # | Feature | Source |
|---|---------|--------|
| 16 | File age (log-scaled days) | Wang, Kamei (AGE) |
| 17 | Recent churn (LA+LD, 30-day window) | Kamei, Heliyon, PLOS ONE |
| 18 | Developer count (distinct authors) | DeMuVGN (DDEV), PLOS ONE, Kamei (NDEV) |
| 19 | Unique change count (commits to file) | Kamei (NUC), PLOS ONE (COMM) |
| 20 | Is recently created (<7 days: 0/1) | Kamei (AGE inverse) |

### Ownership (2) — Tier 2 from DeMuVGN

| # | Feature | Source |
|---|---------|--------|
| 21 | Major contributor % (lines by top author / total) | DeMuVGN (OWN_LINE) |
| 22 | Minor contributor count | DeMuVGN (MINOR_COMMIT) |

### Path/Context (3) — Validated by Wang, SEI

| # | Feature | Source |
|---|---------|--------|
| 23 | File depth in directory tree | Wang (file depth), SEI |
| 24 | FP path indicator count | Wang (path patterns), current heuristic |
| 25 | TP path indicator count | Wang (path patterns), current heuristic |

### Cross-Finding (3) — Non-leaking version of "warning context"

| # | Feature | Source |
|---|---------|--------|
| 26 | Finding density in file (findings/kLOC) | SEI (alert count), Wang (context) |
| 27 | Same-detector findings in file | SEI (fused alerts) |
| 28 | Historical FP rate for this detector | Wang (defect likelihood), non-leaking |

## Model Architecture

### Primary: GBDT via gbdt-rs (pure Rust)

```
Finding → FeatureExtractor (28 features) → GBDT Model → tp_probability [0,1]
                                                ↓
                                     CategoryThresholds → filter/keep/prioritize
```

- 100 trees, max depth 6, learning rate 0.1
- Trained offline in Python (XGBoost), exported to JSON
- Loaded in Rust via `gbdt-rs` (XGBoost JSON format)
- Inference: microseconds per finding, no GPU
- Model size: ~200-400KB embedded via `include_bytes!`

### Fallback Chain

1. Trained GBDT model (if available) → highest accuracy
2. Heuristic classifier (current, improved) → no training needed
3. Raw detector output (no filtering) → always available

### Three Output Modes

| Mode | Output | Use Case |
|------|--------|----------|
| Classification | TP/FP binary | FP filtering (`filter_false_positives`) |
| Ranking | Actionability score 0-100 | `--rank` flag on analyze |
| Debt scoring | Per-file risk score 0-100 | `repotoire debt` command |

Same 28 features and GBDT model serve all three modes.

## Training Pipeline

### Stage 1: Seed Model (ships with binary)

- Manually label findings on Flask, FastAPI, Django, repotoire
- Train XGBoost in Python (`scripts/train_model.py`), export JSON
- Embed in binary — users start with a working model on day one

### Stage 2: Git-Mined Labels (automatic, per-project)

- On first `repotoire analyze`, mine git history via git2
- Findings on code changed in "fix" commits → likely TP (weight 0.7)
- Findings on code stable 6+ months → likely FP (weight 0.5)
- Weak labels (lower weight than user labels at 1.0)
- Triggered via `repotoire train --auto`

### Stage 3: Active Learning (user feedback)

- `repotoire feedback <id> --tp/--fp` records labels (existing)
- New: `repotoire feedback --uncertain` shows 10 most uncertain findings
- Each label triggers incremental model update
- 50-100 labels sufficient for useful per-project model (Yang/Menzies)

## Debt Scoring

### New Command: `repotoire debt [path]`

Per-file debt risk score (0-100) computed from:

```
debt_risk = w1 * finding_density    # weighted by severity
          + w2 * coupling_score     # fan-in + fan-out + SCC
          + w3 * churn_score        # recent churn velocity
          + w4 * ownership_dispersion  # many authors = higher risk
          + w5 * age_factor         # recently created files = higher risk
```

Weights learned from GBDT feature importance on the seed dataset.

Output: ranked file list with scores and trend indicators (↑↓→).

### Integration

- `repotoire analyze --rank` sorts findings by actionability instead of severity
- `repotoire debt` standalone command
- MCP server gets `repotoire_predict_debt` tool (FREE tier)

## Implementation Scope

### New Dependencies

- `gbdt` crate (~100KB) — pure Rust GBDT inference + training

### Files to Create

| File | Purpose |
|------|---------|
| `src/classifier/features_v2.rs` | 28-feature evidence-backed extractor |
| `src/classifier/gbdt_model.rs` | GBDT model loading/inference via gbdt-rs |
| `src/classifier/bootstrap.rs` | Git-mined label generation |
| `src/classifier/debt.rs` | Per-file debt scoring |
| `src/cli/debt.rs` | `repotoire debt` command |
| `scripts/train_model.py` | Seed model training script |

### Files to Modify

| File | Change |
|------|--------|
| `src/classifier/model.rs` | Add GBDT model as primary, keep heuristic as fallback |
| `src/classifier/train.rs` | Update to use gbdt crate for Rust-side training |
| `src/cli/mod.rs` | Add debt command + `--rank` flag |
| `src/cli/analyze/postprocess.rs` | Use GBDT model in FP filter |
| `src/mcp/tools/` | Add debt prediction tool |
| `Cargo.toml` | Add gbdt dependency |

### Unchanged

- All 99 detectors
- Graph layer, parsers, scoring system
- Existing `feedback` command
- Config system, reporters, cache

## References

- Yang & Menzies (2021): [arXiv:2006.00444](https://arxiv.org/abs/2006.00444)
- Wang et al. (2018): [ACM DL](https://dl.acm.org/doi/10.1145/3239235.3239523)
- DeMuVGN (2024): [arXiv:2410.19550](https://arxiv.org/abs/2410.19550)
- PLOS ONE TD (2025): [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12148173/)
- CMU SEI (2018): [SEI Library](https://www.sei.cmu.edu/library/prioritizing-alerts-from-multiple-static-analysis-tools-using-classification-models/)
- Kamei/Heliyon (2024): [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11422029/)
- SA Retrospective: [GitHub](https://github.com/soarsmu/sa_retrospective)
- LLM4PFA (2026): [arXiv:2601.18844](https://arxiv.org/html/2601.18844v1)
- gbdt-rs: [GitHub](https://github.com/mesalock-linux/gbdt-rs)
- smartcore: [lib.rs](https://lib.rs/crates/smartcore)
