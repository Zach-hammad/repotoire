//! Unsafe template detector for XSS and template injection vulnerabilities
//!
//! Detects dangerous template patterns that can lead to XSS:
//!
//! - Jinja2 Environment() without autoescape=True
//! - render_template_string() with variables
//! - Markup() with untrusted input
//! - React dangerouslySetInnerHTML
//! - Vue v-html directive
//! - innerHTML = assignments
//! - document.write()
//!
//! CWE-79: Cross-site Scripting (XSS)
//! CWE-1336: Server-Side Template Injection

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};
use uuid::Uuid;

/// Default file patterns to exclude
const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "tests/",
    "test_",
    "_test.py",
    "migrations/",
    "__pycache__/",
    ".git/",
    "node_modules/",
    "venv/",
    ".venv/",
    "dist/",
    "build/",
    ".min.js",
    ".bundle.js",
];

/// Detects XSS and template injection vulnerabilities
pub struct UnsafeTemplateDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_patterns: Vec<String>,
    // Python patterns
    jinja2_env_pattern: Regex,
    autoescape_true_pattern: Regex,
    render_template_string_pattern: Regex,
    markup_pattern: Regex,
    // JavaScript patterns
    dangerous_inner_html_pattern: Regex,
    vue_vhtml_pattern: Regex,
    innerhtml_assign_pattern: Regex,
    outerhtml_assign_pattern: Regex,
    document_write_pattern: Regex,
}

