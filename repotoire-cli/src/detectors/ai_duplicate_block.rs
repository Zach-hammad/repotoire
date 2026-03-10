//! AI Duplicate Block Detector
//!
//! Detects near-identical code blocks that AI coding assistants tend to create
//! (copy-paste patterns). Uses AST-based similarity analysis per ICSE 2025 research.
//!
//! AI assistants often generate repetitive code with minor variations like:
//! - Different variable names but same logic
//! - Same structure with different literals
//! - Copy-paste patterns with slight modifications
//!
//! This detector uses normalized identifier hashing and Jaccard similarity
//! to find these near-duplicates. Threshold: ≥70% similarity.

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};

/// Default thresholds (based on ICSE 2025 research)
const DEFAULT_SIMILARITY_THRESHOLD: f64 = 0.70; // 70% Jaccard similarity
const DEFAULT_GENERIC_NAME_THRESHOLD: f64 = 0.40; // 40% generic identifiers
const DEFAULT_MIN_LOC: usize = 5;
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Generic identifier patterns commonly produced by AI assistants
const GENERIC_IDENTIFIERS: &[&str] = &[
    "result",
    "res",
    "ret",
    "return_value",
    "rv",
    "temp",
    "tmp",
    "t",
    "data",
    "d",
    "value",
    "val",
    "v",
    "item",
    "items",
    "i",
    "obj",
    "object",
    "o",
    "x",
    "y",
    "z",
    "a",
    "b",
    "c",
    "arr",
    "array",
    "list",
    "lst",
    "dict",
    "dictionary",
    "map",
    "mapping",
    "str",
    "string",
    "s",
    "num",
    "number",
    "n",
    "count",
    "cnt",
    "index",
    "idx",
    "key",
    "k",
    "var",
    "variable",
    "input",
    "output",
    "out",
    "response",
    "resp",
    "request",
    "req",
    "config",
    "cfg",
    "args",
    "kwargs",
    "params",
    "parameters",
    "options",
    "opts",
    "settings",
    "handler",
    "callback",
    "cb",
    "func",
    "fn",
    "function",
    "elem",
    "element",
    "node",
    "current",
    "curr",
    "cur",
    "previous",
    "prev",
    "next",
];

/// Processed function data for similarity comparison.
/// Uses an index into a shared signatures array instead of storing full HashSets.
#[derive(Debug, Clone)]
pub struct FunctionData {
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub loc: usize,
    /// Index into the shared MinHash signatures array.
    pub sig_idx: usize,
    pub generic_ratio: f64,
    pub ast_size: usize,
}

/// Pre-built set for O(1) generic identifier lookup
static GENERIC_SET: std::sync::LazyLock<HashSet<&'static str>> =
    std::sync::LazyLock::new(|| GENERIC_IDENTIFIERS.iter().copied().collect());

/// Calculate ratio of generic identifiers in a function
fn calculate_generic_ratio(identifiers: &[String]) -> f64 {
    if identifiers.is_empty() {
        return 0.0;
    }

    let generic_count = identifiers
        .iter()
        .filter(|id| GENERIC_SET.contains(id.to_lowercase().as_str()))
        .count();

    generic_count as f64 / identifiers.len() as f64
}

/// Detect near-identical code blocks typical of AI-generated code
pub struct AIDuplicateBlockDetector {
    config: DetectorConfig,
    similarity_threshold: f64,
    generic_name_threshold: f64,
    min_loc: usize,
    max_findings: usize,
}

