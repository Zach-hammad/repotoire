//! Message Chain detector for Law of Demeter violations
//!
//! Detects long method chains like: a.b().c().d().e()
//! These violate the Law of Demeter by coupling to internal object structure.
//!
//! Uses both:
//! - Source code pattern matching for inline chains
//! - Call graph analysis for cross-function delegation chains

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::{debug, info};

static CHAIN_PATTERN: OnceLock<Regex> = OnceLock::new();

fn chain_pattern() -> &'static Regex {
    CHAIN_PATTERN.get_or_init(|| {
        // Match method chains: .method().method().method()
        // At least 4 chained calls
        Regex::new(r"(\.[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\)){4,}").expect("valid regex")
    })
}

/// Thresholds for message chain detection
#[derive(Debug, Clone)]
pub struct MessageChainThresholds {
    /// Minimum chain depth to report
    pub min_chain_depth: usize,
    /// Chain depth for high severity
    pub high_severity_depth: usize,
}

impl Default for MessageChainThresholds {
    fn default() -> Self {
        Self {
            min_chain_depth: 5,     // 4 was too aggressive — A→B→C→D is normal abstraction
            high_severity_depth: 8, // 6 was too aggressive for High severity
        }
    }
}

/// Patterns to exclude (builder patterns, fluent APIs)
const EXCLUDE_PATTERNS: &[&str] = &[
    "builder", "with_", "set_", "add_", "and_", "or_", "filter", "map", "reduce", "collect",
    "iter", "select", "where", "order_by", "group_by", "join", "expect", "unwrap", "ok", "err",
    "and_then",
];

/// Detects Law of Demeter violations
pub struct MessageChainDetector {
    config: DetectorConfig,
    thresholds: MessageChainThresholds,
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
}

