//! Dependency Vulnerability Auditor
//!
//! Parses lockfiles (package-lock.json, Cargo.lock, requirements.txt, go.sum, etc.)
//! and checks packages against the OSV.dev vulnerability database.
//!
//! Phase 1: Online mode via OSV.dev batch API (free, no key)
//! Phase 2 (future): Offline cached advisory snapshot
//!
//! Replaces the deleted npm-audit external tool wrapper with a pure Rust,
//! multi-ecosystem implementation.

use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};

/// A parsed dependency from a lockfile
#[derive(Debug, Clone)]
pub struct Dependency {
    pub name: String,
    pub version: String,
    pub ecosystem: String,
}

/// OSV.dev batch query request
#[derive(Serialize)]
struct OsvBatchQuery {
    queries: Vec<OsvQuery>,
}

#[derive(Serialize)]
struct OsvQuery {
    package: OsvPackage,
    version: String,
}

#[derive(Serialize)]
struct OsvPackage {
    name: String,
    ecosystem: String,
}

/// OSV.dev batch response
#[derive(Deserialize, Debug)]
struct OsvBatchResponse {
    results: Vec<OsvResult>,
}

#[derive(Deserialize, Debug)]
struct OsvResult {
    #[serde(default)]
    vulns: Vec<OsvVuln>,
}

#[derive(Deserialize, Debug)]
struct OsvVuln {
    id: String,
    summary: Option<String>,
    #[serde(default)]
    severity: Vec<OsvSeverity>,
    #[serde(default)]
    affected: Vec<OsvAffected>,
    #[serde(default)]
    aliases: Vec<String>,
}

#[allow(dead_code)] // Fields populated by serde deserialization
#[derive(Deserialize, Debug)]
struct OsvSeverity {
    #[serde(rename = "type")]
    severity_type: String,
    score: String,
}

#[derive(Deserialize, Debug)]
struct OsvAffected {
    #[serde(default)]
    ranges: Vec<OsvRange>,
}

#[allow(dead_code)] // Fields populated by serde deserialization
#[derive(Deserialize, Debug)]
struct OsvRange {
    #[serde(rename = "type")]
    range_type: String,
    #[serde(default)]
    events: Vec<OsvEvent>,
}

#[allow(dead_code)] // Fields populated by serde deserialization
#[derive(Deserialize, Debug)]
struct OsvEvent {
    introduced: Option<String>,
    fixed: Option<String>,
}

