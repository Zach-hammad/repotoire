//! L4: Architectural surprise — module-level distributional outlier detection.
//!
//! Aggregates per-module metrics (fan-in, fan-out, cohesion, coupling, entity
//! count, smell diversity) into a 6-dimensional feature vector and uses
//! Mahalanobis distance via `StructuralScorer` to flag modules whose profile
//! deviates significantly from the codebase norm.
//!
//! Reference: Zhang et al. arXiv 2509.03896

use super::structural::StructuralScorer;
use std::collections::HashMap;

/// Aggregated metrics for a single module (directory / package).
#[derive(Debug, Clone)]
pub struct ModuleProfile {
    /// Mean incoming edges per entity in this module.
    pub avg_fan_in: f64,
    /// Mean outgoing edges per entity in this module.
    pub avg_fan_out: f64,
    /// Ratio of intra-module edges to total edges involving module entities.
    pub internal_cohesion: f64,
    /// Ratio of inter-module edges to total edges involving module entities.
    pub external_coupling: f64,
    /// Number of code entities (functions, classes, etc.) in the module.
    pub entity_count: usize,
    /// Number of distinct smell/detector types that fired in this module.
    pub smell_type_count: usize,
}

impl ModuleProfile {
    /// Convert profile to a 6-dimensional feature vector for Mahalanobis scoring.
    pub fn to_feature_vec(&self) -> Vec<f64> {
        vec![
            self.avg_fan_in,
            self.avg_fan_out,
            self.internal_cohesion,
            self.external_coupling,
            self.entity_count as f64,
            self.smell_type_count as f64,
        ]
    }
}

/// Module-level outlier detector using Mahalanobis distance.
///
/// Workflow:
/// 1. Call `add_module` for every module in the codebase.
/// 2. Call `finalize` to build the multivariate distribution model.
/// 3. Call `module_distance` to query individual module surprise.
pub struct ArchitecturalScorer {
    modules: HashMap<String, ModuleProfile>,
    scorer: Option<StructuralScorer>,
}

impl ArchitecturalScorer {
    pub fn new() -> Self {
        Self {
            modules: HashMap::new(),
            scorer: None,
        }
    }

    /// Register a module profile. Call before `finalize`.
    pub fn add_module(&mut self, module_path: &str, profile: ModuleProfile) {
        self.modules.insert(module_path.to_string(), profile);
    }

    /// Build the Mahalanobis scorer from all registered module profiles.
    ///
    /// Requires at least 3 modules to produce a meaningful covariance matrix.
    /// If fewer than 3 modules are present, the scorer remains `None` and all
    /// distances will return 0.
    pub fn finalize(&mut self) {
        let features: Vec<Vec<f64>> = self.modules.values().map(|p| p.to_feature_vec()).collect();
        if features.len() >= 3 {
            self.scorer = Some(StructuralScorer::from_features(&features));
        }
    }

    /// Mahalanobis distance for a module from the codebase-wide distribution.
    ///
    /// Returns 0.0 if the module is unknown or `finalize` has not been called
    /// (or was called with fewer than 3 modules).
    pub fn module_distance(&self, module_path: &str) -> f64 {
        let Some(profile) = self.modules.get(module_path) else {
            return 0.0;
        };
        let Some(scorer) = &self.scorer else {
            return 0.0;
        };
        scorer.mahalanobis_distance(&profile.to_feature_vec())
    }

    /// Get all registered module paths.
    pub fn module_paths(&self) -> Vec<&str> {
        self.modules.keys().map(|s| s.as_str()).collect()
    }
}

