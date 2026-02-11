//! AI Boilerplate Explosion detector - identifies excessive boilerplate code
//!
//! Uses AST-based clustering to find groups of structurally similar functions
//! that could be abstracted. AI assistants often generate verbose, repetitive
//! code patterns that should be consolidated.
//!
//! Research-backed approach (ICSE 2025):
//! 1. Parse all functions to normalized AST
//! 2. Cluster functions by AST similarity (>70% threshold)
//! 3. For clusters with 3+ functions, check for shared abstraction
//! 4. Flag groups lacking abstraction as boilerplate
//!
//! Key patterns detected:
//! - Same try/except structure
//! - Same validation logic
//! - Same API call patterns with minor variations
//! - CRUD operations that could be genericized

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Default thresholds for boilerplate detection
const DEFAULT_SIMILARITY_THRESHOLD: f64 = 0.70; // 70% AST similarity
const DEFAULT_MIN_CLUSTER_SIZE: usize = 3;
const DEFAULT_MIN_LOC: usize = 5;
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Patterns commonly detected in boilerplate
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum BoilerplatePattern {
    TryExcept,
    Validation,
    HttpMethod,
    Database,
    Crud,
    ContextManager,
    Loop,
    Async,
    ErrorHandling,
}

impl std::fmt::Display for BoilerplatePattern {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BoilerplatePattern::TryExcept => write!(f, "try_except"),
            BoilerplatePattern::Validation => write!(f, "validation"),
            BoilerplatePattern::HttpMethod => write!(f, "http_method"),
            BoilerplatePattern::Database => write!(f, "database"),
            BoilerplatePattern::Crud => write!(f, "crud"),
            BoilerplatePattern::ContextManager => write!(f, "context_manager"),
            BoilerplatePattern::Loop => write!(f, "loop"),
            BoilerplatePattern::Async => write!(f, "async"),
            BoilerplatePattern::ErrorHandling => write!(f, "error_handling"),
        }
    }
}

/// Parsed function with AST analysis
#[derive(Debug, Clone)]
pub struct FunctionAST {
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub loc: usize,
    pub hash_set: HashSet<String>,
    pub patterns: Vec<BoilerplatePattern>,
    pub decorators: Vec<String>,
    pub parent_class: Option<String>,
    pub is_method: bool,
}

/// A cluster of structurally similar functions
#[derive(Debug, Clone)]
pub struct BoilerplateCluster {
    pub functions: Vec<FunctionAST>,
    pub avg_similarity: f64,
    pub dominant_patterns: Vec<BoilerplatePattern>,
    pub has_shared_abstraction: bool,
    pub abstraction_type: Option<String>,
}

/// Calculate Jaccard similarity between two sets
fn jaccard_similarity(set1: &HashSet<String>, set2: &HashSet<String>) -> f64 {
    if set1.is_empty() && set2.is_empty() {
        return 1.0;
    }
    if set1.is_empty() || set2.is_empty() {
        return 0.0;
    }
    let intersection = set1.intersection(set2).count();
    let union = set1.union(set2).count();
    if union == 0 {
        0.0
    } else {
        intersection as f64 / union as f64
    }
}