impl UnsafeTemplateDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self::with_config(DetectorConfig::new(), PathBuf::from("."))
    }

    /// Create with custom repository path
    pub fn with_repository_path(repository_path: PathBuf) -> Self {
        Self::with_config(DetectorConfig::new(), repository_path)
    }

    /// Create with custom config and repository path
    pub fn with_config(config: DetectorConfig, repository_path: PathBuf) -> Self {
        let max_findings = config.get_option_or("max_findings", 100);
        let exclude_patterns = config
            .get_option::<Vec<String>>("exclude_patterns")
            .unwrap_or_else(|| {
                DEFAULT_EXCLUDE_PATTERNS
                    .iter()
                    .map(|s| s.to_string())
                    .collect()
            });

        // Compile Python patterns
        let jinja2_env_pattern = Regex::new(r"\bEnvironment\s*\([^)]*\)").unwrap();
        let autoescape_true_pattern = Regex::new(
            r"(?i)autoescape\s*=\s*(?:True|select_autoescape\s*\()"
        ).unwrap();
        // Simplified: detect render_template_string calls with any content
        // (filtering for variable usage happens in scan logic)
        let render_template_string_pattern = Regex::new(
            r#"\brender_template_string\s*\([^)]+\)"#
        ).unwrap();
        // Simplified: detect Markup calls with any content
        let markup_pattern = Regex::new(
            r#"\bMarkup\s*\([^)]+\)"#
        ).unwrap();

        // Compile JavaScript patterns
        let dangerous_inner_html_pattern = Regex::new(
            r"\bdangerouslySetInnerHTML\s*=\s*\{"
        ).unwrap();
        let vue_vhtml_pattern = Regex::new(
            r#"\bv-html\s*=\s*["'][^"']+["']"#
        ).unwrap();
        let innerhtml_assign_pattern = Regex::new(
            r"\.\s*innerHTML\s*=\s*[^;]+"
        ).unwrap();
        let outerhtml_assign_pattern = Regex::new(
            r"\.\s*outerHTML\s*=\s*[^;]+"
        ).unwrap();
        let document_write_pattern = Regex::new(
            r"\bdocument\s*\.\s*write(?:ln)?\s*\("
        ).unwrap();

        Self {
            config,
            repository_path,
            max_findings,
            exclude_patterns,
            jinja2_env_pattern,
            autoescape_true_pattern,
            render_template_string_pattern,
            markup_pattern,
            dangerous_inner_html_pattern,
            vue_vhtml_pattern,
            innerhtml_assign_pattern,
            outerhtml_assign_pattern,
            document_write_pattern,
        }
    }

    /// Check if a function call contains only a string literal (safe)
    fn is_string_literal_only(&self, call_match: &str) -> bool {
        // Pattern: function_name("string") or function_name('string')
        // If it matches this pattern, it's safe (static string)
        let safe_pattern = Regex::new(r#"^\w+\s*\(\s*["'][^"']*["']\s*\)$"#).unwrap();
        safe_pattern.is_match(call_match.trim())
    }

    /// Check if path should be excluded
    fn should_exclude(&self, path: &str) -> bool {
        for pattern in &self.exclude_patterns {
            if pattern.ends_with('/') {
                let dir = pattern.trim_end_matches('/');
                if path.split('/').any(|p| p == dir) {
                    return true;
                }
            } else if pattern.contains('*') {
                let pattern = pattern.replace('*', ".*");
                if let Ok(re) = Regex::new(&format!("^{}$", pattern)) {
                    let filename = Path::new(path)
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("");
                    if re.is_match(path) || re.is_match(filename) {
                        return true;
                    }
                }
            } else if path.contains(pattern) {
                return true;
            }
        }
        false
    }

    /// Scan Python files for template vulnerabilities
    fn scan_python_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;
        
        let mut findings = Vec::new();

        // Walk through Python files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(&self.repository_path, Some(&["py"])) {
            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            if self.should_exclude(&rel_path) {
                continue;
            }

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;
                let stripped = line.trim();

                if stripped.starts_with('#') {
                    continue;
                }
                
                // Check for suppression comments
                let prev_line = if line_no > 0 { Some(lines[line_no - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                // Check for Jinja2 Environment without autoescape
                if let Some(env_match) = self.jinja2_env_pattern.find(line) {
                    let env_code = env_match.as_str();
                    if !self.autoescape_true_pattern.is_match(env_code) {
                        findings.push(self.create_finding(
                            &rel_path,
                            line_num,
                            "jinja2_no_autoescape",
                            stripped,
                        ));
                    }
                }

                // Check for render_template_string with variable (skip string-only calls)
                if let Some(m) = self.render_template_string_pattern.find(line) {
                    if !self.is_string_literal_only(m.as_str()) {
                        findings.push(self.create_finding(
                            &rel_path,
                            line_num,
                            "render_template_string",
                            stripped,
                        ));
                    }
                }

                // Check for Markup with variable (skip string-only calls)
                if let Some(m) = self.markup_pattern.find(line) {
                    if !self.is_string_literal_only(m.as_str()) {
                        findings.push(self.create_finding(
                            &rel_path,
                            line_num,
                            "markup_unsafe",
                            stripped,
                        ));
                    }
                }

                if findings.len() >= self.max_findings {
                    return findings;
                }
            }
        }

        findings
    }

    /// Scan JavaScript/TypeScript files for XSS vulnerabilities
    fn scan_javascript_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;
        
        let mut findings = Vec::new();

        // Walk through JS/TS files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(&self.repository_path, Some(&["js", "jsx", "ts", "tsx"])) {
            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            if self.should_exclude(&rel_path) {
                continue;
            }

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;
                let stripped = line.trim();

                if stripped.starts_with("//") || stripped.starts_with("/*") {
                    continue;
                }
                
                // Check for suppression comments
                let prev_line = if line_no > 0 { Some(lines[line_no - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                // Check for dangerouslySetInnerHTML (React)
                if self.dangerous_inner_html_pattern.is_match(line) {
                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        "dangerously_set_inner_html",
                        stripped,
                    ));
                }

                // Check for innerHTML assignment
                if self.innerhtml_assign_pattern.is_match(line) {
                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        "innerhtml_assignment",
                        stripped,
                    ));
                }

                // Check for outerHTML assignment
                if self.outerhtml_assign_pattern.is_match(line) {
                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        "outerhtml_assignment",
                        stripped,
                    ));
                }

                // Check for document.write
                if self.document_write_pattern.is_match(line) {
                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        "document_write",
                        stripped,
                    ));
                }

                if findings.len() >= self.max_findings {
                    return findings;
                }
            }
        }

        findings
    }

    /// Scan Vue files for v-html directive
    fn scan_vue_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;
        
        let mut findings = Vec::new();

        // Walk through Vue files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(&self.repository_path, Some(&["vue"])) {
            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            if self.should_exclude(&rel_path) {
                continue;
            }

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;
                
                // Check for suppression comments
                let prev_line = if line_no > 0 { Some(lines[line_no - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                if self.vue_vhtml_pattern.is_match(line) {
                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        "vue_vhtml",
                        line.trim(),
                    ));
                }

                if findings.len() >= self.max_findings {
                    return findings;
                }
            }
        }

        findings
    }

    /// Create a finding for detected template vulnerability
    fn create_finding(
        &self,
        file_path: &str,
        line_start: u32,
        pattern_type: &str,
        snippet: &str,
    ) -> Finding {
        let (title_desc, desc, cwe) = match pattern_type {
            "jinja2_no_autoescape" => (
                "Jinja2 Environment without autoescape",
                "Jinja2 Environment() created without autoescape=True, allowing XSS attacks",
                "CWE-79",
            ),
            "render_template_string" => (
                "Unsafe render_template_string",
                "render_template_string() with variable input can lead to template injection",
                "CWE-1336",
            ),
            "markup_unsafe" => (
                "Unsafe Markup usage",
                "Markup() with variable input bypasses escaping, enabling XSS",
                "CWE-79",
            ),
            "dangerously_set_inner_html" => (
                "React dangerouslySetInnerHTML",
                "dangerouslySetInnerHTML can introduce XSS vulnerabilities",
                "CWE-79",
            ),
            "vue_vhtml" => (
                "Vue v-html directive",
                "v-html directive bypasses Vue's XSS protection",
                "CWE-79",
            ),
            "innerhtml_assignment" => (
                "innerHTML assignment",
                "Direct innerHTML assignment can lead to XSS vulnerabilities",
                "CWE-79",
            ),
            "outerhtml_assignment" => (
                "outerHTML assignment",
                "Direct outerHTML assignment can lead to XSS vulnerabilities",
                "CWE-79",
            ),
            "document_write" => (
                "document.write usage",
                "document.write() can introduce XSS vulnerabilities",
                "CWE-79",
            ),
            _ => (
                "Unsafe template pattern",
                "Potentially unsafe template handling detected",
                "CWE-79",
            ),
        };

        let title = format!("XSS: {}", title_desc);

        let description = format!(
            "**{}**\n\n\
             **Location**: {}:{}\n\n\
             **Code snippet**:\n```\n{}\n```\n\n\
             Cross-Site Scripting (XSS) vulnerabilities occur when untrusted data is included\n\
             in web pages without proper validation or escaping. Attackers can inject malicious\n\
             scripts that:\n\
             - Steal user session cookies\n\
             - Capture keystrokes and credentials\n\
             - Redirect users to malicious sites\n\
             - Deface the application\n\n\
             This vulnerability is classified as **{}: Improper Neutralization of\n\
             Input During Web Page Generation ('Cross-site Scripting')**.",
            desc, file_path, line_start,
            &snippet[..snippet.len().min(100)],
            cwe
        );

        let suggested_fix = self.get_recommendation(pattern_type);

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "UnsafeTemplateDetector".to_string(),
            severity: Severity::High,
            title,
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start: Some(line_start),
            line_end: Some(line_start),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Medium (1-4 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some(cwe.to_string()),
            why_it_matters: Some(
                "XSS vulnerabilities allow attackers to execute scripts in users' browsers, \
                 potentially stealing sensitive data or hijacking user sessions."
                    .to_string(),
            ),
            ..Default::default()
        }
    }

    /// Get remediation recommendation for pattern type
    fn get_recommendation(&self, pattern_type: &str) -> String {
        match pattern_type {
            "jinja2_no_autoescape" => {
                "**Recommended fixes**:\n\n\
                 1. **Enable autoescape globally** (preferred):\n\
                    ```python\n\
                    from jinja2 import Environment, select_autoescape\n\n\
                    env = Environment(\n\
                        autoescape=select_autoescape(['html', 'htm', 'xml'])\n\
                    )\n\
                    ```\n\n\
                 2. **Use Flask's default environment** (autoescape enabled by default):\n\
                    ```python\n\
                    from flask import render_template\n\
                    return render_template('template.html', data=user_data)\n\
                    ```".to_string()
            }
            "render_template_string" => {
                "**Recommended fixes**:\n\n\
                 1. **Use file-based templates** instead of string templates:\n\
                    ```python\n\
                    # Instead of:\n\
                    return render_template_string(user_template)\n\n\
                    # Use:\n\
                    return render_template('user_template.html', data=user_data)\n\
                    ```\n\n\
                 2. **If string templates are required**, validate and sanitize:\n\
                    ```python\n\
                    from markupsafe import escape\n\
                    safe_data = escape(user_data)\n\
                    ```".to_string()
            }
            "markup_unsafe" => {
                "**Recommended fixes**:\n\n\
                 1. **Avoid Markup() with untrusted input**:\n\
                    ```python\n\
                    # Instead of:\n\
                    return Markup(user_data)\n\n\
                    # Use:\n\
                    from markupsafe import escape\n\
                    return escape(user_data)\n\
                    ```\n\n\
                 2. **Only use Markup() for trusted, static content**:\n\
                    ```python\n\
                    return Markup('<strong>') + escape(user_data) + Markup('</strong>')\n\
                    ```".to_string()
            }
            "dangerously_set_inner_html" => {
                "**Recommended fixes**:\n\n\
                 1. **Avoid dangerouslySetInnerHTML when possible**:\n\
                    ```jsx\n\
                    // Instead of:\n\
                    <div dangerouslySetInnerHTML={{__html: userContent}} />\n\n\
                    // Use React's built-in escaping:\n\
                    <div>{userContent}</div>\n\
                    ```\n\n\
                 2. **If HTML rendering is required**, sanitize first:\n\
                    ```jsx\n\
                    import DOMPurify from 'dompurify';\n\n\
                    <div dangerouslySetInnerHTML={{__html: DOMPurify.sanitize(userContent)}} />\n\
                    ```".to_string()
            }
            "vue_vhtml" => {
                "**Recommended fixes**:\n\n\
                 1. **Avoid v-html with user content**:\n\
                    ```vue\n\
                    <!-- Instead of: -->\n\
                    <div v-html=\"userContent\"></div>\n\n\
                    <!-- Use text interpolation: -->\n\
                    <div>{{ userContent }}</div>\n\
                    ```\n\n\
                 2. **If HTML rendering is required**, sanitize first:\n\
                    ```vue\n\
                    import DOMPurify from 'dompurify';\n\n\
                    computed: {\n\
                      safeContent() {\n\
                        return DOMPurify.sanitize(this.userContent);\n\
                      }\n\
                    }\n\
                    <div v-html=\"safeContent\"></div>\n\
                    ```".to_string()
            }
            "innerhtml_assignment" | "outerhtml_assignment" => {
                "**Recommended fixes**:\n\n\
                 1. **Use textContent for text** (auto-escapes):\n\
                    ```javascript\n\
                    // Instead of:\n\
                    element.innerHTML = userInput;\n\n\
                    // Use:\n\
                    element.textContent = userInput;\n\
                    ```\n\n\
                 2. **Use DOM APIs for structure**:\n\
                    ```javascript\n\
                    const span = document.createElement('span');\n\
                    span.textContent = userInput;\n\
                    element.appendChild(span);\n\
                    ```\n\n\
                 3. **If HTML is required**, sanitize first:\n\
                    ```javascript\n\
                    import DOMPurify from 'dompurify';\n\
                    element.innerHTML = DOMPurify.sanitize(userInput);\n\
                    ```".to_string()
            }
            "document_write" => {
                "**Recommended fixes**:\n\n\
                 1. **Avoid document.write entirely** (deprecated):\n\
                    ```javascript\n\
                    // Instead of:\n\
                    document.write('<div>' + userInput + '</div>');\n\n\
                    // Use DOM APIs:\n\
                    const div = document.createElement('div');\n\
                    div.textContent = userInput;\n\
                    document.body.appendChild(div);\n\
                    ```\n\n\
                 2. **For dynamic script loading**, use createElement:\n\
                    ```javascript\n\
                    const script = document.createElement('script');\n\
                    script.src = trustedScriptUrl;\n\
                    document.head.appendChild(script);\n\
                    ```".to_string()
            }
            _ => {
                "**Recommended fixes**:\n\n\
                 1. Avoid using raw HTML/template injection patterns\n\
                 2. Use framework-provided escaping mechanisms\n\
                 3. Sanitize user input with a library like DOMPurify\n\
                 4. Apply Content Security Policy (CSP) headers".to_string()
            }
        }
    }
}