impl Default for ArchitecturalScorer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_profile(
        fan_in: f64,
        fan_out: f64,
        cohesion: f64,
        coupling: f64,
        entities: usize,
        smells: usize,
    ) -> ModuleProfile {
        ModuleProfile {
            avg_fan_in: fan_in,
            avg_fan_out: fan_out,
            internal_cohesion: cohesion,
            external_coupling: coupling,
            entity_count: entities,
            smell_type_count: smells,
        }
    }

    #[test]
    fn test_module_profile_to_feature_vec() {
        let profile = make_profile(3.5, 2.1, 0.8, 0.2, 42, 5);
        let vec = profile.to_feature_vec();

        assert_eq!(vec.len(), 6, "Feature vector should have 6 elements");
        assert!((vec[0] - 3.5).abs() < f64::EPSILON, "avg_fan_in");
        assert!((vec[1] - 2.1).abs() < f64::EPSILON, "avg_fan_out");
        assert!((vec[2] - 0.8).abs() < f64::EPSILON, "internal_cohesion");
        assert!((vec[3] - 0.2).abs() < f64::EPSILON, "external_coupling");
        assert!((vec[4] - 42.0).abs() < f64::EPSILON, "entity_count");
        assert!((vec[5] - 5.0).abs() < f64::EPSILON, "smell_type_count");
    }

    #[test]
    fn test_architectural_scorer_outlier() {
        let mut scorer = ArchitecturalScorer::new();

        // Build a large population of normal modules so the covariance estimate
        // is dominated by the "normal" cluster.  Each module has slight jitter
        // around the centre (fan_in=3, fan_out=2, cohesion=0.7, coupling=0.3,
        // entities=22, smells=2).
        let normal_names: Vec<String> = (0..30).map(|i| format!("src/mod_{i}")).collect();
        for (i, name) in normal_names.iter().enumerate() {
            let jitter = (i as f64 - 15.0) * 0.05; // small perturbation
            scorer.add_module(
                name,
                make_profile(
                    3.0 + jitter,
                    2.0 + jitter * 0.5,
                    (0.7 + jitter * 0.01).clamp(0.0, 1.0),
                    (0.3 - jitter * 0.01).clamp(0.0, 1.0),
                    (22.0 + jitter * 2.0).max(1.0) as usize,
                    2,
                ),
            );
        }

        // One outlier module: extreme values on every dimension
        scorer.add_module(
            "src/god_module",
            make_profile(50.0, 40.0, 0.1, 0.9, 500, 25),
        );

        scorer.finalize();

        let max_normal_dist = normal_names
            .iter()
            .map(|name| scorer.module_distance(name))
            .fold(0.0_f64, f64::max);
        let dist_outlier = scorer.module_distance("src/god_module");

        println!("max normal distance = {max_normal_dist:.4}");
        println!("outlier distance    = {dist_outlier:.4}");

        // With 30 normal modules the covariance is tight; the outlier should
        // stand out clearly.
        assert!(
            dist_outlier > max_normal_dist,
            "Outlier ({dist_outlier}) should exceed max normal ({max_normal_dist})"
        );
        assert!(
            dist_outlier > max_normal_dist * 1.5,
            "Outlier ({dist_outlier}) should be at least 1.5x the max normal ({max_normal_dist})"
        );
    }

    #[test]
    fn test_finalize_requires_minimum_3() {
        // With only 2 modules, scorer should stay None
        let mut scorer = ArchitecturalScorer::new();
        scorer.add_module("src/a", make_profile(1.0, 1.0, 0.5, 0.5, 10, 1));
        scorer.add_module("src/b", make_profile(2.0, 2.0, 0.6, 0.4, 15, 2));
        scorer.finalize();

        assert!(
            scorer.scorer.is_none(),
            "Scorer should be None with < 3 modules"
        );
        assert_eq!(
            scorer.module_distance("src/a"),
            0.0,
            "Distance should be 0 when scorer is None"
        );
        assert_eq!(
            scorer.module_distance("src/b"),
            0.0,
            "Distance should be 0 when scorer is None"
        );

        // With 0 modules
        let mut empty = ArchitecturalScorer::new();
        empty.finalize();
        assert!(empty.scorer.is_none());

        // With exactly 1 module
        let mut single = ArchitecturalScorer::new();
        single.add_module("src/only", make_profile(1.0, 1.0, 0.5, 0.5, 10, 1));
        single.finalize();
        assert!(single.scorer.is_none());
        assert_eq!(single.module_distance("src/only"), 0.0);
    }

    #[test]
    fn test_missing_module_returns_zero() {
        let mut scorer = ArchitecturalScorer::new();
        scorer.add_module("src/a", make_profile(1.0, 1.0, 0.5, 0.5, 10, 1));
        scorer.add_module("src/b", make_profile(2.0, 2.0, 0.6, 0.4, 15, 2));
        scorer.add_module("src/c", make_profile(3.0, 3.0, 0.7, 0.3, 20, 3));
        scorer.finalize();

        // Query a module that was never added
        assert_eq!(
            scorer.module_distance("src/nonexistent"),
            0.0,
            "Unknown module should return distance 0"
        );
        assert_eq!(
            scorer.module_distance(""),
            0.0,
            "Empty path should return distance 0"
        );
    }

    #[test]
    fn test_module_paths_returns_all_registered() {
        let mut scorer = ArchitecturalScorer::new();
        scorer.add_module("src/a", make_profile(1.0, 1.0, 0.5, 0.5, 10, 1));
        scorer.add_module("src/b", make_profile(2.0, 2.0, 0.6, 0.4, 15, 2));

        let mut paths = scorer.module_paths();
        paths.sort();
        assert_eq!(paths, vec!["src/a", "src/b"]);
    }
}
