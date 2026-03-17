//! Standalone metrics cache for cross-phase metric reuse.
//!
//! Extracted from `GraphStore`'s `metrics_cache: DashMap<String, f64>` field.
//! This is a simple concurrent key-value store used by detectors to cache
//! computed metrics (e.g., degree centrality, modularity) that are then
//! reused by the scoring phase.
//!
//! Key format: "metric_name:entity_qn" (e.g., "degree_centrality:module.Class")

use dashmap::DashMap;

/// Concurrent metrics cache for cross-phase metric reuse.
///
/// Thread-safe via DashMap. Detectors write computed metrics during the
/// detection phase; the scoring phase reads them without recomputation.
pub struct MetricsCache {
    cache: DashMap<String, f64>,
}

impl MetricsCache {
    /// Create a new empty metrics cache.
    pub fn new() -> Self {
        Self {
            cache: DashMap::new(),
        }
    }

    /// Store a computed metric.
    ///
    /// Key format: "metric_name:entity_qn" (e.g., "degree_centrality:module.Class")
    pub fn set(&self, key: &str, value: f64) {
        self.cache.insert(key.to_string(), value);
    }

    /// Retrieve a cached metric.
    pub fn get(&self, key: &str) -> Option<f64> {
        self.cache.get(key).map(|r| *r)
    }

    /// Get all cached metrics with a given prefix.
    ///
    /// Useful for retrieving all metrics of a type (e.g., all "modularity:" metrics).
    /// Results are sorted by key for determinism.
    pub fn get_with_prefix(&self, prefix: &str) -> Vec<(String, f64)> {
        let mut results: Vec<(String, f64)> = self
            .cache
            .iter()
            .filter(|entry| entry.key().starts_with(prefix))
            .map(|entry| (entry.key().clone(), *entry.value()))
            .collect();
        results.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        results
    }

    /// Remove all cached metrics for a set of entity qualified names.
    ///
    /// Used during delta patching when entities are removed from the graph.
    pub fn remove_for_entities(&self, entity_qns: &[String]) {
        for qn in entity_qns {
            let suffix = format!(":{}", qn);
            self.cache.retain(|k, _| !k.ends_with(&suffix));
        }
    }

    /// Clear all cached metrics.
    pub fn clear(&self) {
        self.cache.clear();
    }

    /// Number of cached entries.
    pub fn len(&self) -> usize {
        self.cache.len()
    }

    /// Check if the cache is empty.
    pub fn is_empty(&self) -> bool {
        self.cache.is_empty()
    }
}

impl Default for MetricsCache {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_set_and_get() {
        let cache = MetricsCache::new();
        cache.set("degree_centrality:module.Class", 0.75);
        assert_eq!(cache.get("degree_centrality:module.Class"), Some(0.75));
        assert_eq!(cache.get("nonexistent"), None);
    }

    #[test]
    fn test_get_with_prefix() {
        let cache = MetricsCache::new();
        cache.set("modularity:module_a", 0.8);
        cache.set("modularity:module_b", 0.6);
        cache.set("centrality:func_a", 0.9);

        let results = cache.get_with_prefix("modularity:");
        assert_eq!(results.len(), 2);
        // Should be sorted by key
        assert_eq!(results[0].0, "modularity:module_a");
        assert_eq!(results[1].0, "modularity:module_b");
    }

    #[test]
    fn test_remove_for_entities() {
        let cache = MetricsCache::new();
        cache.set("degree_centrality:a.py::foo", 0.5);
        cache.set("modularity:a.py::foo", 0.3);
        cache.set("degree_centrality:b.py::bar", 0.7);

        cache.remove_for_entities(&["a.py::foo".to_string()]);

        assert_eq!(cache.get("degree_centrality:a.py::foo"), None);
        assert_eq!(cache.get("modularity:a.py::foo"), None);
        assert_eq!(cache.get("degree_centrality:b.py::bar"), Some(0.7));
    }

    #[test]
    fn test_clear() {
        let cache = MetricsCache::new();
        cache.set("a", 1.0);
        cache.set("b", 2.0);
        assert_eq!(cache.len(), 2);

        cache.clear();
        assert_eq!(cache.len(), 0);
        assert!(cache.is_empty());
    }
}
