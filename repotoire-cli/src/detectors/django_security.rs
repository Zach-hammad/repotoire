//! Django Security Detector
//!
//! Graph-enhanced detection of Django security issues.
//! Uses graph to:
//! - Identify which views are affected by csrf_exempt
//! - Check if raw SQL is in exposed endpoints
//! - Trace authentication/authorization coverage

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static CSRF_EXEMPT: OnceLock<Regex> = OnceLock::new();
static DEBUG_TRUE: OnceLock<Regex> = OnceLock::new();
static RAW_SQL: OnceLock<Regex> = OnceLock::new();
static SECRET_KEY: OnceLock<Regex> = OnceLock::new();
static ALLOWED_HOSTS: OnceLock<Regex> = OnceLock::new();

fn csrf_exempt() -> &'static Regex {
    CSRF_EXEMPT.get_or_init(|| Regex::new(r"@csrf_exempt|csrf_exempt\(").unwrap())
}

fn debug_true() -> &'static Regex {
    DEBUG_TRUE.get_or_init(|| Regex::new(r"DEBUG\s*=\s*True").unwrap())
}

fn raw_sql() -> &'static Regex {
    RAW_SQL.get_or_init(|| Regex::new(r"\.raw\(|\.extra\(|RawSQL\(|cursor\.execute").unwrap())
}

fn secret_key() -> &'static Regex {
    SECRET_KEY.get_or_init(|| Regex::new(r#"SECRET_KEY\s*=\s*['"][^'"]{10,}['"]"#).unwrap())
}

fn allowed_hosts() -> &'static Regex {
    ALLOWED_HOSTS.get_or_init(|| Regex::new(r#"ALLOWED_HOSTS\s*=\s*\[\s*['"][*]['"]"#).unwrap())
}

pub struct DjangoSecurityDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DjangoSecurityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find containing function/view
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize, bool)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name);
                let name_lower = f.name.to_lowercase();

                // Check if this is a view function
                let is_view = name_lower.contains("view")
                    || name_lower.starts_with("get")
                    || name_lower.starts_with("post")
                    || name_lower.starts_with("put")
                    || name_lower.starts_with("delete")
                    || name_lower.starts_with("patch")
                    || name_lower.contains("api")
                    || name_lower.contains("handler");

                (f.name, callers.len(), is_view)
            })
    }

    /// Check if function has authentication decorators
    fn has_auth_decorator(lines: &[&str], func_line: usize) -> bool {
        // Look backwards for decorators
        let start = func_line.saturating_sub(5);
        let context = lines[start..func_line].join(" ").to_lowercase();

        context.contains("@login_required")
            || context.contains("@permission_required")
            || context.contains("@user_passes_test")
            || context.contains("@staff_member_required")
            || context.contains("@authentication_classes")
            || context.contains("@permission_classes")
    }
}