pub struct DepAuditDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DepAuditDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Discover and parse all lockfiles in the repository
    fn discover_dependencies(&self) -> Vec<Dependency> {
        let mut deps = Vec::new();

        // package-lock.json (npm)
        let npm_lock = self.repository_path.join("package-lock.json");
        if npm_lock.exists() {
            deps.extend(self.parse_npm_lockfile(&npm_lock));
        }

        // Cargo.lock (Rust)
        let cargo_lock = self.repository_path.join("Cargo.lock");
        if cargo_lock.exists() {
            deps.extend(self.parse_cargo_lockfile(&cargo_lock));
        }

        // requirements.txt (Python)
        let req_txt = self.repository_path.join("requirements.txt");
        if req_txt.exists() {
            deps.extend(self.parse_requirements_txt(&req_txt));
        }

        // go.sum (Go)
        let go_sum = self.repository_path.join("go.sum");
        if go_sum.exists() {
            deps.extend(self.parse_go_sum(&go_sum));
        }

        // yarn.lock
        let yarn_lock = self.repository_path.join("yarn.lock");
        if yarn_lock.exists() {
            deps.extend(self.parse_yarn_lockfile(&yarn_lock));
        }

        // Pipfile.lock (Python)
        let pipfile_lock = self.repository_path.join("Pipfile.lock");
        if pipfile_lock.exists() {
            deps.extend(self.parse_pipfile_lock(&pipfile_lock));
        }

        // poetry.lock (Python)
        let poetry_lock = self.repository_path.join("poetry.lock");
        if poetry_lock.exists() {
            deps.extend(self.parse_poetry_lock(&poetry_lock));
        }

        // Also check subdirectories (monorepos)
        if let Ok(entries) = std::fs::read_dir(&self.repository_path) {
            for entry in entries.filter_map(|e| e.ok()) {
                let path = entry.path();
                if path.is_dir() {
                    let sub_npm = path.join("package-lock.json");
                    if sub_npm.exists() {
                        deps.extend(self.parse_npm_lockfile(&sub_npm));
                    }
                }
            }
        }

        deps
    }

    fn parse_npm_lockfile(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let json: serde_json::Value = match serde_json::from_str(&content) {
            Ok(v) => v,
            Err(_) => return vec![],
        };

        let mut deps = Vec::new();

        // npm lockfile v2/v3: packages field
        if let Some(packages) = json.get("packages").and_then(|p| p.as_object()) {
            for (key, value) in packages {
                if key.is_empty() {
                    continue; // Skip root package
                }
                let name = key.strip_prefix("node_modules/").unwrap_or(key);
                if let Some(version) = value.get("version").and_then(|v| v.as_str()) {
                    deps.push(Dependency {
                        name: name.to_string(),
                        version: version.to_string(),
                        ecosystem: "npm".to_string(),
                    });
                }
            }
        }

        // npm lockfile v1: dependencies field
        if deps.is_empty() {
            if let Some(dependencies) = json.get("dependencies").and_then(|d| d.as_object()) {
                for (name, value) in dependencies {
                    if let Some(version) = value.get("version").and_then(|v| v.as_str()) {
                        deps.push(Dependency {
                            name: name.clone(),
                            version: version.to_string(),
                            ecosystem: "npm".to_string(),
                        });
                    }
                }
            }
        }

        debug!("Parsed {} npm dependencies from {:?}", deps.len(), path);
        deps
    }

    fn parse_cargo_lockfile(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let mut deps = Vec::new();
        let mut current_name = String::new();
        let mut current_version = String::new();

        for line in content.lines() {
            if line.starts_with("name = ") {
                current_name = line
                    .trim_start_matches("name = ")
                    .trim_matches('"')
                    .to_string();
            } else if line.starts_with("version = ") {
                current_version = line
                    .trim_start_matches("version = ")
                    .trim_matches('"')
                    .to_string();
            } else if line.is_empty() && !current_name.is_empty() && !current_version.is_empty() {
                deps.push(Dependency {
                    name: current_name.clone(),
                    version: current_version.clone(),
                    ecosystem: "crates.io".to_string(),
                });
                current_name.clear();
                current_version.clear();
            }
        }

        // Don't forget the last entry
        if !current_name.is_empty() && !current_version.is_empty() {
            deps.push(Dependency {
                name: current_name,
                version: current_version,
                ecosystem: "crates.io".to_string(),
            });
        }

        debug!("Parsed {} Cargo dependencies from {:?}", deps.len(), path);
        deps
    }

    fn parse_requirements_txt(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let mut deps = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with('-') {
                continue;
            }
            // Parse "package==version" or "package>=version"
            let parts: Vec<&str> = line.splitn(2, "==").collect();
            if parts.len() == 2 {
                deps.push(Dependency {
                    name: parts[0].trim().to_string(),
                    version: parts[1].trim().to_string(),
                    ecosystem: "PyPI".to_string(),
                });
            }
        }

        debug!("Parsed {} Python dependencies from {:?}", deps.len(), path);
        deps
    }

    fn parse_go_sum(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let mut seen = std::collections::HashSet::new();
        let mut deps = Vec::new();

        for line in content.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 2 {
                let module = parts[0];
                let version = parts[1]
                    .trim_start_matches('v')
                    .split('/')
                    .next()
                    .unwrap_or("");
                let key = format!("{}@{}", module, version);
                if !seen.contains(&key) && !version.is_empty() {
                    seen.insert(key);
                    deps.push(Dependency {
                        name: module.to_string(),
                        version: version.to_string(),
                        ecosystem: "Go".to_string(),
                    });
                }
            }
        }

        debug!("Parsed {} Go dependencies from {:?}", deps.len(), path);
        deps
    }

    fn parse_yarn_lockfile(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let mut deps = Vec::new();
        let mut current_name = String::new();

        for line in content.lines() {
            let trimmed = line.trim();
            // Package header: "package@version":
            if !trimmed.starts_with('#') && trimmed.ends_with(':') && !trimmed.starts_with(' ') {
                let name = trimmed
                    .trim_end_matches(':')
                    .trim_matches('"')
                    .split('@')
                    .next()
                    .unwrap_or("")
                    .to_string();
                current_name = name;
            } else if trimmed.starts_with("version ") && !current_name.is_empty() {
                let version = trimmed
                    .trim_start_matches("version ")
                    .trim_matches('"')
                    .to_string();
                deps.push(Dependency {
                    name: current_name.clone(),
                    version,
                    ecosystem: "npm".to_string(),
                });
                current_name.clear();
            }
        }

        debug!("Parsed {} yarn dependencies from {:?}", deps.len(), path);
        deps
    }

    fn parse_pipfile_lock(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let json: serde_json::Value = match serde_json::from_str(&content) {
            Ok(v) => v,
            Err(_) => return vec![],
        };

        let mut deps = Vec::new();
        for section in &["default", "develop"] {
            if let Some(packages) = json.get(section).and_then(|s| s.as_object()) {
                for (name, value) in packages {
                    if let Some(version) = value.get("version").and_then(|v| v.as_str()) {
                        let version = version.trim_start_matches("==").to_string();
                        deps.push(Dependency {
                            name: name.clone(),
                            version,
                            ecosystem: "PyPI".to_string(),
                        });
                    }
                }
            }
        }

        debug!(
            "Parsed {} Pipfile.lock dependencies from {:?}",
            deps.len(),
            path
        );
        deps
    }

    fn parse_poetry_lock(&self, path: &Path) -> Vec<Dependency> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return vec![],
        };

        let mut deps = Vec::new();
        let mut current_name = String::new();

        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with("name = ") {
                current_name = trimmed
                    .trim_start_matches("name = ")
                    .trim_matches('"')
                    .to_string();
            } else if trimmed.starts_with("version = ") && !current_name.is_empty() {
                let version = trimmed
                    .trim_start_matches("version = ")
                    .trim_matches('"')
                    .to_string();
                deps.push(Dependency {
                    name: current_name.clone(),
                    version,
                    ecosystem: "PyPI".to_string(),
                });
                current_name.clear();
            }
        }

        debug!(
            "Parsed {} poetry.lock dependencies from {:?}",
            deps.len(),
            path
        );
        deps
    }

    /// Query OSV.dev batch API for vulnerabilities
    fn query_osv(&self, deps: &[Dependency]) -> Vec<(usize, Vec<OsvVuln>)> {
        if deps.is_empty() {
            return vec![];
        }

        // OSV batch API limit is 1000 queries per request
        let mut all_results = Vec::new();

        // Sync HTTP via ureq (no tokio needed)
        let agent = ureq::config::Config::builder()
            .http_status_as_error(false)
            .build()
            .new_agent();

        for chunk in deps.chunks(1000) {
            let query = OsvBatchQuery {
                queries: chunk
                    .iter()
                    .map(|d| OsvQuery {
                        package: OsvPackage {
                            name: d.name.clone(),
                            ecosystem: d.ecosystem.clone(),
                        },
                        version: d.version.clone(),
                    })
                    .collect(),
            };

            let result = agent
                .post("https://api.osv.dev/v1/querybatch")
                .header("Content-Type", "application/json")
                .send_json(&query);

            match result {
                Ok(response) => {
                    let text = response.into_body().read_to_string();
                    if let Ok(text) = text {
                        if let Ok(batch_response) = serde_json::from_str::<OsvBatchResponse>(&text)
                        {
                            let offset = all_results.len();
                            for (i, result) in batch_response.results.into_iter().enumerate() {
                                if !result.vulns.is_empty() {
                                    all_results.push((offset + i, result.vulns));
                                }
                            }
                        }
                    }
                }
                Err(e) => {
                    warn!("OSV.dev API request failed (working offline): {}", e);
                    // Graceful degradation — no findings from vuln DB
                }
            }
        }

        all_results
    }

    fn cvss_to_severity(score: &str) -> Severity {
        if let Ok(s) = score.parse::<f64>() {
            if s >= 9.0 {
                Severity::Critical
            } else if s >= 7.0 {
                Severity::High
            } else if s >= 4.0 {
                Severity::Medium
            } else {
                Severity::Low
            }
        } else {
            Severity::Medium // Default if unparseable
        }
    }

    fn get_fix_version(vuln: &OsvVuln) -> Option<String> {
        for affected in &vuln.affected {
            for range in &affected.ranges {
                for event in &range.events {
                    if let Some(ref fixed) = event.fixed {
                        return Some(fixed.clone());
                    }
                }
            }
        }
        None
    }
}

