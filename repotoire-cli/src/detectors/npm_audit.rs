//! npm/bun audit security detector
//!
//! Uses npm audit (or bun audit) to detect known vulnerabilities
//! in JavaScript/TypeScript dependencies.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{get_graph_context, get_js_runtime, run_external_tool, JsRuntime};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use serde_json::Value as JsonValue;
use std::path::{Path, PathBuf};
use tracing::{debug, info, warn};
use uuid::Uuid;

/// npm audit security detector
pub struct NpmAuditDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    min_severity: String,
    production_only: bool,
}

impl NpmAuditDetector {
    /// Create a new npm audit detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            min_severity: "low".to_string(),
            production_only: false,
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set minimum severity to report
    pub fn with_min_severity(mut self, severity: impl Into<String>) -> Self {
        self.min_severity = severity.into();
        self
    }

    /// Only check production dependencies
    pub fn with_production_only(mut self, prod_only: bool) -> Self {
        self.production_only = prod_only;
        self
    }

    /// Check if required files exist
    fn check_prerequisites(&self) -> bool {
        let package_json = self.repository_path.join("package.json");
        if !package_json.exists() {
            info!("No package.json found, skipping npm audit");
            return false;
        }

        // Check for lock file
        let has_lock = self.repository_path.join("package-lock.json").exists()
            || self.repository_path.join("yarn.lock").exists()
            || self.repository_path.join("pnpm-lock.yaml").exists()
            || self.repository_path.join("bun.lockb").exists();

        if !has_lock {
            warn!(
                "No lock file found (package-lock.json, yarn.lock, pnpm-lock.yaml, or bun.lockb). \
                 npm audit requires a lock file. Run 'npm install' first."
            );
            return false;
        }

        true
    }

    /// Run npm/bun audit and parse results
    fn run_audit(&self) -> Vec<Vulnerability> {
        if !self.check_prerequisites() {
            return Vec::new();
        }

        // Detect lock file type and runtime
        let has_yarn_lock = self.repository_path.join("yarn.lock").exists();
        let has_pnpm_lock = self.repository_path.join("pnpm-lock.yaml").exists();
        let has_bun_lock = self.repository_path.join("bun.lockb").exists();
        let runtime = get_js_runtime();

        let mut cmd = if has_yarn_lock {
            vec!["yarn".to_string(), "audit".to_string(), "--json".to_string()]
        } else if has_pnpm_lock {
            vec!["pnpm".to_string(), "audit".to_string(), "--json".to_string()]
        } else if has_bun_lock && runtime == JsRuntime::Bun {
            vec!["bun".to_string(), "audit".to_string(), "--json".to_string()]
        } else {
            vec!["npm".to_string(), "audit".to_string(), "--json".to_string()]
        };

        if self.production_only {
            match cmd[0].as_str() {
                "npm" => cmd.push("--omit=dev".to_string()),
                "yarn" => cmd.push("--groups=production".to_string()),
                "pnpm" => cmd.push("--prod".to_string()),
                _ => {}
            }
        }

        let result = run_external_tool(&cmd, "npm audit", 120, Some(&self.repository_path), None);

        // npm audit returns non-zero exit code when vulnerabilities found
        if result.stdout.is_empty() {
            return Vec::new();
        }

        let audit_data: JsonValue = match serde_json::from_str(&result.stdout) {
            Ok(data) => data,
            Err(e) => {
                warn!("Failed to parse npm audit JSON: {}", e);
                return Vec::new();
            }
        };

        let mut vulnerabilities = Vec::new();

        // npm v7+ format
        if let Some(vulns) = audit_data.get("vulnerabilities").and_then(|v| v.as_object()) {
            for (pkg_name, vuln_data) in vulns {
                let severity = vuln_data.get("severity").and_then(|s| s.as_str()).unwrap_or("info");
                let via = vuln_data.get("via").and_then(|v| v.as_array()).map(|a| a.as_slice()).unwrap_or(&[]);

                for v in via {
                    if let Some(title) = v.get("title").and_then(|t| t.as_str()) {
                        vulnerabilities.push(Vulnerability {
                            package: pkg_name.clone(),
                            severity: severity.to_string(),
                            title: title.to_string(),
                            url: v.get("url").and_then(|u| u.as_str()).map(String::from),
                            cwe: v.get("cwe").and_then(|c| c.as_array()).map(|arr| {
                                arr.iter().filter_map(|c| c.as_str().map(String::from)).collect()
                            }).unwrap_or_default(),
                            range: v.get("range").and_then(|r| r.as_str()).unwrap_or(
                                vuln_data.get("range").and_then(|r| r.as_str()).unwrap_or("*")
                            ).to_string(),
                            fix_available: vuln_data.get("fixAvailable").and_then(|f| f.as_bool()).unwrap_or(false),
                        });
                    }
                }
            }
        }
        // npm v6 format
        else if let Some(advisories) = audit_data.get("advisories").and_then(|a| a.as_object()) {
            for (_, advisory) in advisories {
                vulnerabilities.push(Vulnerability {
                    package: advisory.get("module_name").and_then(|n| n.as_str()).unwrap_or("unknown").to_string(),
                    severity: advisory.get("severity").and_then(|s| s.as_str()).unwrap_or("info").to_string(),
                    title: advisory.get("title").and_then(|t| t.as_str()).unwrap_or("Unknown vulnerability").to_string(),
                    url: advisory.get("url").and_then(|u| u.as_str()).map(String::from),
                    cwe: advisory.get("cwe").and_then(|c| c.as_str()).map(|c| vec![c.to_string()]).unwrap_or_default(),
                    range: advisory.get("vulnerable_versions").and_then(|v| v.as_str()).unwrap_or("*").to_string(),
                    fix_available: advisory.get("patched_versions").and_then(|p| p.as_str()).map(|p| p != "<0.0.0").unwrap_or(false),
                });
            }
        }

        info!("npm audit found {} vulnerabilities", vulnerabilities.len());

        // Filter by severity
        let severity_order = ["critical", "high", "moderate", "low", "info"];
        let min_idx = severity_order.iter().position(|&s| s == self.min_severity.to_lowercase()).unwrap_or(3);

        vulnerabilities
            .into_iter()
            .filter(|v| {
                severity_order.iter().position(|&s| s == v.severity.to_lowercase()).unwrap_or(4) <= min_idx
            })
            .collect()
    }

