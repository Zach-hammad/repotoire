# Framework-Aware Scoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire `ProjectType` into `GraphScorer` so bonus thresholds adjust per project type.

**Architecture:** Add `repo_path` to `GraphScorer`, use `ProjectConfig::project_type(repo_path)` to get multipliers, scale modularity/cohesion/complexity bonus thresholds accordingly.

**Tech Stack:** Rust, existing `ProjectType` multipliers

---

### Task 1: Update `GraphScorer` to accept `repo_path` and compute project-type-aware bonuses

**Files:**
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs:195-203` (struct + constructor)
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs:504-541` (bonus calculations)

**Step 1: Add `repo_path` to `GraphScorer` struct and constructor**

Change the struct and `new()` at lines 195-203:

```rust
/// Graph-aware health scorer
pub struct GraphScorer<'a> {
    graph: &'a GraphStore,
    config: &'a ProjectConfig,
    repo_path: &'a std::path::Path,
}

impl<'a> GraphScorer<'a> {
    pub fn new(graph: &'a GraphStore, config: &'a ProjectConfig, repo_path: &'a std::path::Path) -> Self {
        Self { graph, config, repo_path }
    }
```

**Step 2: Update `calculate_modularity_bonus` to use project-type-aware thresholds**

Replace lines 504-510:

```rust
    /// Calculate modularity bonus (low coupling is good)
    fn calculate_modularity_bonus(&self, metrics: &GraphMetrics) -> f64 {
        let cm = self.config.project_type(self.repo_path).coupling_multiplier();
        // Scale thresholds by coupling multiplier:
        // - Web (1.0x): full bonus at ≤0.3, none at ≥0.7
        // - Compiler (3.0x): full bonus at ≤0.9, none at ≥1.0
        let full_threshold = (0.3 * cm).min(1.0);
        let zero_threshold = (0.7 * cm).min(1.0);
        let range = (zero_threshold - full_threshold).max(0.01);
        let coupling_score = 1.0 - ((metrics.avg_coupling - full_threshold) / range).clamp(0.0, 1.0);
        coupling_score * MAX_MODULARITY_BONUS
    }
```

**Step 3: Update `calculate_cohesion_bonus` to use project-type-aware thresholds**

Replace lines 512-518:

```rust
    /// Calculate cohesion bonus (high cohesion is good)
    fn calculate_cohesion_bonus(&self, metrics: &GraphMetrics) -> f64 {
        let cm = self.config.project_type(self.repo_path).coupling_multiplier();
        // High-coupling project types expect less cohesion
        let full_threshold = (0.7 / cm).max(0.15);
        let zero_threshold = (0.3 / cm).max(0.05);
        let range = (full_threshold - zero_threshold).max(0.01);
        let cohesion_score = ((metrics.avg_cohesion - zero_threshold) / range).clamp(0.0, 1.0);
        cohesion_score * MAX_COHESION_BONUS
    }
```

**Step 4: Update `calculate_complexity_bonus` to use project-type-aware thresholds**

Replace lines 527-533:

```rust
    /// Calculate complexity distribution bonus
    fn calculate_complexity_bonus(&self, metrics: &GraphMetrics) -> f64 {
        let xm = self.config.project_type(self.repo_path).complexity_multiplier();
        // Scale thresholds by complexity multiplier:
        // - Web (1.0x): full bonus at 90%+ simple, none at 50%
        // - Kernel (2.0x): full bonus at 45%+ simple, none at 25%
        let full_threshold = (0.9 / xm).max(0.3);
        let zero_threshold = (0.5 / xm).max(0.1);
        let range = (full_threshold - zero_threshold).max(0.01);
        let score = ((metrics.simple_function_ratio - zero_threshold) / range).clamp(0.0, 1.0);
        score * MAX_COMPLEXITY_DIST_BONUS
    }
```

**Step 5: Add debug logging for resolved project type**

In the `calculate()` method, after `let metrics = self.compute_graph_metrics();` (line 208), add:

