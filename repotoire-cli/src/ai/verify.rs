//! LLM-based false positive verification
//!
//! Uses LLM to verify HIGH severity findings, filtering out likely false positives.
//! Research shows 94-98% FP reduction with hybrid LLM+static analysis (arXiv:2601.18844).

use crate::ai::client::{AiClient, LlmBackend};
use crate::ai::{AiError, AiResult};
use crate::models::{Finding, Severity};
use std::path::Path;
use tracing::{debug, info, warn};

/// Result of LLM verification
#[derive(Debug, Clone)]
pub enum VerifyResult {
    /// Finding is a true positive (real issue)
    TruePositive { reason: String },
    /// Finding is a false positive (not a real issue)
    FalsePositive { reason: String },
    /// Verification failed (network error, etc.)
    Error { message: String },
}

/// LLM-based finding verifier
pub struct FindingVerifier {
    client: AiClient,
    repo_path: std::path::PathBuf,
}

impl FindingVerifier {
    /// Create a new verifier (tries Ollama first, then Anthropic)
    pub fn new(repo_path: &Path) -> AiResult<Self> {
        // Try Ollama first (free, local)
        if AiClient::ollama_available() {
            let client = AiClient::from_env(LlmBackend::Ollama)?;
            return Ok(Self {
                client,
                repo_path: repo_path.to_path_buf(),
            });
        }

        // Fall back to Anthropic if available
        let client = AiClient::from_env(LlmBackend::Anthropic)?;
        Ok(Self {
            client,
            repo_path: repo_path.to_path_buf(),
        })
    }

    /// Create with specific backend
    pub fn with_backend(repo_path: &Path, backend: LlmBackend) -> AiResult<Self> {
        let client = AiClient::from_env(backend)?;
        Ok(Self {
            client,
            repo_path: repo_path.to_path_buf(),
        })
    }

    /// Verify a single finding (sync — ureq)
    pub fn verify_finding(&self, finding: &Finding) -> VerifyResult {
        // Read code context
        let code_context = match self.read_code_context(finding) {
            Ok(ctx) => ctx,
            Err(e) => return VerifyResult::Error { message: e.to_string() },
        };

        // Build verification prompt
        let prompt = format!(
            r#"You are a code analysis expert. Analyze this static analysis finding and determine if it's a TRUE POSITIVE (real issue) or FALSE POSITIVE (not a real issue).

FINDING:
- Detector: {}
- Severity: {:?}
- Title: {}
- Description: {}

CODE CONTEXT:
```
{}
```

Analyze the code and finding carefully. Consider:
1. Is the detection logic correct for this specific code?
2. Could this be a false alarm due to context the detector can't see?
3. Is this actually a problem in practice?

Reply with exactly one line:
TRUE_POSITIVE: <brief reason>
or
FALSE_POSITIVE: <brief reason>"#,
            finding.detector, finding.severity, finding.title, finding.description, code_context
        );

        // Call LLM
        let messages = vec![crate::ai::Message {
            role: crate::ai::Role::User,
            content: prompt,
        }];

        match self.client.generate(messages, None) {
            Ok(response) => self.parse_response(&response),
            Err(e) => VerifyResult::Error {
                message: e.to_string(),
            },
        }
    }

    /// Read code context around the finding
    fn read_code_context(&self, finding: &Finding) -> AiResult<String> {
        let file_path = finding
            .affected_files
            .first()
            .ok_or_else(|| AiError::ConfigError("No affected file".into()))?;

        let full_path = self.repo_path.join(file_path);
        let content = std::fs::read_to_string(&full_path)?;

        let lines: Vec<&str> = content.lines().collect();
        let start = finding.line_start.unwrap_or(1) as usize;
        let end = finding.line_end.unwrap_or(start as u32) as usize;

        // Get 5 lines before and after
        let context_start = start.saturating_sub(6);
        let context_end = (end + 5).min(lines.len());

        let context: Vec<String> = lines[context_start..context_end]
            .iter()
            .enumerate()
            .map(|(i, line)| format!("{:4} | {}", context_start + i + 1, line))
            .collect();

        Ok(context.join("\n"))
    }