impl AIDuplicateBlockDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            similarity_threshold: DEFAULT_SIMILARITY_THRESHOLD,
            generic_name_threshold: DEFAULT_GENERIC_NAME_THRESHOLD,
            min_loc: DEFAULT_MIN_LOC,
            max_findings: DEFAULT_MAX_FINDINGS,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            similarity_threshold: config
                .get_option_or("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD),
            generic_name_threshold: config
                .get_option_or("generic_name_threshold", DEFAULT_GENERIC_NAME_THRESHOLD),
            min_loc: config.get_option_or("min_loc", DEFAULT_MIN_LOC),
            max_findings: config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS),
            config,
        }
    }

    /// Find duplicate pairs using MinHash/LSH + MinHash-estimated Jaccard.
    ///
    /// Uses LSH banding on pre-computed MinHash signatures for candidate generation,
    /// then verifies candidates with MinHash-estimated Jaccard (±0.1 at k=100).
    /// Drops the full `HashSet<String>` bigram sets — only signatures are needed.
    fn find_duplicates(
        &self,
        functions: &[FunctionData],
        signatures: &[[u64; 100]],
    ) -> Vec<(FunctionData, FunctionData, f64)> {
        use rayon::prelude::*;

        if functions.len() < 2 {
            return Vec::new();
        }

        let candidates = crate::detectors::ast_fingerprint::lsh_candidate_pairs_from_sigs(signatures);

        debug!(
            "LSH: {} candidates from {} functions ({:.1}% of {:.0} total pairs)",
            candidates.len(),
            functions.len(),
            candidates.len() as f64 / (functions.len() as f64 * (functions.len() - 1) as f64 / 2.0) * 100.0,
            functions.len() as f64 * (functions.len() - 1) as f64 / 2.0,
        );

        // Parallel MinHash Jaccard verification on LSH candidates
        let threshold = self.similarity_threshold;
        let candidate_vec: Vec<_> = candidates.into_iter().collect();
        let mut duplicates: Vec<(FunctionData, FunctionData, f64)> = candidate_vec
            .par_iter()
            .filter_map(|&(i, j)| {
                let func1 = &functions[i];
                let func2 = &functions[j];

                // Skip same-file comparisons
                if func1.file_path == func2.file_path {
                    return None;
                }

                // Size-ratio pre-filter (Jaccard upper bound)
                if func1.ast_size > 0 && func2.ast_size > 0 {
                    let size_ratio = func1.ast_size.min(func2.ast_size) as f64
                        / func1.ast_size.max(func2.ast_size) as f64;
                    if size_ratio < threshold {
                        return None;
                    }
                }

                // MinHash-estimated Jaccard (±0.1 at k=100)
                let similarity = crate::detectors::ast_fingerprint::minhash_jaccard(
                    &signatures[func1.sig_idx],
                    &signatures[func2.sig_idx],
                );
                if similarity >= threshold {
                    Some((func1.clone(), func2.clone(), similarity))
                } else {
                    None
                }
            })
            .collect();

        // Sort by similarity (highest first)
        duplicates.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));

        duplicates
    }

    /// Create a finding from a duplicate pair
    fn create_finding(
        &self,
        func1: &FunctionData,
        func2: &FunctionData,
        similarity: f64,
    ) -> Finding {
        let similarity_pct = (similarity * 100.0) as u32;

        // Check for generic naming pattern
        let has_generic_naming = func1.generic_ratio >= self.generic_name_threshold
            || func2.generic_ratio >= self.generic_name_threshold;

        // Determine severity based on similarity and generic naming
        let severity = if similarity >= 0.90 && has_generic_naming {
            Severity::Critical
        } else if similarity >= 0.85 || (similarity >= 0.70 && has_generic_naming) {
            Severity::High
        } else {
            Severity::Medium
        };

        // Build description
        let mut description = format!(
            "Functions '{}' and '{}' have {}% AST similarity, \
             indicating structural duplication.\n\n\
             **{}** ({} LOC): `{}`\n\
             **{}** ({} LOC): `{}`\n\n",
            func1.name,
            func2.name,
            similarity_pct,
            func1.name,
            func1.loc,
            func1.file_path,
            func2.name,
            func2.loc,
            func2.file_path,
        );

        if has_generic_naming {
            let avg_generic = (func1.generic_ratio + func2.generic_ratio) / 2.0;
            description.push_str(&format!(
                "⚠️ **Generic naming detected**: {:.0}% of identifiers \
                 use generic names (result, temp, data, etc.), suggesting low-effort duplication.\n\n",
                avg_generic * 100.0
            ));
        }

        description.push_str(
            "Near-identical functions increase maintenance burden and \
             can lead to inconsistent bug fixes.",
        );

        let suggestion = "Consider one of the following approaches:\n\
             1. **Extract common logic** into a shared helper function\n\
             2. **Use a template/factory pattern** if variations are intentional\n\
             3. **Consolidate** into a single implementation if truly duplicates\n\
             4. **Add documentation** explaining why similar implementations exist"
            .to_string();

        let mut affected_files = Vec::new();
        if func1.file_path != "unknown" {
            affected_files.push(PathBuf::from(&func1.file_path));
        }
        if func2.file_path != "unknown" {
            affected_files.push(PathBuf::from(&func2.file_path));
        }

        Finding {
            id: String::new(),
            detector: "AIDuplicateBlockDetector".to_string(),
            severity,
            title: format!(
                "Structural duplicate: {} ≈ {} ({}% AST)",
                func1.name, func2.name, similarity_pct
            ),
            description,
            affected_files,
            line_start: Some(func1.line_start),
            line_end: Some(func1.line_end),
            suggested_fix: Some(suggestion),
            estimated_effort: Some("Medium (1-2 hours)".to_string()),
            category: Some("duplication".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Near-identical code duplicates increase maintenance burden. \
                 When bugs are found, they must be fixed in multiple places. \
                 When requirements change, all copies must be updated consistently."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for AIDuplicateBlockDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIDuplicateBlockDetector {
    fn name(&self) -> &'static str {
        "AIDuplicateBlockDetector"
    }

    fn description(&self) -> &'static str {
        "Detects near-identical code blocks using AST similarity (≥70%)"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        use rayon::prelude::*;

        let source_exts = &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"];

        // Collect file paths + content upfront (FileProvider requires &self borrows)
        let file_data: Vec<_> = files
            .files_with_extensions(source_exts)
            .into_iter()
            .filter(|path| !crate::detectors::base::is_test_path(&path.to_string_lossy()))
            .filter_map(|path| {
                let content = files.content(path)?;
                let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
                let has_functions = match ext {
                    "py" => content.contains("\ndef ") || content.starts_with("def "),
                    "js" | "jsx" => content.contains("function ") || content.contains("=> ") || content.contains("=>{"),
                    "ts" | "tsx" => content.contains("function ") || content.contains("=> ") || content.contains("=>{"),
                    "go" => content.contains("\nfunc ") || content.starts_with("func "),
                    "rs" => content.contains("\nfn ") || content.starts_with("fn ") || content.contains(" fn "),
                    "java" => content.contains("void ") || content.contains("public ") || content.contains("private "),
                    _ => true,
                };
                if !has_functions { return None; }
                Some((path.to_path_buf(), content, ext.to_string()))
            })
            .collect();

        // Collect functions AND their MinHash signatures in parallel.
        // FunctionData stores only a sig_idx (index into signatures vec).
        let min_loc = self.min_loc;
        let mut all_functions: Vec<FunctionData> = Vec::new();
        let mut all_signatures: Vec<[u64; 100]> = Vec::new();

        // Parallel phase: collect (FunctionData-minus-sig_idx, signature) tuples
        struct FuncWithSig {
            qualified_name: String,
            name: String,
            file_path: String,
            line_start: u32,
            line_end: u32,
            loc: usize,
            generic_ratio: f64,
            ast_size: usize,
            sig: [u64; 100],
        }

        let func_sigs: Vec<FuncWithSig> = file_data
            .par_iter()
            .flat_map_iter(|(path, content, ext)| {
                let path_str = path.to_string_lossy().to_string();

                // Fast path: use cached fingerprints + pre-computed sigs from parse phase
                if let Some(cached) = crate::parsers::get_cached_fps(&path_str) {
                    return cached
                        .into_iter()
                        .filter_map(|fp| {
                            let loc = (fp.line_end - fp.line_start + 1) as usize;
                            if loc < min_loc || fp.normalized_bigrams.is_empty() {
                                return None;
                            }
                            let generic_ratio = calculate_generic_ratio(&fp.identifiers);
                            let ast_size = fp.normalized_bigrams.len();
                            // Use pre-computed sig or compute from bigrams
                            let sig = fp.minhash_sig.unwrap_or_else(|| {
                                crate::detectors::ast_fingerprint::compute_minhash_signature(&fp.normalized_bigrams)
                            });
                            Some(FuncWithSig {
                                qualified_name: format!("{}::{}", path_str, fp.name),
                                name: fp.name,
                                file_path: path_str.clone(),
                                line_start: fp.line_start,
                                line_end: fp.line_end,
                                loc,
                                generic_ratio,
                                ast_size,
                                sig,
                            })
                        })
                        .collect::<Vec<_>>();
                }

                // Slow path: fallback to re-parsing (e.g. tests with MockFileProvider)
                let lang = crate::parsers::lightweight::Language::from_extension(ext);
                let functions = crate::detectors::ast_fingerprint::parse_functions_with_fingerprints(content, lang);

                functions.into_iter().filter_map(move |(func, fp)| {
                    let loc = (func.line_end - func.line_start + 1) as usize;
                    if loc < min_loc || fp.normalized_bigrams.is_empty() {
                        return None;
                    }
                    let generic_ratio = calculate_generic_ratio(&fp.identifiers);
                    let ast_size = fp.normalized_bigrams.len();
                    let sig = crate::detectors::ast_fingerprint::compute_minhash_signature(&fp.normalized_bigrams);
                    Some(FuncWithSig {
                        qualified_name: format!("{}::{}", path_str, func.name),
                        name: func.name,
                        file_path: path_str.clone(),
                        line_start: func.line_start,
                        line_end: func.line_end,
                        loc,
                        generic_ratio,
                        ast_size,
                        sig,
                    })
                }).collect::<Vec<_>>()
            })
            .collect();

        // Assign sig_idx sequentially (signatures must be contiguous)
        for fs in func_sigs {
            let sig_idx = all_signatures.len();
            all_signatures.push(fs.sig);
            all_functions.push(FunctionData {
                qualified_name: fs.qualified_name,
                name: fs.name,
                file_path: fs.file_path,
                line_start: fs.line_start,
                line_end: fs.line_end,
                loc: fs.loc,
                sig_idx,
                generic_ratio: fs.generic_ratio,
                ast_size: fs.ast_size,
            });
        }

        let duplicates = self.find_duplicates(&all_functions, &all_signatures);

        let mut findings = Vec::new();
        for (func1, func2, similarity) in &duplicates {
            findings.push(self.create_finding(func1, func2, *similarity));
            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!("AIDuplicateBlockDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_minhash_jaccard_identical() {
        let set: HashSet<String> = ["a", "b", "c", "d"].iter().map(|s| s.to_string()).collect();
        let sig = crate::detectors::ast_fingerprint::compute_minhash_signature(&set);
        let sim = crate::detectors::ast_fingerprint::minhash_jaccard(&sig, &sig);
        assert_eq!(sim, 1.0, "Identical sets should have Jaccard 1.0");
    }

    #[test]
    fn test_minhash_jaccard_disjoint() {
        let set1: HashSet<String> = (0..50).map(|i| format!("a_{}", i)).collect();
        let set2: HashSet<String> = (50..100).map(|i| format!("b_{}", i)).collect();
        let sig1 = crate::detectors::ast_fingerprint::compute_minhash_signature(&set1);
        let sig2 = crate::detectors::ast_fingerprint::compute_minhash_signature(&set2);
        let sim = crate::detectors::ast_fingerprint::minhash_jaccard(&sig1, &sig2);
        assert!(sim < 0.2, "Disjoint sets should have low Jaccard, got {}", sim);
    }

    #[test]
    fn test_generic_ratio() {
        let identifiers = vec![
            "result".to_string(),
            "temp".to_string(),
            "user_id".to_string(),
            "data".to_string(),
        ];
        let ratio = calculate_generic_ratio(&identifiers);
        // result, temp, data are generic (3/4 = 0.75)
        assert!((ratio - 0.75).abs() < 0.01);

        let no_generic = vec!["user_id".to_string(), "order_amount".to_string()];
        assert_eq!(calculate_generic_ratio(&no_generic), 0.0);

        let empty: Vec<String> = vec![];
        assert_eq!(calculate_generic_ratio(&empty), 0.0);
    }

    #[test]
    fn test_detector_defaults() {
        let detector = AIDuplicateBlockDetector::new();
        assert!((detector.similarity_threshold - 0.70).abs() < 0.01);
        assert!((detector.generic_name_threshold - 0.40).abs() < 0.01);
        assert_eq!(detector.min_loc, 5);
    }

    #[test]
    fn test_detects_near_duplicates() {
        // Two functions with identical structure but different variable names
        // across different files — classic Type-2 clone.
        let store = crate::graph::GraphStore::in_memory();
        let detector = AIDuplicateBlockDetector::new();
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            (
                "services/user_service.py",
                "def process_user(data):\n    result = validate(data)\n    if result is None:\n        raise ValueError('invalid')\n    output = transform(result)\n    return output\n",
            ),
            (
                "services/order_service.py",
                "def process_order(info):\n    value = validate(info)\n    if value is None:\n        raise ValueError('invalid')\n    output = transform(value)\n    return output\n",
            ),
        ]);
        let findings = detector.detect(&store, &files).expect("should detect duplicate blocks");
        assert!(
            !findings.is_empty(),
            "Should detect near-duplicate functions with same structure but different variable names"
        );
        assert_eq!(findings[0].detector, "AIDuplicateBlockDetector");
        assert!(
            findings[0].title.contains("process_user")
                || findings[0].title.contains("process_order"),
            "Finding title should reference the duplicate function names. Got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_different_functions() {
        // Two structurally different functions — should produce no duplicates.
        let store = crate::graph::GraphStore::in_memory();
        let detector = AIDuplicateBlockDetector::new();
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            (
                "auth.py",
                "def login(username, password):\n    user = authenticate(username, password)\n    if user is None:\n        raise AuthError('Invalid credentials')\n    token = create_token(user)\n    return token\n",
            ),
            (
                "export.py",
                "def export_csv(data, output_path):\n    with open(output_path, 'w') as f:\n        writer = csv.writer(f)\n        writer.writerow(data[0].keys())\n        for row in data:\n            writer.writerow(row.values())\n",
            ),
        ]);
        let findings = detector.detect(&store, &files).expect("should detect different functions");
        assert!(
            findings.is_empty(),
            "Should not flag structurally different functions. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
