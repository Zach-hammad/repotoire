//! Fix proposal generation from findings
//!
//! Uses LLM to generate code fixes based on analysis findings.

use crate::ai::prompts::{FixPromptBuilder, FixType, PromptTemplate};
use crate::ai::{AiClient, AiError, AiResult, Message};
use crate::models::{Finding, Severity};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

/// Confidence level of the fix
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FixConfidence {
    High,
    Medium,
    Low,
}

/// A single code change within a fix
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeChange {
    pub file_path: PathBuf,
    pub original_code: String,
    pub fixed_code: String,
    pub start_line: u32,
    pub end_line: u32,
    pub description: String,
}

/// Evidence supporting a fix
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Evidence {
    pub similar_patterns: Vec<String>,
    pub documentation_refs: Vec<String>,
    pub best_practices: Vec<String>,
}

/// A proposed fix for a finding
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FixProposal {
    pub id: String,
    pub finding_id: String,
    pub fix_type: FixType,
    pub confidence: FixConfidence,
    pub title: String,
    pub description: String,
    pub rationale: String,
    pub changes: Vec<CodeChange>,
    pub evidence: Evidence,
    pub syntax_valid: bool,
}

impl FixProposal {
    /// Generate a unified diff for the changes
    pub fn diff(&self, _repo_path: &Path) -> String {
        let mut diff = String::new();

        for change in &self.changes {
            // Simple line-based diff
            diff.push_str(&format!("--- a/{}\n", change.file_path.display()));
            diff.push_str(&format!("+++ b/{}\n", change.file_path.display()));
            diff.push_str(&format!(
                "@@ -{},{} +{},{} @@\n",
                change.start_line,
                change.end_line - change.start_line + 1,
                change.start_line,
                change.fixed_code.lines().count()
            ));

            // Show removed lines
            for line in change.original_code.lines() {
                diff.push_str(&format!("-{}\n", line));
            }

            // Show added lines
            for line in change.fixed_code.lines() {
                diff.push_str(&format!("+{}\n", line));
            }

            diff.push('\n');
        }

        diff
    }

    /// Apply the fix to files
    pub fn apply(&self, repo_path: &Path) -> AiResult<()> {
        for change in &self.changes {
            let file_path = repo_path.join(&change.file_path);

            let content = fs::read_to_string(&file_path)?;
            // Use replacen(1) to only replace first occurrence (#6)
            let new_content = content.replacen(&change.original_code, &change.fixed_code, 1);

            if new_content == content {
                return Err(AiError::ParseError(format!(
                    "Original code not found in {}",
                    change.file_path.display()
                )));
            }

            fs::write(&file_path, new_content)?;
        }

        Ok(())
    }
}

/// Generator for AI-powered code fixes
pub struct FixGenerator {
    client: AiClient,
}

impl FixGenerator {
    pub fn new(client: AiClient) -> Self {
        Self { client }
    }

    /// Generate a fix for a finding
    pub async fn generate_fix(&self, finding: &Finding, repo_path: &Path) -> AiResult<FixProposal> {
        // Determine language from file extension
        let language = finding
            .affected_files
            .first()
            .and_then(|p| p.extension())
            .and_then(|e| e.to_str())
            .map(extension_to_language)
            .unwrap_or("python");

        // Determine fix type from finding
        let fix_type = determine_fix_type(finding);

        // Read the affected code section
        let code_section = self.read_code_section(finding, repo_path)?;

        // Build the prompt
        let prompt = FixPromptBuilder::new(finding.clone(), fix_type, language)
            .code_section(&code_section)
            .build();

        // Get system prompt for language
        let system_prompt = PromptTemplate::system_prompt(language);

        // Call LLM
        let response = self
            .client
            .generate(vec![Message::user(prompt)], Some(system_prompt))
            .await?;

        // Parse response
        let mut fix = self.parse_response(&response, finding, fix_type)?;

        // Validate syntax
        fix.syntax_valid = self.validate_syntax(&fix, language);

        // Validate original code exists
        if !self.validate_original_code(&fix, repo_path) {
            fix.confidence = FixConfidence::Low;
        }

        Ok(fix)
    }