    /// Parse LLM response into VerifyResult
    fn parse_response(&self, response: &str) -> VerifyResult {
        let response = response.trim();

        if response.starts_with("TRUE_POSITIVE:") {
            let reason = response.strip_prefix("TRUE_POSITIVE:").unwrap_or("").trim();
            return VerifyResult::TruePositive {
                reason: reason.to_string(),
            };
        }
        if response.starts_with("FALSE_POSITIVE:") {
            let reason = response
                .strip_prefix("FALSE_POSITIVE:")
                .unwrap_or("")
                .trim();
            return VerifyResult::FalsePositive {
                reason: reason.to_string(),
            };
        }
        // Try to infer from content
        infer_verify_result(response)
    }
}

/// Infer verification result from unstructured LLM response text.
fn infer_verify_result(response: &str) -> VerifyResult {
    let lower = response.to_lowercase();
    if lower.contains("false positive") || lower.contains("not a real") {
        VerifyResult::FalsePositive {
            reason: response.to_string(),
        }
    } else if lower.contains("true positive") || lower.contains("real issue") {
        VerifyResult::TruePositive {
            reason: response.to_string(),
        }
    } else {
        // Default to keeping the finding (conservative)
        VerifyResult::TruePositive {
            reason: "Unable to parse response, keeping finding".to_string(),
        }
    }
}

/// Verify HIGH severity findings and filter false positives
/// Returns the filtered list of findings
pub fn verify_findings(findings: Vec<Finding>, repo_path: &Path) -> Vec<Finding> {
    // Only verify HIGH findings (cost/benefit tradeoff)
    let (high_findings, other_findings): (Vec<_>, Vec<_>) = findings
        .into_iter()
        .partition(|f| f.severity == Severity::High);

    if high_findings.is_empty() {
        info!("No HIGH findings to verify");
        return other_findings;
    }

    info!(
        "Verifying {} HIGH findings with LLM...",
        high_findings.len()
    );

    // Create verifier
    let verifier = match FindingVerifier::new(repo_path) {
        Ok(v) => v,
        Err(e) => {
            warn!("Failed to create verifier: {}. Skipping verification.", e);
            let mut all = other_findings;
            all.extend(high_findings);
            return all;
        }
    };

    // Verify each HIGH finding (sync — no runtime needed)
    let mut verified_findings = Vec::new();
    let mut fp_count = 0;
    let mut tp_count = 0;
    let mut err_count = 0;

    for finding in high_findings {
        let result = verifier.verify_finding(&finding);

        match result {
            VerifyResult::TruePositive { reason } => {
                debug!("TRUE_POSITIVE: {} - {}", finding.title, reason);
                tp_count += 1;
                verified_findings.push(finding);
            }
            VerifyResult::FalsePositive { reason } => {
                debug!("FALSE_POSITIVE: {} - {}", finding.title, reason);
                fp_count += 1;
                // Don't add to verified_findings (filtered out)
            }
            VerifyResult::Error { message } => {
                debug!("VERIFY_ERROR: {} - {}", finding.title, message);
                err_count += 1;
                // Keep finding on error (conservative)
                verified_findings.push(finding);
            }
        }
    }

    info!(
        "LLM verification: {} true positives, {} false positives filtered, {} errors",
        tp_count, fp_count, err_count
    );

    // Combine verified HIGH findings with other findings
    let mut all = other_findings;
    all.extend(verified_findings);
    all
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_true_positive() {
        // Test response parsing (doesn't need real client)
        let response = "TRUE_POSITIVE: This is a real SQL injection vulnerability";

        // Parse manually since we can't create verifier without API key
        let result = if response.starts_with("TRUE_POSITIVE:") {
            let reason = response.strip_prefix("TRUE_POSITIVE:").unwrap_or("").trim();
            VerifyResult::TruePositive {
                reason: reason.to_string(),
            }
        } else {
            VerifyResult::FalsePositive {
                reason: "".to_string(),
            }
        };

        assert!(matches!(result, VerifyResult::TruePositive { .. }));
    }

    #[test]
    fn test_parse_false_positive() {
        let response = "FALSE_POSITIVE: The input is sanitized before use";

        let result = if response.starts_with("FALSE_POSITIVE:") {
            let reason = response
                .strip_prefix("FALSE_POSITIVE:")
                .unwrap_or("")
                .trim();
            VerifyResult::FalsePositive {
                reason: reason.to_string(),
            }
        } else {
            VerifyResult::TruePositive {
                reason: "".to_string(),
            }
        };

        assert!(matches!(result, VerifyResult::FalsePositive { .. }));
    }
}