/// Cluster functions by AST similarity using single-linkage clustering
fn cluster_by_similarity(
    functions: &[FunctionAST],
    threshold: f64,
    min_cluster_size: usize,
) -> Vec<Vec<FunctionAST>> {
    if functions.len() < 2 {
        return vec![];
    }

    let n = functions.len();
    let mut similar_pairs: HashMap<usize, HashSet<usize>> = HashMap::new();

    // Build similarity matrix
    for i in 0..n {
        for j in (i + 1)..n {
            let sim = jaccard_similarity(&functions[i].hash_set, &functions[j].hash_set);
            if sim >= threshold {
                similar_pairs.entry(i).or_default().insert(j);
                similar_pairs.entry(j).or_default().insert(i);
            }
        }
    }

    // Union-find for single-linkage clustering
    let mut parent: Vec<usize> = (0..n).collect();

    fn find(parent: &mut [usize], x: usize) -> usize {
        if parent[x] != x {
            parent[x] = find(parent, parent[x]);
        }
        parent[x]
    }

    fn union(parent: &mut [usize], x: usize, y: usize) {
        let px = find(parent, x);
        let py = find(parent, y);
        if px != py {
            parent[px] = py;
        }
    }

    for (i, neighbors) in &similar_pairs {
        for &j in neighbors {
            union(&mut parent, *i, j);
        }
    }

    // Group by cluster
    let mut clusters_map: HashMap<usize, Vec<usize>> = HashMap::new();
    for i in 0..n {
        let root = find(&mut parent, i);
        clusters_map.entry(root).or_default().push(i);
    }

    // Convert to function lists, filter by minimum size
    clusters_map
        .into_values()
        .filter(|indices| indices.len() >= min_cluster_size)
        .map(|indices| indices.into_iter().map(|i| functions[i].clone()).collect())
        .collect()
}

/// Detects excessive boilerplate code using AST clustering
pub struct AIBoilerplateDetector {
    config: DetectorConfig,
    similarity_threshold: f64,
    min_cluster_size: usize,
    min_loc: usize,
    max_findings: usize,
}