    /// Generate a fix with retry on validation failure
    pub async fn generate_fix_with_retry(
        &self,
        finding: &Finding,
        repo_path: &Path,
        max_retries: u32,
    ) -> AiResult<FixProposal> {
        let mut last_errors: Vec<String> = Vec::new();

        for attempt in 0..=max_retries {
            let language = finding
                .affected_files
                .first()
                .and_then(|p| p.extension())
                .and_then(|e| e.to_str())
                .map(extension_to_language)
                .unwrap_or("python");

            let fix_type = determine_fix_type(finding);
            let code_section = self.read_code_section(finding, repo_path)?;

            let mut builder = FixPromptBuilder::new(finding.clone(), fix_type, language)
                .code_section(&code_section);

            if attempt > 0 && !last_errors.is_empty() {
                builder = builder.previous_errors(last_errors.clone());
            }

            let prompt = builder.build();
            let system_prompt = PromptTemplate::system_prompt(language);

            let response = self
                .client
                .generate(vec![Message::user(prompt)], Some(system_prompt))
                .await?;

            let mut fix = self.parse_response(&response, finding, fix_type)?;
            fix.syntax_valid = self.validate_syntax(&fix, language);

            // Check validation
            let mut errors = Vec::new();

            if !fix.syntax_valid {
                errors.push("SyntaxError: generated code has syntax errors".to_string());
            }

            if !self.validate_original_code(&fix, repo_path) {
                errors.push("MatchError: Original code not found in file".to_string());
            }

            if errors.is_empty() {
                return Ok(fix);
            }

            last_errors = errors;
        }

        // Return last attempt even if invalid
        self.generate_fix(finding, repo_path).await
    }

    fn read_code_section(&self, finding: &Finding, repo_path: &Path) -> AiResult<String> {
        let file_path = finding
            .affected_files
            .first()
            .ok_or_else(|| AiError::ParseError("No affected files".to_string()))?;

        let full_path = repo_path.join(file_path);
        let content = fs::read_to_string(&full_path)?;
        let lines: Vec<&str> = content.lines().collect();

        // Extract relevant section with context
        let start = finding.line_start.unwrap_or(1).saturating_sub(10) as usize;
        let end = finding
            .line_end
            .or(finding.line_start)
            .unwrap_or(1)
            .saturating_add(20) as usize;

        let start = start.min(lines.len());
        let end = end.min(lines.len());

        Ok(lines[start..end].join("\n"))
    }

    fn parse_response(
        &self,
        response: &str,
        finding: &Finding,
        fix_type: FixType,
    ) -> AiResult<FixProposal> {
        // Extract JSON from response (may be wrapped in markdown)
        // (?s) enables dot-matches-newline for multiline JSON responses (#38)
        let json_regex = Regex::new(r"(?s)```json\s*(\{.*?\})\s*```").unwrap();
        let json_str = json_regex
            .captures(response)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str())
            .unwrap_or(response);

        let data: serde_json::Value = serde_json::from_str(json_str)
            .map_err(|e| AiError::ParseError(format!("Failed to parse JSON response: {}", e)))?;

