//! Hierarchical Predictive Coding Engine
//!
//! Applies Friston's hierarchical predictive coding theory to code analysis.
//! Five hierarchy levels independently model "what's normal" and compute
//! prediction errors (z-scores). Concordance across levels drives severity.

pub mod architectural;
pub mod compound;
pub mod dependency_chain;
pub mod embeddings;
pub mod relational;
pub mod structural;
pub mod token_level;

use crate::detectors::function_context::FunctionContextMap;
use crate::graph::GraphQueryExt;
use crate::models::Severity;

/// Prediction error at a single hierarchy level for a single entity.
#[derive(Debug, Clone)]
pub struct LevelScore {
    pub level: Level,
    pub z_score: f64,
    pub threshold: f64,
    pub is_surprising: bool,
}

/// The 5 hierarchy levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Level {
    Token,           // L1
    Structural,      // L2
    DependencyChain, // L1.5
    Relational,      // L3
    Architectural,   // L4
}

impl Level {
    pub fn label(&self) -> &'static str {
        match self {
            Level::Token => "L1 Token",
            Level::Structural => "L2 Structural",
            Level::DependencyChain => "L1.5 Dependency",
            Level::Relational => "L3 Relational",
            Level::Architectural => "L4 Architectural",
        }
    }
}

/// Per-entity compound prediction score across all hierarchy levels.
#[derive(Debug, Clone)]
pub struct CompoundScore {
    pub level_scores: Vec<LevelScore>,
    pub concordance: usize,
    pub compound_surprise: f64,
    pub severity: Severity,
}

// ---------------------------------------------------------------------------
// PredictiveCodingEngine — orchestrates all 5 hierarchy levels
// ---------------------------------------------------------------------------

use std::collections::HashMap;
use std::sync::Arc;
use tracing::debug;

/// The main engine that orchestrates all 5 hierarchy levels.
///
/// Workflow:
/// 1. Construct with `new()`.
/// 2. Call `train_and_score()` with a graph + file provider.
/// 3. Query results via `get_surprising_entities()` or `get_score()`.
pub struct PredictiveCodingEngine {
    /// Per-entity compound scores (keyed by qualified name).
    scores: HashMap<String, CompoundScore>,
    /// Number of hierarchy levels.
    level_count: usize,
}

/// Holds all trained hierarchy level models, produced by the training phase
/// and consumed by the scoring phase.
struct TrainedModels {
    token_scorer: token_level::TokenLevelScorer,
    structural_scorer: structural::StructuralScorer,
    func_features: Vec<(String, Vec<f64>)>,
    chain_scorer: dependency_chain::DependencyChainScorer,
    relational_scorer: relational::GraphRelationalScorer,
    arch_scorer: architectural::ArchitecturalScorer,
}

impl PredictiveCodingEngine {
    pub fn new() -> Self {
        Self {
            scores: HashMap::new(),
            level_count: 5,
        }
    }

    pub fn level_count(&self) -> usize {
        self.level_count
    }

    /// Train all 5 levels and score every function in the graph.
    ///
    /// Requires at least 20 functions to produce meaningful distributional
    /// statistics. With fewer functions the engine returns with no scores.
    pub fn train_and_score(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        contexts: &FunctionContextMap,
    ) {
        let all_functions = graph.get_functions_shared();
        if all_functions.len() < 20 {
            return; // Not enough data for meaningful statistics
        }

        let trained = self.train_models(graph, files, contexts, &all_functions);
        self.score_functions(graph, files, &all_functions, contexts, &trained);
    }