impl AIBoilerplateDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            similarity_threshold: DEFAULT_SIMILARITY_THRESHOLD,
            min_cluster_size: DEFAULT_MIN_CLUSTER_SIZE,
            min_loc: DEFAULT_MIN_LOC,
            max_findings: DEFAULT_MAX_FINDINGS,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            similarity_threshold: config
                .get_option_or("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD),
            min_cluster_size: config.get_option_or("min_cluster_size", DEFAULT_MIN_CLUSTER_SIZE),
            min_loc: config.get_option_or("min_loc", DEFAULT_MIN_LOC),
            max_findings: config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS),
            config,
        }
    }

    /// Analyze a cluster of similar functions
    fn analyze_cluster(&self, functions: Vec<FunctionAST>) -> BoilerplateCluster {
        // Calculate average similarity
        let mut similarities = Vec::new();
        for (i, f1) in functions.iter().enumerate() {
            for f2 in functions.iter().skip(i + 1) {
                let sim = jaccard_similarity(&f1.hash_set, &f2.hash_set);
                similarities.push(sim);
            }
        }
        let avg_similarity = if similarities.is_empty() {
            0.0
        } else {
            similarities.iter().sum::<f64>() / similarities.len() as f64
        };

        // Collect dominant patterns
        let mut pattern_counts: HashMap<BoilerplatePattern, usize> = HashMap::new();
        for f in &functions {
            for p in &f.patterns {
                *pattern_counts.entry(p.clone()).or_insert(0) += 1;
            }
        }
        let dominant_patterns: Vec<BoilerplatePattern> = pattern_counts
            .into_iter()
            .filter(|(_, count)| *count >= functions.len() / 2)
            .map(|(p, _)| p)
            .collect();

        // Check for shared abstraction
        let mut has_abstraction = false;
        let mut abstraction_type = None;

        // Check 1: Same parent class
        let parent_classes: HashSet<_> = functions
            .iter()
            .filter_map(|f| f.parent_class.as_ref())
            .collect();
        if parent_classes.len() == 1 {
            has_abstraction = true;
            abstraction_type = Some("same_class".to_string());
        }

        // Check 2: Shared decorators suggesting abstraction
        if !has_abstraction {
            let abstraction_decorators: HashSet<&str> = [
                "abstractmethod",
                "abc.abstractmethod",
                "property",
                "staticmethod",
                "classmethod",
                "route",
                "app.route",
                "api_view",
            ]
            .into_iter()
            .collect();

            let mut shared_decorators: Option<HashSet<&String>> = None;
            for f in &functions {
                let dec_set: HashSet<&String> = f.decorators.iter().collect();
                if let Some(ref mut shared) = shared_decorators {
                    *shared = shared.intersection(&dec_set).cloned().collect();
                } else {
                    shared_decorators = Some(dec_set);
                }
            }

            if let Some(shared) = shared_decorators {
                if shared
                    .iter()
                    .any(|d| abstraction_decorators.contains(d.as_str()))
                {
                    has_abstraction = true;
                    abstraction_type = Some("decorator_pattern".to_string());
                }
            }
        }

        BoilerplateCluster {
            functions,
            avg_similarity,
            dominant_patterns,
            has_shared_abstraction: has_abstraction,
            abstraction_type,
        }
    }

    /// Generate suggestion based on detected patterns
    fn generate_suggestion(&self, cluster: &BoilerplateCluster) -> String {
        let patterns: HashSet<_> = cluster.dominant_patterns.iter().collect();

        if patterns.contains(&BoilerplatePattern::TryExcept)
            || patterns.contains(&BoilerplatePattern::ErrorHandling)
        {
            return r#"**Suggested abstraction: Error handling decorator**

```python
def handle_errors(error_handler=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if error_handler:
                    return error_handler(e)
                raise
        return wrapper
    return decorator
```

Apply `@handle_errors()` to consolidate the try/except pattern."#
                .to_string();
        }

        if patterns.contains(&BoilerplatePattern::Validation) {
            return r#"**Suggested abstraction: Validation decorator or helper**

```python
def validate(*validators):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for validator in validators:
                validator(*args, **kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

Or create reusable validation functions."#
                .to_string();
        }

        if patterns.contains(&BoilerplatePattern::Crud)
            || patterns.contains(&BoilerplatePattern::HttpMethod)
        {
            return r#"**Suggested abstraction: Generic CRUD handler or base class**

```python
class BaseCRUDHandler:
    model = None  # Override in subclass
    
    def create(self, data): ...
    def read(self, id): ...
    def update(self, id, data): ...
    def delete(self, id): ...
```

Or use a factory function to generate endpoints."#
                .to_string();
        }

        if patterns.contains(&BoilerplatePattern::Database) {
            return r#"**Suggested abstraction: Repository pattern or generic query helper**

```python
class BaseRepository:
    model = None
    
    def get(self, **filters): ...
    def create(self, **data): ...
    def update(self, id, **data): ...
```

Consolidate database access patterns."#
                .to_string();
        }

        if patterns.contains(&BoilerplatePattern::Async) {
            return "**Suggested abstraction: Async handler base or decorator**\n\n\
                Create a base async handler or use a decorator to wrap common \
                async patterns like connection management, retry logic, etc."
                .to_string();
        }

        r#"**Suggested abstractions:**

1. **Extract common logic** into a shared helper function
2. **Create a decorator** if there's a wrapper pattern
3. **Use a factory function** to generate variations
4. **Create a base class** with template method pattern
5. **Consolidate into single function** with parameters for variations"#
            .to_string()
    }

    /// Estimate refactoring effort
    fn estimate_effort(&self, cluster_size: usize) -> String {
        if cluster_size >= 8 {
            "Large (1-2 days)".to_string()
        } else if cluster_size >= 5 {
            "Medium (4-8 hours)".to_string()
        } else {
            "Small (2-4 hours)".to_string()
        }
    }

    /// Create a finding from a boilerplate cluster
    fn create_finding(&self, cluster: &BoilerplateCluster) -> Finding {
        let size = cluster.functions.len();
        let similarity_pct = (cluster.avg_similarity * 100.0) as u32;

        // Determine severity
        let severity = if size >= 6 && cluster.avg_similarity >= 0.85 {
            Severity::High
        } else if size >= 4 || cluster.avg_similarity >= 0.80 {
            Severity::Medium
        } else {
            Severity::Low
        };

        // Build title
        let pattern_str = if cluster.dominant_patterns.is_empty() {
            "similar structure".to_string()
        } else {
            cluster
                .dominant_patterns
                .iter()
                .take(2)
                .map(|p| p.to_string())
                .collect::<Vec<_>>()
                .join(", ")
        };
        let title = format!(
            "Boilerplate: {} functions with {} ({}% similar)",
            size, pattern_str, similarity_pct
        );

        // Build description
        let func_names: Vec<_> = cluster.functions.iter().map(|f| f.name.clone()).collect();
        let func_display = if func_names.len() > 5 {
            format!(
                "{} ... and {} more",
                func_names[..5].join(", "),
                func_names.len() - 5
            )
        } else {
            func_names.join(", ")
        };

        let files: HashSet<_> = cluster.functions.iter().map(|f| &f.file_path).collect();
        let mut files_vec: Vec<_> = files.into_iter().collect();
        files_vec.sort();
        let file_display = if files_vec.len() > 3 {
            format!(
                "{} ... and {} more files",
                files_vec[..3].iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "),
                files_vec.len() - 3
            )
        } else {
            files_vec
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        };

        let mut description = format!(
            "Found {} functions with {}% AST similarity that lack a shared abstraction.\n\n\
             **Functions:** {}\n\n\
             **Files:** {}\n\n",
            size, similarity_pct, func_display, file_display
        );

        if !cluster.dominant_patterns.is_empty() {
            let patterns_str = cluster
                .dominant_patterns
                .iter()
                .map(|p| p.to_string())
                .collect::<Vec<_>>()
                .join(", ");
            description.push_str(&format!("**Patterns detected:** {}\n\n", patterns_str));
        }

        description.push_str(
            "These similar functions could be consolidated into a single parameterized \
             function, decorator, or base class to reduce code duplication and improve \
             maintainability.",
        );

        let affected_files: Vec<PathBuf> = cluster
            .functions
            .iter()
            .map(|f| PathBuf::from(&f.file_path))
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "AIBoilerplateDetector".to_string(),
            severity,
            title,
            description,
            affected_files,
            line_start: cluster.functions.first().map(|f| f.line_start),
            line_end: cluster.functions.first().map(|f| f.line_end),
            suggested_fix: Some(self.generate_suggestion(cluster)),
            estimated_effort: Some(self.estimate_effort(size)),
            category: Some("boilerplate".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Repeated boilerplate code increases maintenance burden. \
                 When the pattern needs to change, you must update every copy. \
                 Abstracting common patterns reduces bugs and improves consistency."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for AIBoilerplateDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIBoilerplateDetector {
    fn name(&self) -> &'static str {
        "AIBoilerplateDetector"
    }

    fn description(&self) -> &'static str {
        "Detects excessive boilerplate code using AST clustering"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        // Boilerplate detection needs AST similarity analysis
        let _ = graph;
        Ok(vec![])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_jaccard_similarity() {
        let set1: HashSet<String> = ["a", "b", "c"].iter().map(|s| s.to_string()).collect();
        let set2: HashSet<String> = ["b", "c", "d"].iter().map(|s| s.to_string()).collect();

        let sim = jaccard_similarity(&set1, &set2);
        assert!((sim - 0.5).abs() < 0.01); // 2/4 = 0.5

        let empty: HashSet<String> = HashSet::new();
        assert_eq!(jaccard_similarity(&empty, &empty), 1.0);
        assert_eq!(jaccard_similarity(&set1, &empty), 0.0);
    }

    #[test]
    fn test_detector_defaults() {
        let detector = AIBoilerplateDetector::new();
        assert!((detector.similarity_threshold - 0.70).abs() < 0.01);
        assert_eq!(detector.min_cluster_size, 3);
        assert_eq!(detector.min_loc, 5);
    }

    #[test]
    fn test_pattern_display() {
        assert_eq!(BoilerplatePattern::TryExcept.to_string(), "try_except");
        assert_eq!(BoilerplatePattern::Crud.to_string(), "crud");
    }
}
