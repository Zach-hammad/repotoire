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
    ) {
        // === L1: Train per-language token models ===
        let mut token_scorer = token_level::TokenLevelScorer::new();
        let repo_path = files.repo_path();
        let extensions: &[&str] = &[
            "rs", "py", "ts", "tsx", "js", "jsx", "go", "java", "c", "cpp", "cc", "h", "hpp", "cs",
        ];
        for path in files.files_with_extensions(extensions) {
            if let Some(content) = files.content(path) {
                let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
                token_scorer.train_file(&content, ext);
            }
        }

        // === Get all functions from graph ===
        let functions = graph.get_functions_shared();
        if functions.len() < 20 {
            return; // Not enough data for meaningful statistics
        }

        // === L2: Compute structural features for all functions ===
        let mut feature_vecs: Vec<Vec<f64>> = Vec::with_capacity(functions.len());
        let mut func_features: Vec<(String, Vec<f64>)> = Vec::with_capacity(functions.len());
        for func in functions.iter() {
            let params = func.param_count().unwrap_or(0);
            let complexity = func.complexity().unwrap_or(1);
            let nesting = func.get_i64("maxNesting").unwrap_or(0);
            let loc = func.loc();
            let returns = func.get_i64("returnCount").unwrap_or(1);
            let feat = structural::extract_structural_features_raw(
                params, complexity, nesting, loc, returns,
            );
            feature_vecs.push(feat.clone());
            func_features.push((func.qualified_name.clone(), feat));
        }
        let structural_scorer = structural::StructuralScorer::from_features(&feature_vecs);

        // === L1.5: Dependency chain surprisal ===
        let calls: Vec<(String, String)> = graph.get_calls();
        let chains = dependency_chain::extract_dependency_chains(&calls, 4);
        let mut chain_scorer = dependency_chain::DependencyChainScorer::new();

        // Build a lookup map to avoid O(n^2) scanning inside the chain loop
        let fn_by_name: HashMap<&str, &crate::graph::CodeNode> = functions
            .iter()
            .map(|f| (f.qualified_name.as_str(), f))
            .collect();

        // Score chains using L1 model — find a confident language model
        for chain in &chains {
            // Get representative first-line snippets for chain members
            let chain_lines: Vec<String> = chain
                .iter()
                .filter_map(|qn| fn_by_name.get(qn.as_str()).copied())
                .filter_map(|f| {
                    let path = repo_path.join(&f.file_path);
                    files.content(&path).and_then(|c| {
                        let lines: Vec<&str> = c.lines().collect();
                        let start = f.line_start.saturating_sub(1) as usize;
                        let end = (f.line_end as usize).min(lines.len());
                        if start < end {
                            Some(lines[start].to_string())
                        } else {
                            None
                        }
                    })
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

        // === L3: Relational embeddings ===
        let import_edges = graph.get_imports();
        let inheritance_edges = graph.get_inheritance();

        // Build node ID mapping: qualified_name -> u32
        let mut name_to_id: HashMap<String, u32> = HashMap::new();
        let mut next_id = 0u32;

        let call_edges_u32: Vec<(u32, u32)> = calls
            .iter()
            .map(|(a, b)| {
                let id_a = *name_to_id.entry(a.clone()).or_insert_with(|| {
                    let id = next_id;
                    next_id += 1;
                    id
                });
                let id_b = *name_to_id.entry(b.clone()).or_insert_with(|| {
                    let id = next_id;
                    next_id += 1;
                    id
                });
                (id_a, id_b)
            })
            .collect();

        let import_edges_u32: Vec<(u32, u32)> = import_edges
            .iter()
            .map(|(a, b)| {
                let id_a = *name_to_id.entry(a.clone()).or_insert_with(|| {
                    let id = next_id;
                    next_id += 1;
                    id
                });
                let id_b = *name_to_id.entry(b.clone()).or_insert_with(|| {
                    let id = next_id;
                    next_id += 1;
                    id
                });
                (id_a, id_b)
            })
            .collect();

        let inherit_edges_u32: Vec<(u32, u32)> = inheritance_edges
            .iter()
            .map(|(a, b)| {
                let id_a = *name_to_id.entry(a.clone()).or_insert_with(|| {
                    let id = next_id;
                    next_id += 1;
                    id
                });
                let id_b = *name_to_id.entry(b.clone()).or_insert_with(|| {
                    let id = next_id;
                    next_id += 1;
                    id
                });
                (id_a, id_b)
            })
            .collect();

        let num_nodes = next_id as usize;
        let mut edge_sets: Vec<(&str, Vec<(u32, u32)>)> = Vec::new();
        if !call_edges_u32.is_empty() {
            edge_sets.push(("calls", call_edges_u32));
        }
        if !import_edges_u32.is_empty() {
            edge_sets.push(("imports", import_edges_u32));
        }
        if !inherit_edges_u32.is_empty() {
            edge_sets.push(("inherits", inherit_edges_u32));
        }

        let relational_scorer = if !edge_sets.is_empty() && num_nodes > 0 {
            Some(relational::RelationalScorer::from_edge_sets(
                &edge_sets,
                num_nodes,
                64,
                Some(42),
            ))
        } else {
            None
        };

        // === L4: Architectural module profiles ===
        let mut arch_scorer = architectural::ArchitecturalScorer::new();

        // Group functions by directory (module)
        let mut module_funcs: HashMap<String, Vec<&crate::graph::CodeNode>> = HashMap::new();
        for func in functions.iter() {
            let module = func
                .file_path
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
                .map(|f| graph.call_fan_in(&f.qualified_name) as f64)
                .sum::<f64>()
                / count;
            let avg_fan_out = funcs
                .iter()
                .map(|f| graph.call_fan_out(&f.qualified_name) as f64)
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

        // === Score all functions at all 5 levels ===
        let thresholds = compound::default_thresholds();

        // First pass: collect raw scores for all functions
        struct RawScores {
            token: f64,
            structural: f64,
            dep_chain: f64,
            relational: f64,
            architectural: f64,
        }

        let mut raw_scores: Vec<(String, RawScores)> = Vec::with_capacity(functions.len());

        for (i, func) in functions.iter().enumerate() {
            let ext = func.file_path.rsplit('.').next().unwrap_or("rs");

            // L1: Token surprisal
            let token_score = if let Some(content) = files.content(&repo_path.join(&func.file_path))
            {
                let lines: Vec<&str> = content.lines().collect();
                let start = func.line_start.saturating_sub(1) as usize;
                let end = (func.line_end as usize).min(lines.len());
                if start < end && end - start >= 4 {
                    token_scorer.score_function(&lines[start..end], ext)
                } else {
                    0.0
                }
            } else {
                0.0
            };

            // L2: Structural distance
            let structural_score = func_features
                .get(i)
                .map(|(_, feat)| structural_scorer.mahalanobis_distance(feat))
                .unwrap_or(0.0);

            // L1.5: Dependency chain
            let dep_score = chain_scorer.score(&func.qualified_name);

            // L3: Relational kNN distance
            let relational_score = relational_scorer
                .as_ref()
                .and_then(|rs| {
                    name_to_id
                        .get(&func.qualified_name)
                        .map(|&id| rs.knn_distance(id, 5))
                })
                .unwrap_or(0.0);

            // L4: Module distance
            let module = func
                .file_path
                .rsplit_once('/')
                .map(|(dir, _)| dir)
                .unwrap_or("root");
            let arch_score = arch_scorer.module_distance(module);

            raw_scores.push((
                func.qualified_name.clone(),
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
        let mut engine = PredictiveCodingEngine::new();
        engine.train_and_score(&store, &empty_files);
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