    /// Train all 5 hierarchy level models from graph data and file contents.
    fn train_models(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        contexts: &FunctionContextMap,
        functions: &[crate::graph::CodeNode],
    ) -> TrainedModels {
        let i = graph.interner();
        let repo_path = files.repo_path();

        // === L1: Train per-language token models ===
        let mut token_scorer = token_level::TokenLevelScorer::new();
        let extensions: &[&str] = &[
            "rs", "py", "ts", "tsx", "js", "jsx", "go", "java", "c", "cpp", "cc", "h", "hpp", "cs",
        ];
        // Train until each language model has sufficient tokens for stable n-gram statistics.
        // 50k tokens (10x confidence threshold) gives stable trigram distributions;
        // training beyond this yields diminishing returns while costing O(n) I/O.
        const L1_TOKEN_SATURATION: usize = 50_000;
        let mut lang_tokens: HashMap<&str, usize> = HashMap::new();
        for path in files.files_with_extensions(extensions) {
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if *lang_tokens.get(ext).unwrap_or(&0) >= L1_TOKEN_SATURATION {
                continue;
            }
            if let Some(content) = files.content(path) {
                token_scorer.train_file(&content, ext);
                *lang_tokens.entry(ext).or_insert(0) += content.split_whitespace().count();
            }
        }

        // === L2: Compute structural features for all functions ===
        let mut feature_vecs: Vec<Vec<f64>> = Vec::with_capacity(functions.len());
        let mut func_features: Vec<(String, Vec<f64>)> = Vec::with_capacity(functions.len());
        for func in functions.iter() {
            let params = func.param_count_opt().unwrap_or(0);
            let complexity = func.complexity_opt().unwrap_or(1);
            let nesting = func.get_i64("maxNesting").unwrap_or(0);
            let loc = func.loc();
            let returns = func.get_i64("returnCount").unwrap_or(1);
            let feat = structural::extract_structural_features_raw(
                params, complexity, nesting, loc, returns,
            );
            feature_vecs.push(feat.clone());
            func_features.push((func.qn(i).to_string(), feat));
        }
        let structural_scorer = structural::StructuralScorer::from_features(&feature_vecs);

        // === L1.5: Dependency chain surprisal ===
        let calls: Vec<(String, String)> = graph.get_calls().into_iter().map(|(a, b)| (i.resolve(a).to_string(), i.resolve(b).to_string())).collect();
        // Cap chains to 10k to avoid combinatorial explosion on large call graphs.
        // 10k chains is more than enough for distributional statistics.
        let chains = dependency_chain::extract_dependency_chains_bounded(&calls, 4, 10_000);
        let mut chain_scorer = dependency_chain::DependencyChainScorer::new();

        // Build a lookup map to avoid O(n^2) scanning inside the chain loop
        let fn_by_name: HashMap<&str, &crate::graph::CodeNode> = functions
            .iter()
            .map(|f| (f.qn(i), f))
            .collect();

        // Score chains using L1 model — find a confident language model.
        // Cache pre-split lines per file to avoid repeated lines().collect() on large files.
        let mut lines_cache: HashMap<&str, Vec<String>> = HashMap::new();
        for chain in &chains {
            // Get representative first-line snippets for chain members
            let chain_lines: Vec<String> = chain
                .iter()
                .filter_map(|qn| fn_by_name.get(qn.as_str()).copied())
                .filter_map(|f| {
                    let cached_lines = lines_cache
                        .entry(f.path(i))
                        .or_insert_with(|| {
                            let path = repo_path.join(f.path(i));
                            let content = files.content(&path).unwrap_or_else(|| Arc::new(String::new()));
                            content.lines().map(|l| l.to_string()).collect()
                        });
                    if cached_lines.is_empty() {
                        return None;
                    }
                    let start = f.line_start.saturating_sub(1) as usize;
                    let end = (f.line_end as usize).min(cached_lines.len());
                    if start < end {
                        Some(cached_lines[start].clone())
                    } else {
                        None
                    }
                })
                .filter(|s| !s.is_empty())
                .collect();

            if chain_lines.is_empty() {
                continue;
            }

            let line_refs: Vec<&str> = chain_lines.iter().map(|s| s.as_str()).collect();
            if let Some(model) = token_scorer.models.values().find(|m| m.is_confident()) {
                let surprisal = dependency_chain::chain_surprisal(model, &line_refs);
                chain_scorer.record_chain(chain, surprisal);
            }
        }
        drop(lines_cache); // Free cached lines before scoring phase
        debug!("[predictive] L1.5 extracted {} chains from {} calls", chains.len(), calls.len());

        // === L3: Relational graph features (Mahalanobis distance) ===
        let relational_scorer = relational::GraphRelationalScorer::from_contexts(contexts);

        // === L4: Architectural module profiles ===
        let mut arch_scorer = architectural::ArchitecturalScorer::new();

        // Group functions by directory (module)
        let mut module_funcs: HashMap<String, Vec<&crate::graph::CodeNode>> = HashMap::new();
        for func in functions.iter() {
            let module = func
                .path(i)
                .rsplit_once('/')
                .map(|(dir, _)| dir)
                .unwrap_or("root");
            module_funcs
                .entry(module.to_string())
                .or_default()
                .push(func);
        }
        for (module_path, funcs) in &module_funcs {
            let count = funcs.len().max(1) as f64;
            let avg_fan_in = funcs
                .iter()
                .map(|f| graph.call_fan_in(f.qn(i)) as f64)
                .sum::<f64>()
                / count;
            let avg_fan_out = funcs
                .iter()
                .map(|f| graph.call_fan_out(f.qn(i)) as f64)
                .sum::<f64>()
                / count;

            arch_scorer.add_module(
                module_path,
                architectural::ModuleProfile {
                    avg_fan_in,
                    avg_fan_out,
                    internal_cohesion: 0.5, // Placeholder — would need intra-module call analysis
                    external_coupling: 0.5, // Placeholder
                    entity_count: funcs.len(),
                    smell_type_count: 0, // Populated later if detector results available
                },
            );
        }
        arch_scorer.finalize();

        TrainedModels {
            token_scorer,
            structural_scorer,
            func_features,
            chain_scorer,
            relational_scorer,
            arch_scorer,
        }
    }

