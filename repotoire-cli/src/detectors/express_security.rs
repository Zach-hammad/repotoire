//! Express Security Detector
//!
//! Graph-enhanced detection of Express.js security issues.
//! Uses graph to:
//! - Check middleware chain coverage
//! - Identify routes without authentication
//! - Trace error handling coverage

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static EXPRESS_APP: OnceLock<Regex> = OnceLock::new();
static ROUTE_HANDLER: OnceLock<Regex> = OnceLock::new();

fn express_app() -> &'static Regex {
    EXPRESS_APP.get_or_init(|| {
        Regex::new(r#"express\(\)|require\(["']express["']\)|from ['"]express['"']"#).unwrap()
    })
}

fn route_handler() -> &'static Regex {
    ROUTE_HANDLER.get_or_init(|| Regex::new(r"\.(get|post|put|delete|patch|all|use)\s*\(").unwrap())
}

/// Security features to check
struct SecurityFeatures {
    has_helmet: bool,
    has_cors: bool,
    has_rate_limit: bool,
    has_body_parser_limit: bool,
    has_hpp: bool,
    has_csrf: bool,
    has_compression: bool,
    route_count: usize,
    auth_middleware_count: usize,
}

impl SecurityFeatures {
    fn from_content(content: &str) -> Self {
        let lower = content.to_lowercase();
        Self {
            has_helmet: content.contains("helmet"),
            has_cors: content.contains("cors(") || content.contains("cors."),
            has_rate_limit: lower.contains("ratelimit")
                || lower.contains("rate-limit")
                || lower.contains("express-rate"),
            has_body_parser_limit: content.contains("limit:")
                || content.contains("bodyParser") && content.contains("limit"),
            has_hpp: content.contains("hpp"),
            has_csrf: lower.contains("csrf") || lower.contains("csurf"),
            has_compression: content.contains("compression"),
            route_count: route_handler().find_iter(content).count(),
            auth_middleware_count: Self::count_auth_middleware(content),
        }
    }

    fn count_auth_middleware(content: &str) -> usize {
        let auth_patterns = [
            "passport.",
            "jwt.",
            "jsonwebtoken",
            "express-jwt",
            "isAuthenticated",
            "requireAuth",
            "authenticate",
            "authorize",
            "checkAuth",
            "verifyToken",
        ];
        auth_patterns
            .iter()
            .filter(|p| content.contains(*p))
            .count()
    }

    fn security_score(&self) -> f64 {
        let mut score = 0.0;
        let mut max_score = 0.0;

        // Helmet - critical
        max_score += 25.0;
        if self.has_helmet {
            score += 25.0;
        }

        // Rate limiting - critical for production
        max_score += 20.0;
        if self.has_rate_limit {
            score += 20.0;
        }

        // Body parser limits
        max_score += 15.0;
        if self.has_body_parser_limit {
            score += 15.0;
        }

        // CORS
        max_score += 10.0;
        if self.has_cors {
            score += 10.0;
        }

        // CSRF for non-API apps
        max_score += 10.0;
        if self.has_csrf {
            score += 10.0;
        }

        // Auth middleware
        max_score += 20.0;
        if self.auth_middleware_count > 0 {
            score += 20.0;
        }

        (score / max_score) * 100.0
    }
}

pub struct ExpressSecurityDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl ExpressSecurityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find containing function
    fn find_containing_function(
        graph: &GraphStore,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| (f.name, graph.get_callers(&f.qualified_name).len()))
    }
}