    /// Map npm audit severity to our severity
    fn map_severity(npm_severity: &str) -> Severity {
        match npm_severity.to_lowercase().as_str() {
            "critical" => Severity::Critical,
            "high" => Severity::High,
            "moderate" => Severity::Medium,
            "low" => Severity::Low,
            _ => Severity::Info,
        }
    }

    /// Find files that import a vulnerable package
    fn find_importing_files(&self, graph: &GraphStore, package: &str) -> Vec<String> {
        // Get all import edges and filter for the package
        let imports = graph.get_imports();
        let mut files: Vec<String> = imports
            .iter()
            .filter(|(_, imported)| {
                imported.ends_with(package) || imported == package
            })
            .map(|(importer, _)| importer.clone())
            .take(10)
            .collect();
        
        files.sort();
        files.dedup();
        files
    }

    /// Create finding from vulnerability
    fn create_finding(&self, vuln: &Vulnerability, graph: &GraphStore) -> Finding {
        let affected_files = self.find_importing_files(graph, &vuln.package);
        let severity = Self::map_severity(&vuln.severity);

        // Build description
        let mut description = format!(
            "**{}**\n\n\
             **Package**: {}\n\
             **Severity**: {}\n\
             **Vulnerable versions**: {}\n",
            vuln.title,
            vuln.package,
            vuln.severity.to_uppercase(),
            vuln.range
        );

        if let Some(url) = &vuln.url {
            description.push_str(&format!("**Advisory**: {}\n", url));
        }

        if !vuln.cwe.is_empty() {
            description.push_str(&format!("**CWE**: {}\n", vuln.cwe.join(", ")));
        }

        if !affected_files.is_empty() {
            description.push_str(&format!("\n**Affected files** ({}):\n", affected_files.len()));
            for f in affected_files.iter().take(5) {
                description.push_str(&format!("  - {}\n", f));
            }
            if affected_files.len() > 5 {
                description.push_str(&format!("  - ... and {} more\n", affected_files.len() - 5));
            }
        }

        let suggested_fix = if vuln.fix_available {
            format!("Run `npm audit fix` or manually update {} to a patched version", vuln.package)
        } else {
            format!("Check for alternative packages or apply workarounds for {}. See advisory for details.", vuln.package)
        };

        let effort = if vuln.fix_available {
            "Small (15-30 minutes)"
        } else {
            "Medium (1-2 hours)"
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "NpmAuditDetector".to_string(),
            severity,
            title: format!("Vulnerable dependency: {}", vuln.package),
            description,
            affected_files: if affected_files.is_empty() {
                vec![PathBuf::from("package.json")]
            } else {
                affected_files.iter().map(PathBuf::from).collect()
            },
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(effort.to_string()),
            category: Some("security".to_string()),
            cwe_id: vuln.cwe.first().cloned(),
            why_it_matters: Some(format!(
                "This dependency ({}) has a known security vulnerability that could be exploited by attackers.",
                vuln.package
            )),
        }
    }
}

/// Parsed vulnerability
struct Vulnerability {
    package: String,
    severity: String,
    title: String,
    url: Option<String>,
    cwe: Vec<String>,
    range: String,
    fix_available: bool,
}

impl Detector for NpmAuditDetector {
    fn name(&self) -> &'static str {
        "NpmAuditDetector"
    }

    fn description(&self) -> &'static str {
        "Detects security vulnerabilities in npm dependencies"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        info!("Running npm audit on {:?}", self.repository_path);

        let vulnerabilities = self.run_audit();

        if vulnerabilities.is_empty() {
            info!("No security vulnerabilities found");
            return Ok(Vec::new());
        }

        let findings: Vec<Finding> = vulnerabilities
            .iter()
            .take(self.max_findings)
            .map(|v| self.create_finding(v, graph))
            .collect();

        info!("Created {} security findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_mapping() {
        assert_eq!(NpmAuditDetector::map_severity("critical"), Severity::Critical);
        assert_eq!(NpmAuditDetector::map_severity("high"), Severity::High);
        assert_eq!(NpmAuditDetector::map_severity("moderate"), Severity::Medium);
        assert_eq!(NpmAuditDetector::map_severity("low"), Severity::Low);
        assert_eq!(NpmAuditDetector::map_severity("info"), Severity::Info);
    }
}