impl Detector for DjangoSecurityDetector {
    fn name(&self) -> &'static str {
        "django-security"
    }
    fn description(&self) -> &'static str {
        "Detects Django security issues"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let path_str = path.to_string_lossy().to_string();

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");

                for (i, line) in lines.iter().enumerate() {
                    let line_num = (i + 1) as u32;

                    // Check CSRF exemption
                    if csrf_exempt().is_match(line) {
                        let func_context =
                            Self::find_containing_function(graph, &path_str, line_num);
                        let has_auth = Self::has_auth_decorator(&lines, i);

                        // Severity based on context
                        let severity = if has_auth {
                            Severity::Medium // At least has auth
                        } else if func_context
                            .as_ref()
                            .map(|(_, _, is_view)| *is_view)
                            .unwrap_or(false)
                        {
                            Severity::Critical // View without CSRF or auth
                        } else {
                            Severity::High
                        };

                        let mut notes = Vec::new();
                        if let Some((func_name, callers, is_view)) = &func_context {
                            notes.push(format!(
                                "📦 Function: `{}` ({} callers)",
                                func_name, callers
                            ));
                            if *is_view {
                                notes.push("🌐 Appears to be a view function".to_string());
                            }
                        }
                        if has_auth {
                            notes.push("✅ Has authentication decorator".to_string());
                        } else {
                            notes.push("❌ No authentication decorator found".to_string());
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity,
                            title: "CSRF protection disabled".to_string(),
                            description: format!(
                                "@csrf_exempt removes CSRF protection from this view.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Options:\n\
                                 1. Remove @csrf_exempt and use CSRF tokens properly\n\
                                 2. If this is an API endpoint, use DRF authentication:\n\
                                    ```python\n\
                                    @api_view(['POST'])\n\
                                    @authentication_classes([TokenAuthentication])\n\
                                    def my_view(request):\n\
                                        ...\n\
                                    ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("20 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-352".to_string()),
                            why_it_matters: Some(
                                "CSRF exemption allows attackers to trick users into performing \
                                 unintended actions through malicious websites or links."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }

                    // Check DEBUG setting
                    if debug_true().is_match(line)
                        && fname.contains("settings")
                        && !fname.contains("dev")
                        && !fname.contains("local")
                        && !crate::detectors::base::is_test_path(fname)
                    {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity: Severity::Critical,
                            title: "DEBUG = True in settings".to_string(),
                            description: format!(
                                "Debug mode is enabled in `{}`.\n\n\
                                     **Impact:**\n\
                                     • Stack traces exposed to users\n\
                                     • Configuration details leaked\n\
                                     • Database queries visible\n\
                                     • Template variables exposed",
                                fname
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Use environment variables:\n\
                                     ```python\n\
                                     DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'\n\
                                     ```\n\
                                     Or use django-environ:\n\
                                     ```python\n\
                                     import environ\n\
                                     env = environ.Env(DEBUG=(bool, False))\n\
                                     DEBUG = env('DEBUG')\n\
                                     ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-215".to_string()),
                            why_it_matters: Some(
                                "Debug mode leaks sensitive information to attackers.".to_string(),
                            ),
                            ..Default::default()
                        });
                    }

                    // Check hardcoded SECRET_KEY
                    if secret_key().is_match(line)
                        && !line.contains("os.environ")
                        && !line.contains("env(")
                        && fname.contains("settings")
                        && !fname.contains("dev")
                        && !fname.contains("local")
                    {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity: Severity::Critical,
                            title: "Hardcoded SECRET_KEY".to_string(),
                            description: "SECRET_KEY is hardcoded in settings file.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Move to environment variable:\n\
                                     ```python\n\
                                     SECRET_KEY = os.environ['SECRET_KEY']\n\
                                     ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-798".to_string()),
                            why_it_matters: Some(
                                "Leaked SECRET_KEY allows session hijacking and data tampering."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }

                    // Check ALLOWED_HOSTS wildcard
                    if allowed_hosts().is_match(line)
                        && fname.contains("settings")
                        && !fname.contains("dev")
                        && !fname.contains("local")
                    {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity: Severity::High,
                            title: "ALLOWED_HOSTS allows all hosts".to_string(),
                            description: "ALLOWED_HOSTS = ['*'] allows any host.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some("Specify allowed hosts explicitly.".to_string()),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-16".to_string()),
                            why_it_matters: Some("Allows HTTP Host header attacks.".to_string()),
                            ..Default::default()
                        });
                    }

                    // Check raw SQL
                    if raw_sql().is_match(line) {
                        let func_context =
                            Self::find_containing_function(graph, &path_str, line_num);

                        // Check for user input
                        let has_user_input = line.contains("request.")
                            || line.contains("f\"")
                            || line.contains("f'")
                            || line.contains("+ ")
                            || line.contains(".format(");

                        let severity = if has_user_input {
                            Severity::Critical
                        } else if func_context
                            .as_ref()
                            .map(|(_, _, is_view)| *is_view)
                            .unwrap_or(false)
                        {
                            Severity::High // In a view = exposed
                        } else {
                            Severity::Medium
                        };

                        let mut notes = Vec::new();
                        if has_user_input {
                            notes.push(
                                "⚠️ String interpolation detected - possible SQL injection"
                                    .to_string(),
                            );
                        }
                        if let Some((func_name, callers, is_view)) = &func_context {
                            notes.push(format!(
                                "📦 In function: `{}` ({} callers)",
                                func_name, callers
                            ));
                            if *is_view {
                                notes.push("🌐 In view function (exposed)".to_string());
                            }
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity,
                            title: "Raw SQL usage".to_string(),
                            description: format!(
                                "Raw SQL bypasses Django's ORM protections.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Use Django ORM methods or parameterized queries:\n\
                                 ```python\n\
                                 # Instead of:\n\
                                 cursor.execute(f\"SELECT * FROM users WHERE id = {user_id}\")\n\
                                 \n\
                                 # Use:\n\
                                 User.objects.filter(id=user_id)\n\
                                 # Or:\n\
                                 cursor.execute(\"SELECT * FROM users WHERE id = %s\", [user_id])\n\
                                 ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-89".to_string()),
                            why_it_matters: Some(
                                "Raw SQL with user input can lead to SQL injection.".to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "DjangoSecurityDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
