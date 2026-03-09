# Hierarchical Predictive Coding Engine — Design Document

**Date:** 2026-03-09
**Status:** Approved
**Branch:** `feat/hierarchical-predictive-coding`

## Summary

Replace the current flat n-gram surprisal detector with a **5-level hierarchical predictive coding engine** that computes prediction errors at multiple independent levels of code abstraction. Severity is determined by **concordance** (how many levels agree something is surprising) and **precision-weighted** aggregation (Friston's free energy formalism).

This is the first application of hierarchical predictive coding theory to code analysis.

## Motivation

The current `SurprisalDetector` uses a single trigram language model to flag unusual token sequences. This is noisy (high FP rate) and misses higher-level anomalies. Research shows:

- Token surprisal alone: AUC ~0.6-0.65 for bug detection (Ray & Hellendoorn 2015)
- Graph embeddings alone: +9% F1 over baselines (Qu et al. 2018, node2defect)
- Token + graph relational bias: 10-15% improvement over either alone (Hellendoorn et al. ICLR 2020, GREAT)
- Dependency-chain surprisal: outperforms line-by-line surprisal (Yang et al. OOPSLA 2024, DAN)

**No published work combines all five levels with precision-weighted aggregation.**

## The Hierarchy

```
L4: Architecture    Module-level outlier + cross-smell co-occurrence
    ↕ prediction errors
L3: Relational      Per-edge-type node2vec embeddings (concatenated)
    ↕ prediction errors
L1.5: Dependency    N-gram surprisal along dependency-chain paths
    ↕ prediction errors
L2: Structural      Mahalanobis on per-language feature vectors
    ↕ prediction errors
L1: Token           Per-language trigram + 5-gram n-gram
```

Each level independently learns a generative model of "what's normal" from the codebase, then scores every entity against it. The prediction error at each level is a z-score.

## Level Details

### L1: Token Surprisal (Enhanced)

**What it models:** Token sequence patterns within functions.

**How:** Per-language trigram + 5-gram n-gram models trained during the analyze pipeline. Tokens are normalized (identifiers → `<ID>`, strings → `<STR>`, numbers → `<NUM>`, keywords preserved).

**Enhancement over current:** Separate models per language (Python, TypeScript, Rust, etc.) instead of one cross-language model. This is backed by Partachi & Sugiyama (ICSE 2024) showing naturalness signals are language-specific.

**Z-threshold:** > 2.5 (noisy signal, demand stronger evidence).

**Output:** Per-function average surprisal z-score.

**Existing code:** `calibrate/ngram.rs`, `detectors/surprisal.rs` — enhanced, not replaced.

### L2: Structural Surprise

**What it models:** Function/class shapes — the statistical distribution of structural metrics.

**How:** For each function, compute a feature vector:
```
[param_count, cyclomatic_complexity, nesting_depth, LOC, return_count, branch_ratio]
```

Learn per-language multivariate Gaussian (mean vector + covariance matrix). Compute Mahalanobis distance from the project centroid = structural surprise.

**Why Mahalanobis:** Captures unusual *combinations* of metrics even when individual metrics are in range (e.g., a function with low LOC but high complexity AND high nesting is unusual even if each metric alone is fine). Theoretically justified by arXiv 2003.00402.

**Z-threshold:** > 2.0

**Data source:** Metrics already extracted by parsers and stored in graph nodes.

### L1.5: Dependency-Chain Surprisal (Novel Bridge)

**What it models:** Token patterns along program dependency paths, not isolated lines.

**How:**
1. Extract dependency chains from the graph: for each function, follow its outgoing Calls/Uses edges to form chains of connected code
2. Concatenate token sequences along each chain (ordered by dependency direction)
3. Compute n-gram surprisal on each chain's token sequence
4. Per-function score = max surprisal over all chains containing that function

**Why:** DAN (Yang et al. OOPSLA 2024) showed this outperforms line-by-line surprisal because it captures whether code *along a dependency path* is internally consistent. A function that looks normal in isolation but uses unusual patterns compared to its callers/callees is flagged.

**Z-threshold:** > 2.0

**Reuses:** L1's trained n-gram model for the actual surprisal computation.

### L3: Relational Surprise

**What it models:** How entities connect in the code graph — call patterns, import patterns, inheritance.

**How:**
1. Port node2vec random walk generation and word2vec skip-gram from `repotoire-fast` to `repotoire-cli` (pure Rust, remove PyO3 wrappers)
2. Run **separate node2vec passes per edge type** (Calls, Imports, Inherits, Contains) — backed by DSHGT (arXiv 2306.01376) showing heterogeneous edge types carry different structural information
3. Concatenate per-edge-type embeddings → combined relational vector per entity
4. For each entity: compute cosine distance to k-nearest neighbors in embedding space
5. Entities far from their local neighborhood = relationally surprising

**Z-threshold:** > 1.5 (cleanest signal, lowest bar).

**What this catches:**
- Functions called by unusual callers
- Classes that inherit oddly compared to project norms
- Modules with misplaced dependencies
- Code ported from different parts of the system that doesn't fit its graph neighborhood

**Parameters:**
- Embedding dimension: 64 per edge type (256 total concatenated)
- Walk length: 10
- Walks per node: 20
- p=1.0, q=1.0 (unbiased DeepWalk — simplest starting point)
- Word2vec: window=5, negative_samples=5, epochs=5

### L4: Architectural Surprise

**What it models:** Module-level patterns — whether a module's profile is unusual compared to peer modules.

**How:**
1. Aggregate L2 and L3 scores per module/directory
2. Compute module-level features:
   ```
   [avg_fan_in, avg_fan_out, internal_cohesion, external_coupling,
    entity_count, cross_smell_co_occurrence_count]
   ```
3. Cross-smell co-occurrence: count how many distinct smell types from existing detectors are co-located in this module (Zhang et al. arXiv 2509.03896 shows this is more predictive than individual smell counts)
4. Mahalanobis distance from module centroid = architectural surprise

**Z-threshold:** > 2.0

## Scoring & Severity

### Precision-Weighted Aggregation

Instead of fixed equal weights, compute **empirical precision** for each level:

```
precision_i = 1 / variance(z_scores_i across all entities)
weight_i = precision_i / Σ(precision_j)
```

This is Friston's free energy formulation applied to code: noisier levels (higher variance) get automatically downweighted. In practice, we expect L1 to get the lowest weight (noisiest) and L3 to get the highest.

### Compound Surprise Score

```
compound_surprise = Σ(weight_i × z_i) for all levels where z_i > threshold_i
```

### Severity by Concordance

The number of levels exceeding their respective thresholds drives severity:

| Concordance | Severity | Interpretation |
|-------------|----------|----------------|
| 1 level | Info | Normal variance at one level |
| 2 levels | Low | Worth a glance |
| 3 levels | Medium | Warrants review |
| 4-5 levels | High | Almost certainly problematic |

Compound score is used for ranking within a severity tier.

## Module Structure

```
repotoire-cli/src/predictive/
├── mod.rs              — PredictiveCodingEngine (orchestrates all levels)
├── token_level.rs      — L1: Per-language n-gram surprisal
├── structural.rs       — L2: Mahalanobis distance on feature vectors
├── dependency_chain.rs — L1.5: Dependency-path surprisal
├── relational.rs       — L3: Node2vec embeddings + kNN distance
├── architectural.rs    — L4: Module-level outlier detection
├── compound.rs         — Precision-weighted aggregation + concordance scoring
└── embeddings.rs       — Node2vec + Word2vec (ported from repotoire-fast)
```

## New Detector

`HierarchicalSurprisalDetector` replaces `SurprisalDetector`:
- Takes a `PredictiveCodingEngine` (built during analyze pipeline, after graph construction)
- Emits findings with per-level breakdown in `threshold_metadata`
- Category: `predictive-coding`

## Pipeline Integration

```
1. Parse files → build graph               (existing)
2. Train L1 per-language n-gram models      (enhanced existing)
3. Compute L2 feature vectors from graph    (new)
4. Extract L1.5 dependency chains from graph (new)
5. Run per-edge-type node2vec → word2vec    (ported from repotoire-fast)
6. Aggregate to L4 module profiles          (new)
7. Compute z-scores at all 5 levels         (new)
8. Compute empirical precision weights      (new)
9. Score compound surprise + concordance    (new)
10. Emit findings via HierarchicalSurprisalDetector
```

Steps 2-6 happen after graph construction but before detector execution.

## Finding Output Example

```
[MEDIUM] Unusual code pattern in `PaymentService::process_refund`

This function is surprising at 3 of 5 hierarchy levels:

  L1 Token:          z=1.8  (within normal range for Rust)
  L2 Structural:     z=2.9  ★ (unusually complex for this project)
  L1.5 Dependency:   z=2.4  ★ (token patterns along call chain are inconsistent)
  L3 Relational:     z=1.2  (graph neighborhood is typical)
  L4 Architectural:  z=2.1  ★ (this module's coupling pattern is unusual)

Compound surprise: 7.2 (precision-weighted)
Concordance: 3/5 levels

Possible causes:
- Code was ported from a different service
- Structural complexity is inconsistent with peer functions
- Dependency-chain token patterns suggest style mismatch with callers

Research basis: Hierarchical predictive coding (Friston 2005),
naturalness of software (Ray & Hellendoorn 2015),
dependency-aware naturalness (Yang et al. 2024)
```

## Performance Budget

| Component | Estimated time (10k-node graph) |
|-----------|-------------------------------|
| L1 (n-gram training) | ~50ms |
| L2 (feature extraction + Mahalanobis) | ~10ms |
| L1.5 (dependency chain extraction + scoring) | ~100ms |
| L3 (node2vec walks × 4 edge types) | ~400ms |
| L3 (word2vec training × 4 edge types) | ~2s |
| L4 (module aggregation) | ~5ms |
| Scoring all entities | ~20ms |
| **Total** | **~2.5s** |

For typical repos (< 5k nodes), total should be under 1 second.

### Incremental Caching

- L1, L2, L4: Can use cached data when files are unchanged
- L1.5: Depends on graph structure — cache invalidates when dependencies change
- L3: Embeddings need graph rebuild (node2vec is not incremental). Cache embeddings alongside graph, invalidate when graph topology changes significantly (> 5% edge churn)

## References

1. Ray & Hellendoorn, "On the Naturalness of Buggy Code" (ICSE 2016)
2. Wang et al., "Bugram: Bug Detection with N-gram Language Models" (ASE 2016)
3. Qu et al., "node2defect: Using Network Embedding to Improve Software Defect Prediction" (ASE 2018)
4. Allamanis et al., "A Survey of Machine Learning for Big Code and Naturalness" (ACM CSUR 2018)
5. Hellendoorn et al., "Global Relational Models of Source Code" (ICLR 2020, GREAT)
6. "Why is the Mahalanobis Distance Effective for Anomaly Detection?" (arXiv 2003.00402)
7. Khanfir et al., "CodeBERT-nt: Code Naturalness via CodeBERT" (arXiv 2208.06042)
8. Zhang et al., "DSHGT: Dual-Supervisors Heterogeneous Graph Transformer" (arXiv 2306.01376)
9. Partachi & Sugiyama, "On the Naturalness of ASTs" (ICSE 2024)
10. Yang et al., "Dependency-Aware Code Naturalness" (OOPSLA 2024, DAN)
11. Zhang et al., "Analyzing Variations in Dependency Distributions Due to Code Smell Interactions" (arXiv 2509.03896)
12. Sas & Avgeriou, "Architectural Technical Debt Index Based on ML and Architectural Smells" (arXiv 2301.06341)
13. Millidge et al., "Predictive Coding: A Theoretical and Experimental Review" (arXiv 2107.12979)
14. Friston, "A Theory of Cortical Responses" (2005)
15. Bryan & Moriano, "Graph-Based ML Improves Just-in-Time Defect Prediction" (arXiv 2110.05371)