```rust
        let project_type = self.config.project_type(self.repo_path);
        debug!(
            "Scoring with project type {:?} (coupling mult: {:.1}x, complexity mult: {:.1}x)",
            project_type,
            project_type.coupling_multiplier(),
            project_type.complexity_multiplier(),
        );
```

**Step 6: Run `cargo check`**

Run: `cargo check 2>&1`
Expected: Fails — call sites pass 2 args, now needs 3.

---

### Task 2: Update call sites to pass `repo_path`

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/scoring.rs:19`
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:237,555`

**Step 1: Update `calculate_scores()` to accept and pass `repo_path`**

In `scoring.rs`, change the function signature and body:

```rust
/// Phase 5: Calculate health scores using graph-aware scorer
pub(super) fn calculate_scores(
    graph: &Arc<GraphStore>,
    project_config: &ProjectConfig,
    findings: &[Finding],
    repo_path: &std::path::Path,
) -> ScoreResult {
    let scorer = GraphScorer::new(graph, project_config, repo_path);
```

**Step 2: Update the `calculate_scores` call in `mod.rs`**

At line 237, change:

```rust
    let score_result = calculate_scores(&graph, &env.project_config, &findings, &env.repo_path);
```

**Step 3: Update the `explain_score` scorer in `mod.rs`**

At line 555, change:

```rust
        let scorer = crate::scoring::GraphScorer::new(graph, project_config, &env.repo_path);
```

**Step 4: Run `cargo check`**

Run: `cargo check 2>&1`
Expected: Clean compile (possible warnings about unused variables in bonus methods).

**Step 5: Commit**

```bash
git add repotoire-cli/src/scoring/graph_scorer.rs repotoire-cli/src/cli/analyze/scoring.rs repotoire-cli/src/cli/analyze/mod.rs
git commit -m "feat: wire ProjectType into scoring bonuses for framework-aware scoring"
```

---

### Task 3: Update existing tests and add project-type-specific tests

**Files:**
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs:712-787` (test module)

**Step 1: Fix existing tests to pass `repo_path`**

All three existing tests create `GraphScorer::new(&graph, &config)`. Update to use a tempdir:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;
    use tempfile::TempDir;

    fn scorer_with_type(graph: &GraphStore, project_type: Option<crate::config::ProjectType>) -> (TempDir, ProjectConfig) {
        let dir = TempDir::new().unwrap();
        let config = ProjectConfig {
            project_type,
            ..ProjectConfig::default()
        };
        (dir, config)
    }

    #[test]
    fn test_empty_codebase() {
        let graph = GraphStore::in_memory();
        let (dir, config) = scorer_with_type(&graph, None);
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let breakdown = scorer.calculate(&[]);
        assert!(breakdown.overall_score >= 90.0);
    }

    #[test]
    fn test_critical_finding_caps_grade() {
        let graph = GraphStore::in_memory();
        let (dir, config) = scorer_with_type(&graph, None);
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let findings = vec![Finding {
            severity: Severity::Critical,
            detector: "test".to_string(),
            title: "Critical issue".to_string(),
            ..Default::default()
        }];

        let breakdown = scorer.calculate(&findings);
        assert!(
            breakdown.grade.starts_with('A')
                || breakdown.grade.starts_with('B')
                || breakdown.grade.starts_with('C'),
            "Expected reasonable grade, got {}",
            breakdown.grade
        );
    }

    #[test]
    fn test_graph_bonuses() {
        let graph = GraphStore::in_memory();
        use crate::graph::CodeNode;
        graph.add_node(CodeNode::file("src/main.rs"));
        graph.add_node(CodeNode::file("src/lib.rs"));
        graph.add_node(CodeNode::file("tests/test_main.rs"));
        graph.add_node(CodeNode::function("main", "src/main.rs").with_property("complexity", 5i64));
        graph.add_node(CodeNode::function("helper", "src/lib.rs").with_property("complexity", 3i64));
        graph.add_node(CodeNode::function("test_main", "tests/test_main.rs").with_property("complexity", 2i64));

        let (dir, config) = scorer_with_type(&graph, None);
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let metrics = scorer.compute_graph_metrics();
        assert_eq!(metrics.total_files, 3);
        assert_eq!(metrics.total_functions, 3);
        assert!((metrics.test_file_ratio - 0.333).abs() < 0.01, "test_file_ratio={}", metrics.test_file_ratio);
        assert_eq!(metrics.simple_function_ratio, 1.0);
    }
```