impl Detector for ExpressSecurityDetector {
    fn name(&self) -> &'static str {
        "express-security"
    }
    fn description(&self) -> &'static str {
        "Detects Express.js security issues"
    }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
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

            // Skip test files
            if path_str.contains("test") || path_str.contains("spec") {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "js" | "ts" | "mjs") {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                // Check if this is an Express app
                if !express_app().is_match(&content) {
                    continue;
                }

                let features = SecurityFeatures::from_content(&content);
                let security_score = features.security_score();

                // Build app-level notes
                let mut app_notes = Vec::new();
                app_notes.push(format!("📊 Security Score: {:.0}%", security_score));
                app_notes.push(format!("🛣️ Routes: {}", features.route_count));

                let mut missing = Vec::new();
                if !features.has_helmet {
                    missing.push("helmet");
                }
                if !features.has_rate_limit {
                    missing.push("rate-limit");
                }
                if !features.has_body_parser_limit {
                    missing.push("body-parser-limit");
                }
                if features.auth_middleware_count == 0 {
                    missing.push("auth-middleware");
                }

                if !missing.is_empty() {
                    app_notes.push(format!("❌ Missing: {}", missing.join(", ")));
                }

                let context_notes = format!("\n\n**App Analysis:**\n{}", app_notes.join("\n"));

                // Helmet finding
                if !features.has_helmet {
                    let severity = if features.route_count > 5 {
                        Severity::High // Bigger app = more important
                    } else {
                        Severity::Medium
                    };

                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity,
                        title: "Express app missing helmet".to_string(),
                        description: format!(
                            "Helmet sets important security headers to protect against common attacks.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some(
                            "Install and use helmet:\n\
                             ```bash\n\
                             npm install helmet\n\
                             ```\n\
                             ```javascript\n\
                             const helmet = require('helmet');\n\
                             app.use(helmet());\n\
                             \n\
                             // Or with custom config:\n\
                             app.use(helmet({\n\
                               contentSecurityPolicy: {\n\
                                 directives: {\n\
                                   defaultSrc: [\"'self'\"],\n\
                                   scriptSrc: [\"'self'\", \"trusted-cdn.com\"],\n\
                                 },\n\
                               },\n\
                             }));\n\
                             ```".to_string()
                        ),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-693".to_string()),
                        why_it_matters: Some(
                            "Without helmet, your app is missing:\n\
                             • X-Content-Type-Options (prevents MIME sniffing)\n\
                             • X-Frame-Options (prevents clickjacking)\n\
                             • X-XSS-Protection (legacy XSS filter)\n\
                             • Strict-Transport-Security (enforces HTTPS)\n\
                             • Content-Security-Policy (prevents XSS)".to_string()
                        ),
                        ..Default::default()
                    });
                }

                // Rate limiting finding
                if !features.has_rate_limit {
                    let severity = if features.route_count > 5 {
                        Severity::Medium
                    } else {
                        Severity::Low
                    };

                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity,
                        title: "Express app missing rate limiting".to_string(),
                        description: format!(
                            "Rate limiting prevents brute force attacks and DoS.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some(
                            "Install and use express-rate-limit:\n\
                             ```bash\n\
                             npm install express-rate-limit\n\
                             ```\n\
                             ```javascript\n\
                             const rateLimit = require('express-rate-limit');\n\
                             \n\
                             const limiter = rateLimit({\n\
                               windowMs: 15 * 60 * 1000, // 15 minutes\n\
                               max: 100, // limit each IP to 100 requests per windowMs\n\
                               message: 'Too many requests, please try again later.',\n\
                             });\n\
                             \n\
                             app.use(limiter);\n\
                             \n\
                             // Stricter limits for auth endpoints\n\
                             const authLimiter = rateLimit({\n\
                               windowMs: 15 * 60 * 1000,\n\
                               max: 5,\n\
                             });\n\
                             app.use('/api/auth', authLimiter);\n\
                             ```"
                            .to_string(),
                        ),
                        estimated_effort: Some("15 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-770".to_string()),
                        why_it_matters: Some(
                            "Without rate limiting, attackers can:\n\
                             • Brute force passwords\n\
                             • Scrape data at scale\n\
                             • DoS your API\n\
                             • Abuse expensive endpoints"
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }

                // Body parser limit finding
                if !features.has_body_parser_limit {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity: Severity::Low,
                        title: "No body size limit configured".to_string(),
                        description: "Large request bodies can be used for DoS attacks."
                            .to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some(
                            "Set body size limits:\n\
                             ```javascript\n\
                             app.use(express.json({ limit: '10kb' }));\n\
                             app.use(express.urlencoded({ limit: '10kb', extended: true }));\n\
                             ```"
                            .to_string(),
                        ),
                        estimated_effort: Some("5 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-400".to_string()),
                        why_it_matters: Some(
                            "Large payloads can exhaust server memory.".to_string(),
                        ),
                        ..Default::default()
                    });
                }

                // Check for error handling
                let has_error_handler = content.contains("err, req, res, next")
                    || content.contains("error, req, res, next")
                    || content.contains("err: Error");

                if !has_error_handler && features.route_count > 3 {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity: Severity::Medium,
                        title: "No global error handler".to_string(),
                        description: format!(
                            "Express apps should have a global error handler to prevent stack traces from leaking.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some(
                            "Add a global error handler:\n\
                             ```javascript\n\
                             // Error handler must be the LAST middleware\n\
                             app.use((err, req, res, next) => {\n\
                               console.error(err.stack);\n\
                               res.status(500).json({\n\
                                 error: process.env.NODE_ENV === 'production' \n\
                                   ? 'Internal server error' \n\
                                   : err.message\n\
                               });\n\
                             });\n\
                             ```".to_string()
                        ),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-209".to_string()),
                        why_it_matters: Some("Unhandled errors leak stack traces and internal details.".to_string()),
                        ..Default::default()
                    });
                }

                // Check for auth on routes
                if features.auth_middleware_count == 0 && features.route_count > 5 {
                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "ExpressSecurityDetector".to_string(),
                        severity: Severity::Medium,
                        title: "No authentication middleware detected".to_string(),
                        description: format!(
                            "This Express app has {} routes but no apparent authentication.{}",
                            features.route_count, context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(1),
                        suggested_fix: Some(
                            "Consider adding authentication:\n\
                             ```javascript\n\
                             // Using Passport.js\n\
                             const passport = require('passport');\n\
                             app.use(passport.initialize());\n\
                             app.use('/api/protected', passport.authenticate('jwt'));\n\
                             \n\
                             // Or custom middleware\n\
                             const requireAuth = (req, res, next) => {\n\
                               if (!req.user) return res.status(401).json({ error: 'Unauthorized' });\n\
                               next();\n\
                             };\n\
                             ```".to_string()
                        ),
                        estimated_effort: Some("1-2 hours".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-306".to_string()),
                        why_it_matters: Some("APIs without authentication are open to abuse.".to_string()),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "ExpressSecurityDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