impl Default for UnsafeTemplateDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for UnsafeTemplateDetector {
    fn name(&self) -> &'static str {
        "UnsafeTemplateDetector"
    }

    fn description(&self) -> &'static str {
        "Detects XSS and template injection vulnerabilities (Jinja2, React, Vue, innerHTML)"
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting unsafe template detection");

        let mut findings = Vec::new();

        if self.repository_path.exists() {
            // Scan Python files
            findings.extend(self.scan_python_files());

            if findings.len() < self.max_findings {
                // Scan JavaScript/TypeScript files
                findings.extend(self.scan_javascript_files());
            }

            if findings.len() < self.max_findings {
                // Scan Vue files
                findings.extend(self.scan_vue_files());
            }
        }

        // Truncate to max_findings
        findings.truncate(self.max_findings);

        info!("UnsafeTemplateDetector found {} potential vulnerabilities", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_jinja2_detection() {
        let detector = UnsafeTemplateDetector::new();

        // Should detect Environment without autoescape
        assert!(detector.jinja2_env_pattern.is_match("env = Environment(loader=FileSystemLoader())"));
        
        // Should detect autoescape=True
        assert!(detector.autoescape_true_pattern.is_match("autoescape=True"));
        assert!(detector.autoescape_true_pattern.is_match("autoescape=select_autoescape()"));
    }

    #[test]
    fn test_react_detection() {
        let detector = UnsafeTemplateDetector::new();

        // Should detect dangerouslySetInnerHTML
        assert!(detector.dangerous_inner_html_pattern.is_match(
            r#"<div dangerouslySetInnerHTML={{__html: content}} />"#
        ));
    }

    #[test]
    fn test_vue_detection() {
        let detector = UnsafeTemplateDetector::new();

        // Should detect v-html
        assert!(detector.vue_vhtml_pattern.is_match(r#"<div v-html="userContent"></div>"#));
    }

    #[test]
    fn test_innerhtml_detection() {
        let detector = UnsafeTemplateDetector::new();

        // Should detect innerHTML assignment
        assert!(detector.innerhtml_assign_pattern.is_match("element.innerHTML = userInput;"));
        
        // Should detect outerHTML assignment
        assert!(detector.outerhtml_assign_pattern.is_match("element.outerHTML = userInput;"));
    }

    #[test]
    fn test_document_write_detection() {
        let detector = UnsafeTemplateDetector::new();

        assert!(detector.document_write_pattern.is_match("document.write('<div>' + content + '</div>')"));
        assert!(detector.document_write_pattern.is_match("document.writeln(html)"));
    }
}