impl Detector for DepAuditDetector {
    fn name(&self) -> &'static str {
        "DepAuditDetector"
    }
    fn description(&self) -> &'static str {
        "Checks dependencies for known vulnerabilities via OSV.dev"
    }
    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        debug!("Starting dependency vulnerability audit");

        let deps = self.discover_dependencies();
        if deps.is_empty() {
            info!("No lockfiles found — skipping dependency audit");
            return Ok(vec![]);
        }

        info!(
            "Found {} dependencies across lockfiles, querying OSV.dev...",
            deps.len()
        );

        let vuln_results = self.query_osv(&deps);

        let mut findings = Vec::new();
        for (dep_idx, vulns) in &vuln_results {
            if let Some(dep) = deps.get(*dep_idx) {
                for vuln in vulns {
                    let severity = vuln
                        .severity
                        .first()
                        .map(|s| Self::cvss_to_severity(&s.score))
                        .unwrap_or(Severity::Medium);

                    let fix_version = Self::get_fix_version(vuln);

                    let aliases = if !vuln.aliases.is_empty() {
                        format!("\n**Aliases**: {}", vuln.aliases.join(", "))
                    } else {
                        String::new()
                    };

                    let cve_id = vuln.aliases.iter().find(|a| a.starts_with("CVE-")).cloned();

                    findings.push(Finding {
                        id: deterministic_finding_id(
                            "DepAuditDetector",
                            &dep.name,
                            0,
                            &vuln.id,
                        ),
                        detector: "DepAuditDetector".to_string(),
                        title: format!(
                            "Vulnerable dependency: {} {} ({})",
                            dep.name, dep.version, vuln.id
                        ),
                        description: format!(
                            "**{}** in `{}@{}` ({})\n\n{}{}\n\n**Advisory**: {}\n**Ecosystem**: {}{}",
                            vuln.id,
                            dep.name,
                            dep.version,
                            dep.ecosystem,
                            vuln.summary.as_deref().unwrap_or("No description available"),
                            aliases,
                            vuln.id,
                            dep.ecosystem,
                            fix_version
                                .as_ref()
                                .map(|v| format!("\n**Fix available**: Upgrade to {}", v))
                                .unwrap_or_default(),
                        ),
                        severity,
                        affected_files: vec![],
                        line_start: None,
                        line_end: None,
                        suggested_fix: fix_version
                            .map(|v| format!("Upgrade `{}` to version {} or later.", dep.name, v)),
                        cwe_id: cve_id.clone(),
                        confidence: Some(0.99), // Known vulnerability = very high confidence
                        category: Some("security".to_string()),
                        ..Default::default()
                    });

                    if findings.len() >= self.max_findings {
                        break;
                    }
                }
            }
            if findings.len() >= self.max_findings {
                break;
            }
        }

        info!(
            "DepAuditDetector found {} vulnerabilities in {} dependencies",
            findings.len(),
            deps.len()
        );

        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "security"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_cargo_lock() {
        let dir = tempfile::tempdir().unwrap();
        let lock = dir.path().join("Cargo.lock");
        std::fs::write(
            &lock,
            r#"
[[package]]
name = "serde"
version = "1.0.100"

[[package]]
name = "tokio"
version = "1.28.0"
"#,
        )
        .unwrap();

        let detector = DepAuditDetector::new(dir.path());
        let deps = detector.parse_cargo_lockfile(&lock);
        assert_eq!(deps.len(), 2);
        assert_eq!(deps[0].name, "serde");
        assert_eq!(deps[0].version, "1.0.100");
        assert_eq!(deps[0].ecosystem, "crates.io");
        assert_eq!(deps[1].name, "tokio");
    }

    #[test]
    fn test_parse_requirements_txt() {
        let dir = tempfile::tempdir().unwrap();
        let req = dir.path().join("requirements.txt");
        std::fs::write(
            &req,
            "# dependencies\nflask==2.3.0\nrequests==2.28.1\n-r other.txt\n",
        )
        .unwrap();

        let detector = DepAuditDetector::new(dir.path());
        let deps = detector.parse_requirements_txt(&req);
        assert_eq!(deps.len(), 2);
        assert_eq!(deps[0].name, "flask");
        assert_eq!(deps[0].ecosystem, "PyPI");
    }

    #[test]
    fn test_parse_npm_lockfile_v2() {
        let dir = tempfile::tempdir().unwrap();
        let lock = dir.path().join("package-lock.json");
        std::fs::write(
            &lock,
            r#"{
  "name": "test",
  "lockfileVersion": 2,
  "packages": {
    "": { "name": "test" },
    "node_modules/lodash": { "version": "4.17.21" },
    "node_modules/express": { "version": "4.18.2" }
  }
}"#,
        )
        .unwrap();

        let detector = DepAuditDetector::new(dir.path());
        let deps = detector.parse_npm_lockfile(&lock);
        assert_eq!(deps.len(), 2);
        assert!(deps.iter().any(|d| d.name == "lodash"));
        assert!(deps.iter().any(|d| d.name == "express"));
    }

    #[test]
    fn test_no_lockfiles_returns_empty() {
        let dir = tempfile::tempdir().unwrap();
        let detector = DepAuditDetector::new(dir.path());
        let deps = detector.discover_dependencies();
        assert!(deps.is_empty());
    }

    #[test]
    fn test_cvss_severity_mapping() {
        assert_eq!(
            DepAuditDetector::cvss_to_severity("9.8"),
            Severity::Critical
        );
        assert_eq!(DepAuditDetector::cvss_to_severity("7.5"), Severity::High);
        assert_eq!(DepAuditDetector::cvss_to_severity("5.0"), Severity::Medium);
        assert_eq!(DepAuditDetector::cvss_to_severity("2.0"), Severity::Low);
        assert_eq!(
            DepAuditDetector::cvss_to_severity("invalid"),
            Severity::Medium
        );
    }
}