    /// Score all functions against trained models, compute z-scores, and
    /// populate `self.scores` with compound scores.
    fn score_functions(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        functions: &[crate::graph::CodeNode],
        contexts: &FunctionContextMap,
        models: &TrainedModels,
    ) {
        let i = graph.interner();
        let repo_path = files.repo_path();

        // Cap scored functions for performance. 5k is enough for robust z-scores
        // (CLT convergence). Use all functions for model training and covariance
        // estimation, but only score a subset.
        const MAX_SCORED: usize = 5_000;

        // On large repos (>MAX_SCORED functions), pre-filter by L2 structural distance
        // to avoid scoring all functions (O(n) n-gram scoring is expensive at 72k+).
        let scored_indices: Vec<usize> = if functions.len() > MAX_SCORED {
            let mut indexed_dists: Vec<(usize, f64)> = models.func_features
                .iter()
                .enumerate()
                .map(|(i, (_, feat))| (i, models.structural_scorer.mahalanobis_distance(feat)))
                .collect();
            indexed_dists.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            indexed_dists.truncate(MAX_SCORED);
            indexed_dists.into_iter().map(|(i, _)| i).collect()
        } else {
            (0..functions.len()).collect()
        };

        let thresholds = compound::default_thresholds();

        // First pass: collect raw scores for scored functions
        struct RawScores {
            token: f64,
            structural: f64,
            dep_chain: f64,
            relational: f64,
            architectural: f64,
        }

        let mut raw_scores: Vec<(String, RawScores)> = Vec::with_capacity(scored_indices.len());

        // Cache file contents to avoid repeated .content() lookups and .lines() splits
        // for functions sharing the same file.
        let mut file_cache: HashMap<&str, Option<Arc<String>>> = HashMap::new();

        for &idx in &scored_indices {
            let func = &functions[idx];
            let ext = func.path(i).rsplit('.').next().unwrap_or("rs");

            // L1: Token surprisal — skip if no confident model for this language
            let has_model = models.token_scorer
                .models
                .get(ext)
                .map(|m| m.is_confident())
                .unwrap_or(false);

            let token_score = if has_model {
                let content = file_cache
                    .entry(func.path(i))
                    .or_insert_with(|| {
                        let path = repo_path.join(func.path(i));
                        files.content(&path)
                    });
                if let Some(content) = content {
                    let lines: Vec<&str> = content.lines().collect();
                    let start = func.line_start.saturating_sub(1) as usize;
                    let end = (func.line_end as usize).min(lines.len());
                    if start < end && end - start >= 4 {
                        models.token_scorer.score_function(&lines[start..end], ext)
                    } else {
                        0.0
                    }
                } else {
                    0.0
                }
            } else {
                0.0
            };

            // L2: Structural distance
            let structural_score = models.func_features
                .get(idx)
                .map(|(_, feat)| models.structural_scorer.mahalanobis_distance(feat))
                .unwrap_or(0.0);

            // L1.5: Dependency chain
            let dep_score = models.chain_scorer.score(func.qn(i));

            // L3: Relational graph feature distance
            let relational_score = models.relational_scorer.distance(func.qn(i), contexts);

            // L4: Module distance
            let module = func
                .path(i)
                .rsplit_once('/')
                .map(|(dir, _)| dir)
                .unwrap_or("root");
            let arch_score = models.arch_scorer.module_distance(module);

            raw_scores.push((
                func.qn(i).to_string(),
                RawScores {
                    token: token_score,
                    structural: structural_score,
                    dep_chain: dep_score,
                    relational: relational_score,
                    architectural: arch_score,
                },
            ));
        }

        // Compute z-scores from raw scores
        let token_raw: Vec<f64> = raw_scores.iter().map(|(_, r)| r.token).collect();
        let struct_raw: Vec<f64> = raw_scores.iter().map(|(_, r)| r.structural).collect();
        let dep_raw: Vec<f64> = raw_scores.iter().map(|(_, r)| r.dep_chain).collect();
        let rel_raw: Vec<f64> = raw_scores.iter().map(|(_, r)| r.relational).collect();
        let arch_raw: Vec<f64> = raw_scores.iter().map(|(_, r)| r.architectural).collect();

        let token_z = z_scores_from_raw(&token_raw);
        let struct_z = z_scores_from_raw(&struct_raw);
        let dep_z = z_scores_from_raw(&dep_raw);
        let rel_z = z_scores_from_raw(&rel_raw);
        let arch_z = z_scores_from_raw(&arch_raw);

        let mut all_z_scores: HashMap<Level, Vec<f64>> = HashMap::new();
        all_z_scores.insert(Level::Token, token_z.clone());
        all_z_scores.insert(Level::Structural, struct_z.clone());
        all_z_scores.insert(Level::DependencyChain, dep_z.clone());
        all_z_scores.insert(Level::Relational, rel_z.clone());
        all_z_scores.insert(Level::Architectural, arch_z.clone());

        let weights = compound::compute_precision_weights(&all_z_scores);

        // Build CompoundScores for each function
        for (i, (qn, _)) in raw_scores.iter().enumerate() {
            let levels = vec![
                LevelScore {
                    level: Level::Token,
                    z_score: token_z[i],
                    threshold: *thresholds.get(&Level::Token).unwrap_or(&2.5),
                    is_surprising: token_z[i] > *thresholds.get(&Level::Token).unwrap_or(&2.5),
                },
                LevelScore {
                    level: Level::Structural,
                    z_score: struct_z[i],
                    threshold: *thresholds.get(&Level::Structural).unwrap_or(&2.0),
                    is_surprising: struct_z[i]
                        > *thresholds.get(&Level::Structural).unwrap_or(&2.0),
                },
                LevelScore {
                    level: Level::DependencyChain,
                    z_score: dep_z[i],
                    threshold: *thresholds.get(&Level::DependencyChain).unwrap_or(&2.0),
                    is_surprising: dep_z[i]
                        > *thresholds.get(&Level::DependencyChain).unwrap_or(&2.0),
                },
                LevelScore {
                    level: Level::Relational,
                    z_score: rel_z[i],
                    threshold: *thresholds.get(&Level::Relational).unwrap_or(&1.5),
                    is_surprising: rel_z[i] > *thresholds.get(&Level::Relational).unwrap_or(&1.5),
                },
                LevelScore {
                    level: Level::Architectural,
                    z_score: arch_z[i],
                    threshold: *thresholds.get(&Level::Architectural).unwrap_or(&2.0),
                    is_surprising: arch_z[i]
                        > *thresholds.get(&Level::Architectural).unwrap_or(&2.0),
                },
            ];

            let score = compound::score_entity(levels, &weights);
            if score.concordance >= 1 {
                self.scores.insert(qn.clone(), score);
            }
        }
    }