        // Extract changes
        let changes: Vec<CodeChange> = data
            .get("changes")
            .and_then(|c| c.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|change| {
                        Some(CodeChange {
                            file_path: PathBuf::from(change.get("file_path")?.as_str()?),
                            original_code: change.get("original_code")?.as_str()?.to_string(),
                            fixed_code: change.get("fixed_code")?.as_str()?.to_string(),
                            start_line: change.get("start_line")?.as_u64()? as u32,
                            end_line: change.get("end_line")?.as_u64()? as u32,
                            description: change
                                .get("description")
                                .and_then(|d| d.as_str())
                                .unwrap_or("")
                                .to_string(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

        // Extract evidence
        let evidence = data
            .get("evidence")
            .map(|e| Evidence {
                similar_patterns: extract_string_array(e, "similar_patterns"),
                documentation_refs: extract_string_array(e, "documentation_refs"),
                best_practices: extract_string_array(e, "best_practices"),
            })
            .unwrap_or_default();

        // Calculate confidence
        let confidence = calculate_confidence(&data, finding, &changes);

        // Generate fix ID
        let fix_id = format!(
            "{:x}",
            md5::compute(format!(
                "{}:{}:{}",
                finding.id,
                finding.line_start.unwrap_or(0),
                chrono::Utc::now().timestamp()
            ))
        )[..12]
            .to_string();

        Ok(FixProposal {
            id: fix_id,
            finding_id: finding.id.clone(),
            fix_type,
            confidence,
            title: data
                .get("title")
                .and_then(|t| t.as_str())
                .unwrap_or("Auto-generated fix")
                .to_string(),
            description: data
                .get("description")
                .and_then(|d| d.as_str())
                .unwrap_or("")
                .to_string(),
            rationale: data
                .get("rationale")
                .and_then(|r| r.as_str())
                .unwrap_or("")
                .to_string(),
            changes,
            evidence,
            syntax_valid: false, // Set by validate_syntax
        })
    }

    fn validate_syntax(&self, fix: &FixProposal, language: &str) -> bool {
        for change in &fix.changes {
            // Basic syntax validation - just check for obvious issues
            let code = &change.fixed_code;

            match language {
                "python" => {
                    // Check for incomplete function definitions
                    if code.contains("def ") && !code.contains(':') {
                        return false;
                    }
                    // Check for unbalanced parentheses
                    if code.matches('(').count() != code.matches(')').count() {
                        return false;
                    }
                    // Check for unbalanced brackets
                    if code.matches('[').count() != code.matches(']').count() {
                        return false;
                    }
                }
                "javascript" | "typescript" => {
                    // Check for unbalanced braces
                    if code.matches('{').count() != code.matches('}').count() {
                        return false;
                    }
                }
                "rust" | "go" | "java" => {
                    // Check for unbalanced braces
                    if code.matches('{').count() != code.matches('}').count() {
                        return false;
                    }
                }
                _ => {}
            }
        }

        true
    }

    fn validate_original_code(&self, fix: &FixProposal, repo_path: &Path) -> bool {
        for change in &fix.changes {
            let file_path = repo_path.join(&change.file_path);
            if let Ok(content) = fs::read_to_string(&file_path) {
                // Try exact match first
                if content.contains(&change.original_code) {
                    continue;
                }

                // Try normalized match (ignore leading/trailing whitespace on lines)
                let normalized_original: String = change
                    .original_code
                    .lines()
                    .map(|l| l.trim())
                    .filter(|l| !l.is_empty())
                    .collect::<Vec<_>>()
                    .join("\n");

                let normalized_content: String = content
                    .lines()
                    .map(|l| l.trim())
                    .filter(|l| !l.is_empty())
                    .collect::<Vec<_>>()
                    .join("\n");

                if !normalized_content.contains(&normalized_original) {
                    return false;
                }
            } else {
                return false;
            }
        }

        true
    }
}

fn extract_string_array(value: &serde_json::Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

fn determine_fix_type(finding: &Finding) -> FixType {
    let title = finding.title.to_lowercase();
    let description = finding.description.to_lowercase();

    // Security issues
    if finding.severity == Severity::Critical || title.contains("security") {
        return FixType::Security;
    }

    // Complexity issues
    if title.contains("complex") || description.contains("cyclomatic") {
        return FixType::Simplify;
    }

    // Dead code
    if title.contains("unused") || title.contains("dead code") {
        return FixType::Remove;
    }

    // Documentation
    if title.contains("docstring") || title.contains("documentation") {
        return FixType::Documentation;
    }

    // Type hints
    if title.contains("type") && description.contains("hint") {
        return FixType::TypeHint;
    }

    // Long methods
    if title.contains("long") || title.contains("too many") {
        return FixType::Extract;
    }

    FixType::Refactor
}

fn calculate_confidence(
    data: &serde_json::Value,
    finding: &Finding,
    changes: &[CodeChange],
) -> FixConfidence {
    let mut score = 0.5;

    // Boost if changes are small
    if changes.len() == 1 {
        score += 0.1;
    }

    // Boost if rationale is detailed
    if let Some(rationale) = data.get("rationale").and_then(|r| r.as_str()) {
        if rationale.len() > 100 {
            score += 0.1;
        }
    }

    // Reduce for critical findings (need careful review)
    if finding.severity == Severity::Critical {
        score -= 0.2;
    }

    // Boost for having evidence
    if let Some(evidence) = data.get("evidence") {
        if evidence
            .get("best_practices")
            .and_then(|b| b.as_array())
            .map(|a| !a.is_empty())
            .unwrap_or(false)
        {
            score += 0.1;
        }
    }

    if score >= 0.9 {
        FixConfidence::High
    } else if score >= 0.7 {
        FixConfidence::Medium
    } else {
        FixConfidence::Low
    }
}

fn extension_to_language(ext: &str) -> &'static str {
    match ext {
        "py" => "python",
        "js" => "javascript",
        "ts" | "tsx" => "typescript",
        "rs" => "rust",
        "go" => "go",
        "java" => "java",
        _ => "python",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_determine_fix_type() {
        let mut finding = Finding {
            id: "test".to_string(),
            detector: "test".to_string(),
            severity: Severity::Medium,
            title: "High cyclomatic complexity".to_string(),
            description: "Function has high complexity".to_string(),
            affected_files: vec![],
            line_start: None,
            line_end: None,
            suggested_fix: None,
            estimated_effort: None,
            category: None,
            cwe_id: None,
            why_it_matters: None,
            ..Default::default()
        };

        assert_eq!(determine_fix_type(&finding), FixType::Simplify);

        finding.title = "Unused variable".to_string();
        assert_eq!(determine_fix_type(&finding), FixType::Remove);

        finding.title = "Missing docstring".to_string();
        assert_eq!(determine_fix_type(&finding), FixType::Documentation);

        finding.severity = Severity::Critical;
        finding.title = "SQL injection vulnerability".to_string();
        assert_eq!(determine_fix_type(&finding), FixType::Security);
    }

    #[test]
    fn test_extension_to_language() {
        assert_eq!(extension_to_language("py"), "python");
        assert_eq!(extension_to_language("js"), "javascript");
        assert_eq!(extension_to_language("ts"), "typescript");
        assert_eq!(extension_to_language("rs"), "rust");
        assert_eq!(extension_to_language("go"), "go");
    }
}