impl MessageChainDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds: MessageChainThresholds::default(),
            repository_path: repository_path.into(),
        }
    }

    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig, repository_path: impl Into<PathBuf>) -> Self {
        let thresholds = MessageChainThresholds {
            min_chain_depth: config.get_option_or("min_chain_depth", 4),
            high_severity_depth: config.get_option_or("high_severity_depth", 6),
        };
        Self {
            config,
            thresholds,
            repository_path: repository_path.into(),
        }
    }

    /// Check if chain is a fluent API pattern (not a violation)
    fn is_fluent_pattern(&self, chain: &str) -> bool {
        let lower = chain.to_lowercase();
        EXCLUDE_PATTERNS.iter().any(|p| lower.contains(p))
    }

    /// Count depth of a method chain
    fn count_chain_depth(&self, chain: &str) -> usize {
        // Count method calls: .name()
        chain.matches(").").count() + 1
    }

    fn calculate_severity(&self, depth: usize) -> Severity {
        if depth >= self.thresholds.high_severity_depth {
            Severity::High
        } else {
            Severity::Medium
        }
    }

    /// Scan source files for method chains
    fn scan_source_files(&self, files: &dyn crate::detectors::file_provider::FileProvider) -> Vec<Finding> {
        let mut findings = Vec::new();
        let mut seen: HashSet<(String, u32)> = HashSet::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "java", "go", "rs", "rb"]) {
            // Skip test files
            let path_str = path.to_string_lossy();
            if path_str.contains("/test") || path_str.contains("_test.") {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            let rel_path = path
                .strip_prefix(files.repo_path())
                .unwrap_or(path)
                .to_path_buf();

            if let Some(content) = files.masked_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let line_num = (i + 1) as u32;

                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//")
                        || trimmed.starts_with("#")
                        || trimmed.starts_with("*")
                    {
                        continue;
                    }

                    if let Some(m) = chain_pattern().find(line) {
                        let chain = m.as_str();

                        // Skip fluent APIs
                        if self.is_fluent_pattern(chain) {
                            continue;
                        }

                        let depth = self.count_chain_depth(chain);
                        if depth < self.thresholds.min_chain_depth {
                            continue;
                        }

                        // Deduplicate
                        let key = (rel_path.to_string_lossy().to_string(), line_num);
                        if seen.contains(&key) {
                            continue;
                        }
                        seen.insert(key);

                        let severity = self.calculate_severity(depth);

                        findings.push(Finding {
                            id: String::new(),
                            detector: "MessageChainDetector".to_string(),
                            severity,
                            title: format!("Law of Demeter violation: {}-level chain", depth),
                            description: format!(
                                "Method chain with **{} levels** found:\n```\n{}\n```\n\n\
                                 This violates the Law of Demeter by coupling to internal object structure.",
                                depth, chain.trim()
                            ),
                            affected_files: vec![rel_path.clone()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Options:\n\
                                 1. Add a delegate method on the first object\n\
                                 2. Use Tell, Don't Ask - have the object do the work\n\
                                 3. Create a Facade to hide the chain"
                                    .to_string()
                            ),
                            estimated_effort: Some("Small (30 min)".to_string()),
                            category: Some("coupling".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Long method chains couple your code to internal object structure. \
                                 Changes to intermediate objects break the chain."
                                    .to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        findings
    }

    /// Use call graph to find delegation chains across functions.
    ///
    /// A delegation chain is a sequence of functions where each one just calls
    /// the next with minimal logic — pure pass-through indirection.
    ///
    /// We only report the chain HEAD (the entry point) to avoid N findings
    /// for a single chain.
    fn find_delegation_chains(&self, graph: &dyn crate::graph::GraphQuery) -> Vec<Finding> {
        let mut findings = Vec::new();
        let mut reported_in_chain: HashSet<String> = HashSet::new();

        for func in graph.get_functions() {
            // Skip if already reported as part of another chain
            if reported_in_chain.contains(&func.qualified_name) {
                continue;
            }

            let callees = graph.get_callees(&func.qualified_name);
            let callers = graph.get_callers(&func.qualified_name);

            // Chain HEAD: has callers > 1 OR callers == 0, but single callee with low complexity
            // This means it's the entry point of a chain, not a middle link
            let is_chain_head = callers.len() != 1 && callees.len() == 1;
            if !is_chain_head {
                continue;
            }

            let complexity = func.complexity().unwrap_or(1);
            if complexity > 3 {
                continue; // Not a pass-through
            }

            // Trace the chain forward
            let (chain_depth, chain_members) =
                self.trace_chain_with_members(graph, &func.qualified_name, 0);

            if chain_depth < self.thresholds.min_chain_depth as i32 {
                continue;
            }

            // Skip trait delegation chains — when most links in the chain have the
            // same function name (e.g. get_callers → self.inner.get_callers → ...).
            // This is a standard Rust/OOP pattern for trait forwarding, not a design issue.
            if self.is_trait_delegation_chain(&chain_members) {
                debug!(
                    "Skipping trait delegation chain starting at {} ({} levels, same-name forwarding)",
                    func.name, chain_depth
                );
                for member in &chain_members {
                    reported_in_chain.insert(member.clone());
                }
                continue;
            }

            // Skip chains where all functions are in the same file
            // (same-file decomposition is usually intentional)
            let all_funcs = graph.get_functions();
            let files_in_chain: HashSet<String> = chain_members
                .iter()
                .filter_map(|qn| {
                    all_funcs
                        .iter()
                        .find(|f| f.qualified_name == *qn)
                        .map(|f| f.file_path.clone())
                })
                .collect();
            if files_in_chain.len() <= 1 {
                continue; // All in same file — normal decomposition
            }

            // Mark all chain members as reported
            for member in &chain_members {
                reported_in_chain.insert(member.clone());
            }

            // Only flag with Low severity — delegation chains are a style observation
            let severity = if chain_depth >= self.thresholds.high_severity_depth as i32 {
                Severity::Medium
            } else {
                Severity::Low
            };

            findings.push(Finding {
                id: String::new(),
                detector: "MessageChainDetector".to_string(),
                severity,
                title: format!("Delegation chain: {} starts a {}-level chain", func.name, chain_depth),
                description: format!(
                    "Function '{}' is the entry point of a {}-level delegation chain across {} files.\n\n\
                     Each function in the chain just delegates to the next with minimal logic. \
                     Consider collapsing intermediate layers.",
                    func.name, chain_depth, files_in_chain.len()
                ),
                affected_files: vec![func.file_path.clone().into()],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some("Consider collapsing the delegation chain or using direct access".to_string()),
                estimated_effort: Some("Medium (1-2 hours)".to_string()),
                category: Some("coupling".to_string()),
                cwe_id: None,
                why_it_matters: Some("Deep delegation chains add indirection without value".to_string()),
                ..Default::default()
            });
        }

        findings
    }

    /// Check if a chain is trait delegation — most members share the same function name.
    /// e.g. GraphStore::get_callers → CompactGraphStore::get_callers → MmapStore::get_callers
    fn is_trait_delegation_chain(&self, chain_members: &[String]) -> bool {
        if chain_members.len() < 3 {
            return false;
        }

        // Extract the simple function name from each qualified name (last segment after ::)
        let names: Vec<&str> = chain_members
            .iter()
            .filter_map(|qn| qn.rsplit("::").next())
            .collect();

        if names.is_empty() {
            return false;
        }

        // Count how many share the most common name
        let mut freq: HashMap<&str, usize> = HashMap::new();
        for name in &names {
            *freq.entry(name).or_default() += 1;
        }

        let max_freq = freq.values().copied().max().unwrap_or(0);
        // If >50% of chain members share the same function name, it's trait delegation
        max_freq * 2 > names.len()
    }

    /// Trace how deep a delegation chain goes, collecting member names.
    #[allow(clippy::only_used_in_recursion)]
    fn trace_chain_with_members(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        qn: &str,
        depth: i32,
    ) -> (i32, Vec<String>) {
        if depth > 10 {
            return (depth, vec![qn.to_string()]);
        }

        let callees = graph.get_callees(qn);
        if callees.len() != 1 {
            return (depth, vec![qn.to_string()]);
        }

        // Check callee is also a pass-through (low complexity, single callee)
        let callee = &callees[0];
        let complexity = callee.complexity().unwrap_or(1);
        if complexity > 3 {
            return (
                depth + 1,
                vec![qn.to_string(), callee.qualified_name.clone()],
            );
        }

        let (sub_depth, mut members) =
            self.trace_chain_with_members(graph, &callee.qualified_name, depth + 1);
        members.insert(0, qn.to_string());
        (sub_depth, members)
    }
}

impl Default for MessageChainDetector {
    fn default() -> Self {
        Self::new(".")
    }
}

impl Detector for MessageChainDetector {
    fn name(&self) -> &'static str {
        "MessageChainDetector"
    }

    fn description(&self) -> &'static str {
        "Detects Law of Demeter violations through long method chains"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Source code scanning for inline chains
        findings.extend(self.scan_source_files(files));

        // Graph analysis for delegation chains
        findings.extend(self.find_delegation_chains(graph));

        info!("MessageChainDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_fluent_pattern() {
        let detector = MessageChainDetector::new(".");

        assert!(detector.is_fluent_pattern(".filter().map().collect()"));
        assert!(detector.is_fluent_pattern(".with_name().with_age().build()"));
        assert!(!detector.is_fluent_pattern(".get_user().get_profile().get_settings()"));
    }

    #[test]
    fn test_count_chain_depth() {
        let detector = MessageChainDetector::new(".");

        // .a().b() = 2 calls (a, b)
        assert_eq!(detector.count_chain_depth(".a().b()"), 2);
        // .a().b().c().d() = 4 calls
        assert_eq!(detector.count_chain_depth(".a().b().c().d()"), 4);
    }

    #[test]
    fn test_severity() {
        let detector = MessageChainDetector::new(".");

        assert_eq!(detector.calculate_severity(5), Severity::Medium);
        assert_eq!(detector.calculate_severity(7), Severity::Medium);
        assert_eq!(detector.calculate_severity(8), Severity::High);
    }
}