    /// Get all scored entities with concordance >= `min_concordance`,
    /// sorted by `compound_surprise` descending.
    pub fn get_surprising_entities(&self, min_concordance: usize) -> Vec<(&str, &CompoundScore)> {
        let mut results: Vec<(&str, &CompoundScore)> = self
            .scores
            .iter()
            .filter(|(_, s)| s.concordance >= min_concordance)
            .map(|(k, v)| (k.as_str(), v))
            .collect();
        results.sort_by(|a, b| {
            b.1.compound_surprise
                .partial_cmp(&a.1.compound_surprise)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        results
    }

    /// Get the compound score for a specific entity.
    pub fn get_score(&self, qualified_name: &str) -> Option<&CompoundScore> {
        self.scores.get(qualified_name)
    }
}

impl Default for PredictiveCodingEngine {
    fn default() -> Self {
        Self::new()
    }
}

/// Convert raw score values to z-scores (standard score normalization).
fn z_scores_from_raw(values: &[f64]) -> Vec<f64> {
    if values.len() < 2 {
        return vec![0.0; values.len()];
    }
    let n = values.len() as f64;
    let mean = values.iter().sum::<f64>() / n;
    let variance = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n;
    let std = variance.sqrt();
    if std < 1e-10 {
        return vec![0.0; values.len()];
    }
    values.iter().map(|v| (v - mean) / std).collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_engine_constructible() {
        let engine = PredictiveCodingEngine::new();
        assert_eq!(engine.level_count(), 5);
        assert!(engine.get_surprising_entities(1).is_empty());
    }

    #[test]
    fn test_engine_empty_graph() {
        let store = crate::graph::GraphStore::in_memory();
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let contexts = std::collections::HashMap::new();
        let mut engine = PredictiveCodingEngine::new();
        engine.train_and_score(&store, &empty_files, &contexts);
        assert!(engine.get_surprising_entities(1).is_empty());
    }

    #[test]
    fn test_z_scores_from_raw_basic() {
        // Values: [0, 10] → mean=5, std=5, z-scores=[-1, 1]
        let z = z_scores_from_raw(&[0.0, 10.0]);
        assert!((z[0] - (-1.0)).abs() < 1e-9);
        assert!((z[1] - 1.0).abs() < 1e-9);
    }

    #[test]
    fn test_z_scores_from_raw_constant() {
        // All identical → zero variance → all z-scores should be 0
        let z = z_scores_from_raw(&[5.0, 5.0, 5.0]);
        for val in &z {
            assert!(val.abs() < 1e-9);
        }
    }

    #[test]
    fn test_z_scores_from_raw_single_value() {
        let z = z_scores_from_raw(&[42.0]);
        assert_eq!(z.len(), 1);
        assert!(z[0].abs() < 1e-9);
    }

    #[test]
    fn test_z_scores_from_raw_empty() {
        let z = z_scores_from_raw(&[]);
        assert!(z.is_empty());
    }

    #[test]
    fn test_engine_default() {
        let engine = PredictiveCodingEngine::default();
        assert_eq!(engine.level_count(), 5);
    }
}