**Step 2: Add project-type-specific bonus tests**

Add these tests after the existing ones:

```rust
    #[test]
    fn test_compiler_gets_lenient_modularity_bonus() {
        let graph = GraphStore::in_memory();
        let (dir, config) = scorer_with_type(&graph, Some(crate::config::ProjectType::Compiler));
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        // Simulate high coupling metrics
        let metrics = GraphMetrics {
            avg_coupling: 0.6,  // 60% cross-module — bad for web, ok for compiler
            avg_cohesion: 0.4,
            ..Default::default()
        };

        let web_config = ProjectConfig::default();
        let web_scorer = GraphScorer::new(&graph, &web_config, dir.path());

        let compiler_bonus = scorer.calculate_modularity_bonus(&metrics);
        let web_bonus = web_scorer.calculate_modularity_bonus(&metrics);

        // Compiler should get higher modularity bonus than web for same coupling
        assert!(
            compiler_bonus > web_bonus,
            "Compiler bonus ({:.4}) should be > web bonus ({:.4}) at 60% coupling",
            compiler_bonus, web_bonus
        );
    }

    #[test]
    fn test_kernel_gets_lenient_complexity_bonus() {
        let graph = GraphStore::in_memory();
        let (dir, config) = scorer_with_type(&graph, Some(crate::config::ProjectType::Kernel));
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        // Simulate complex code — only 55% simple functions
        let metrics = GraphMetrics {
            simple_function_ratio: 0.55,
            ..Default::default()
        };

        let web_config = ProjectConfig::default();
        let web_scorer = GraphScorer::new(&graph, &web_config, dir.path());

        let kernel_bonus = scorer.calculate_complexity_bonus(&metrics);
        let web_bonus = web_scorer.calculate_complexity_bonus(&metrics);

        assert!(
            kernel_bonus > web_bonus,
            "Kernel bonus ({:.4}) should be > web bonus ({:.4}) at 55% simple",
            kernel_bonus, web_bonus
        );
    }

    #[test]
    fn test_web_default_thresholds_unchanged() {
        let graph = GraphStore::in_memory();
        let (dir, config) = scorer_with_type(&graph, Some(crate::config::ProjectType::Web));
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let metrics = GraphMetrics {
            avg_coupling: 0.5,
            avg_cohesion: 0.5,
            simple_function_ratio: 0.7,
            ..Default::default()
        };

        // Coupling 0.5 is between 0.3 (full) and 0.7 (none) — should get 50% bonus
        let mod_bonus = scorer.calculate_modularity_bonus(&metrics);
        assert!((mod_bonus - 0.05).abs() < 0.001, "Expected 0.05, got {}", mod_bonus);
    }
```

**Step 3: Run tests**

Run: `cargo test -- scoring::graph_scorer::tests -v 2>&1`
Expected: All 6 tests pass.

**Step 4: Commit**

```bash
git add repotoire-cli/src/scoring/graph_scorer.rs
git commit -m "test: add project-type-aware scoring tests"
```

---

### Task 4: Final verification

**Step 1: Run full test suite**

Run: `cargo test 2>&1`
Expected: All tests pass (800+).

**Step 2: Run cargo clippy**

Run: `cargo clippy 2>&1`
Expected: No new warnings.

**Step 3: Verify with `--explain-score` flag**

Run: `cargo run -- analyze . --explain-score 2>&1 | head -30`
Expected: Shows health score breakdown without errors.
